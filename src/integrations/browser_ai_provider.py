# -*- coding: utf-8 -*-
"""
Browser AI Provider — взаимодействие с ChatGPT и Gemini через Chrome CDP.

Использует browser_bridge (Playwright CDP-attach) для работы с уже
открытыми вкладками, где пользователь залогинен по платной подписке.

Особенности:
- Gemini: пробует переключиться на Pro-модель, если выбрана Flash/другая.
- ChatGPT: может не работать при включённой Cloudflare-защите; ошибка
  возвращается как строка с префиксом [ERROR], не выбрасывается exception.
- Оба провайдера: wait_for_stable_text ждёт конца стриминга AI.
"""

from __future__ import annotations

import asyncio
from typing import Literal

import structlog

from .browser_bridge import browser_bridge

logger = structlog.get_logger(__name__)

Service = Literal["gemini", "chatgpt"]

# ---------------------------------------------------------------------------
# URL / fragment configs
# ---------------------------------------------------------------------------

_GEMINI_URL = "https://gemini.google.com/app"
_GEMINI_FRAGMENT = "gemini.google.com"

_CHATGPT_URL = "https://chatgpt.com/"
_CHATGPT_FRAGMENT = "chatgpt.com"

# ---------------------------------------------------------------------------
# CSS selectors
# Gemini и ChatGPT меняют DOM часто → несколько fallback-вариантов.
# ---------------------------------------------------------------------------

# Gemini input: Quill-editor (contenteditable div)
_GEMINI_INPUT_SELECTORS = [
    "rich-textarea .ql-editor",
    ".ql-editor[contenteditable='true']",
    "div[contenteditable='true'][data-placeholder]",
    "div[contenteditable='true']",
]

# Gemini submit button
_GEMINI_SUBMIT_SELECTORS = [
    "button[aria-label='Send message']",
    "button.send-button",
    "button[data-test-id='send-button']",
    "mat-icon[fonticon='send']:xpath=..",  # родительская кнопка
]

# Gemini response container (последний ответ модели)
_GEMINI_RESPONSE_SELECTORS = [
    "model-response .model-response-text",
    ".model-response-text",
    "message-content .markdown",
    ".response-content",
]

# ChatGPT input
_CHATGPT_INPUT_SELECTORS = [
    "#prompt-textarea",
    "div[data-testid='chat-input']",
    "div[contenteditable='true'][data-id]",
]

# ChatGPT submit button
_CHATGPT_SUBMIT_SELECTORS = [
    "button[data-testid='send-button']",
    "button[aria-label='Send prompt']",
    "button[aria-label='Send message']",
]

# ChatGPT response (последний ответ ассистента)
_CHATGPT_RESPONSE_SELECTORS = [
    "article[data-testid^='conversation-turn-']:last-of-type .markdown",
    "[data-message-author-role='assistant']:last-of-type .markdown",
    "[data-message-author-role='assistant'] .markdown",
]

# Ключевые слова Pro-моделей Gemini (case-insensitive)
_GEMINI_PRO_KEYWORDS = ["pro", "1.5", "2.0 pro", "ultra"]
# Flash/non-pro — если текущая модель содержит только эти слова, нужно переключить
_GEMINI_NON_PRO_KEYWORDS = ["flash", "nano", "lite"]


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

async def _try_selectors(page, selectors: list[str], timeout: float = 5_000):
    """Возвращает первый живой locator из списка селекторов или None."""
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            await loc.wait_for(state="visible", timeout=timeout)
            return loc
        except Exception:
            continue
    return None


# ---------------------------------------------------------------------------
# BrowserAIProvider
# ---------------------------------------------------------------------------

class BrowserAIProvider:
    """
    Высокоуровневый провайдер для AI через браузерные вкладки.

    Использует browser_bridge для CDP-attach к уже запущенному Chrome.
    Методы:
      chat(prompt, service) → str  (ответ или "[ERROR] ...")
      is_service_available(service) → bool
    """

    async def chat(
        self,
        prompt: str,
        service: Service = "gemini",
        *,
        ensure_pro: bool = True,
    ) -> str:
        """
        Отправляет prompt в браузерный AI-сервис и возвращает ответ.

        Args:
            prompt: текст запроса
            service: "gemini" или "chatgpt"
            ensure_pro: (только Gemini) попытаться переключиться на Pro-модель

        Returns:
            Строка с ответом AI, или "[ERROR] ..." при неудаче.
        """
        if not await browser_bridge.is_attached():
            return "[ERROR] Chrome CDP недоступен — запустите Chrome с --remote-debugging-port=9222"

        try:
            if service == "gemini":
                return await self._chat_gemini(prompt, ensure_pro=ensure_pro)
            elif service == "chatgpt":
                return await self._chat_chatgpt(prompt)
            else:
                return f"[ERROR] Неизвестный сервис: {service}"
        except Exception as exc:
            logger.warning("browser_ai_provider_error", service=service, error=str(exc))
            return f"[ERROR] {service}: {exc}"

    async def is_service_available(self, service: Service) -> bool:
        """Проверяет, есть ли открытая вкладка сервиса и доступен ли ввод."""
        if not await browser_bridge.is_attached():
            return False
        fragment = _GEMINI_FRAGMENT if service == "gemini" else _CHATGPT_FRAGMENT
        page = await browser_bridge.find_tab_by_url_fragment(fragment)
        if page is None:
            return False
        selectors = _GEMINI_INPUT_SELECTORS if service == "gemini" else _CHATGPT_INPUT_SELECTORS
        loc = await _try_selectors(page, selectors, timeout=3_000)
        return loc is not None

    # ------------------------------------------------------------------
    # Gemini
    # ------------------------------------------------------------------

    async def _chat_gemini(self, prompt: str, *, ensure_pro: bool = True) -> str:
        page = await browser_bridge.get_or_open_tab(_GEMINI_URL, _GEMINI_FRAGMENT)
        logger.info("browser_ai_gemini_tab", url=page.url)

        # Опционально переключаем на Pro-модель
        if ensure_pro:
            await self._ensure_gemini_pro(page)

        # Находим поле ввода
        input_loc = await _try_selectors(page, _GEMINI_INPUT_SELECTORS, timeout=8_000)
        if input_loc is None:
            raise RuntimeError("Не найдено поле ввода Gemini — возможно, изменился интерфейс")

        # Очищаем и вводим текст
        await input_loc.click()
        await asyncio.sleep(0.3)

        # Используем inject_text с fill() → JS fallback
        ok = await browser_bridge.inject_text(
            _GEMINI_INPUT_SELECTORS[0],
            prompt,
            clear_first=True,
        )
        if not ok:
            # Пробуем через клавиатуру как запасной путь
            await input_loc.clear()
            await input_loc.type(prompt, delay=10)

        # Отправляем: Enter или кнопка Submit
        submitted = False
        try:
            await input_loc.press("Enter")
            submitted = True
        except Exception:
            pass

        if not submitted:
            submit_loc = await _try_selectors(page, _GEMINI_SUBMIT_SELECTORS, timeout=3_000)
            if submit_loc:
                await submit_loc.click()
                submitted = True

        if not submitted:
            raise RuntimeError("Не удалось отправить сообщение в Gemini")

        await asyncio.sleep(1.0)  # даём начаться генерации

        # Ждём ответа
        response_text = ""
        for sel in _GEMINI_RESPONSE_SELECTORS:
            try:
                response_text = await browser_bridge.wait_for_stable_text(
                    sel,
                    stable_ms=2500,
                    poll_ms=600,
                    max_wait_ms=120_000,
                )
                if response_text:
                    break
            except Exception:
                continue

        if not response_text:
            # Fallback: взять весь текст страницы
            response_text = await browser_bridge.get_page_text()
            if not response_text:
                raise RuntimeError("Пустой ответ от Gemini — возможно, страница не загрузилась")

        logger.info("browser_ai_gemini_response", length=len(response_text))
        return response_text

    async def _ensure_gemini_pro(self, page) -> None:
        """
        Лучший-effort: переключает Gemini на Pro-модель, если сейчас не Pro.

        Не выбрасывает исключений — при ошибке просто логирует.
        """
        try:
            # Читаем текущую модель через JS (ищем текст в model-switcher области)
            current_model: str = await page.evaluate("""() => {
                // Попробуем найти кнопку/лейбл с именем текущей модели
                const candidates = [
                    document.querySelector('bard-sidenav-item.active .item-label'),
                    document.querySelector('[data-model-name]'),
                    document.querySelector('.model-switcher-button'),
                    document.querySelector('button[aria-label*="model" i]'),
                    document.querySelector('.model-name'),
                    document.querySelector('[class*="model-selector"] button'),
                ];
                for (const el of candidates) {
                    if (el && el.textContent.trim()) return el.textContent.trim();
                }
                return '';
            }""") or ""

            current_lower = current_model.lower()
            logger.debug("browser_ai_gemini_current_model", model=current_model)

            # Если уже Pro или неизвестно (пусто) — не трогаем
            if not current_lower:
                return
            if any(kw in current_lower for kw in _GEMINI_PRO_KEYWORDS):
                logger.debug("browser_ai_gemini_already_pro", model=current_model)
                return

            # Если Flash/Lite/Nano — пробуем переключить
            if not any(kw in current_lower for kw in _GEMINI_NON_PRO_KEYWORDS):
                return  # Неизвестная модель — не трогаем

            logger.info("browser_ai_gemini_switching_to_pro", from_model=current_model)

            # Кликаем на переключатель моделей
            clicked = await page.evaluate("""() => {
                const candidates = [
                    document.querySelector('bard-sidenav-item .item-label'),
                    document.querySelector('.model-switcher-button'),
                    document.querySelector('button[aria-label*="model" i]'),
                    document.querySelector('[class*="model-selector"]'),
                ];
                for (const el of candidates) {
                    if (el) { el.click(); return true; }
                }
                return false;
            }""")

            if not clicked:
                return

            await asyncio.sleep(0.5)

            # Ищем и кликаем на Pro-опцию в открывшемся меню
            switched = await page.evaluate("""() => {
                const proKeywords = ['pro', '1.5 pro', '2.0 pro', 'advanced'];
                const items = document.querySelectorAll(
                    'mat-option, [role="option"], [role="menuitem"], .model-option'
                );
                for (const item of items) {
                    const text = item.textContent.toLowerCase();
                    if (proKeywords.some(kw => text.includes(kw))) {
                        item.click();
                        return text.trim();
                    }
                }
                return '';
            }""")

            if switched:
                logger.info("browser_ai_gemini_switched_to_pro", selected=switched)
                await asyncio.sleep(0.8)
            else:
                # Закрываем дропдаун кликом вне
                await page.keyboard.press("Escape")

        except Exception as exc:
            logger.warning("browser_ai_gemini_pro_switch_failed", error=str(exc))

    # ------------------------------------------------------------------
    # ChatGPT
    # ------------------------------------------------------------------

    async def _chat_chatgpt(self, prompt: str) -> str:
        page = await browser_bridge.get_or_open_tab(_CHATGPT_URL, _CHATGPT_FRAGMENT)
        logger.info("browser_ai_chatgpt_tab", url=page.url)

        # Проверка на Cloudflare / captcha
        page_title = await page.title()
        if "just a moment" in page_title.lower() or "cloudflare" in page_title.lower():
            raise RuntimeError("ChatGPT заблокирован Cloudflare — откройте страницу вручную")

        # Находим поле ввода
        input_loc = await _try_selectors(page, _CHATGPT_INPUT_SELECTORS, timeout=8_000)
        if input_loc is None:
            raise RuntimeError("Не найдено поле ввода ChatGPT — возможно, изменился интерфейс")

        # Вводим текст
        ok = await browser_bridge.inject_text(
            _CHATGPT_INPUT_SELECTORS[0],
            prompt,
            clear_first=True,
        )
        if not ok:
            await input_loc.clear()
            await input_loc.type(prompt, delay=15)

        # Отправляем
        submitted = False
        try:
            await input_loc.press("Enter")
            submitted = True
        except Exception:
            pass

        if not submitted:
            submit_loc = await _try_selectors(page, _CHATGPT_SUBMIT_SELECTORS, timeout=3_000)
            if submit_loc:
                await submit_loc.click()
                submitted = True

        if not submitted:
            raise RuntimeError("Не удалось отправить сообщение в ChatGPT")

        await asyncio.sleep(1.5)

        # Ждём ответа
        response_text = ""
        for sel in _CHATGPT_RESPONSE_SELECTORS:
            try:
                response_text = await browser_bridge.wait_for_stable_text(
                    sel,
                    stable_ms=3000,
                    poll_ms=700,
                    max_wait_ms=180_000,
                )
                if response_text:
                    break
            except Exception:
                continue

        if not response_text:
            response_text = await browser_bridge.get_page_text()
            if not response_text:
                raise RuntimeError("Пустой ответ от ChatGPT")

        logger.info("browser_ai_chatgpt_response", length=len(response_text))
        return response_text


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

browser_ai_provider = BrowserAIProvider()
