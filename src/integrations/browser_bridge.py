# -*- coding: utf-8 -*-
"""
Browser Bridge — подключение к существующему Chrome через CDP (Playwright).

Не запускает отдельный браузер; требует, чтобы Chrome был запущен с
--remote-debugging-port=9222 (через "new Enable Chrome Remote Debugging.command").
"""

from __future__ import annotations

import asyncio
import base64
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


class BrowserBridge:
    """Async-клиент для управления Chrome через Chrome DevTools Protocol."""

    CDP_URL = "http://localhost:9222"

    def __init__(self) -> None:
        self._playwright = None
        self._browser = None
        self._lock = asyncio.Lock()

    async def _get_browser(self):
        """Возвращает подключённый browser-объект, переподключается при необходимости."""
        if self._browser is not None:
            try:
                # Простая проверка живости — список страниц не должен падать
                self._browser.contexts  # noqa: B018
                return self._browser
            except Exception:
                self._browser = None

        from playwright.async_api import async_playwright

        if self._playwright is None:
            self._playwright = await async_playwright().start()

        try:
            self._browser = await self._playwright.chromium.connect_over_cdp(self.CDP_URL)
            logger.info("browser_bridge_connected", cdp_url=self.CDP_URL)
        except Exception as exc:
            logger.warning("browser_bridge_connect_failed", error=str(exc))
            self._browser = None
            raise

        return self._browser

    async def is_attached(self) -> bool:
        """True если Chrome доступен по CDP."""
        try:
            await self._get_browser()
            return True
        except Exception:
            return False

    async def _active_page(self):
        """Возвращает активную (первую) страницу из первого контекста."""
        browser = await self._get_browser()
        contexts = browser.contexts
        if not contexts:
            ctx = await browser.new_context()
            return await ctx.new_page()
        pages = contexts[0].pages
        if not pages:
            return await contexts[0].new_page()
        return pages[-1]

    async def list_tabs(self) -> list[dict]:
        """Возвращает список вкладок: [{title, url, id}]."""
        try:
            browser = await self._get_browser()
            result = []
            for ctx in browser.contexts:
                for i, page in enumerate(ctx.pages):
                    result.append({"title": page.title() or "", "url": page.url, "id": i})
            return result
        except Exception as exc:
            logger.warning("browser_list_tabs_failed", error=str(exc))
            return []

    async def navigate(self, url: str) -> str:
        """Navigates active tab to url. Returns final URL."""
        page = await self._active_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=15_000)
        return page.url

    async def screenshot(self) -> bytes | None:
        """Returns PNG screenshot bytes of current page."""
        try:
            page = await self._active_page()
            return await page.screenshot(type="png")
        except Exception as exc:
            logger.warning("browser_screenshot_failed", error=str(exc))
            return None

    async def get_page_text(self) -> str:
        """Returns innerText of current page, trimmed to 4000 chars."""
        try:
            page = await self._active_page()
            text = await page.inner_text("body")
            return text[:4000]
        except Exception as exc:
            logger.warning("browser_get_text_failed", error=str(exc))
            return ""

    async def execute_js(self, code: str) -> Any:
        """Executes JavaScript in current page context, returns result."""
        page = await self._active_page()
        return await page.evaluate(code)

    async def new_tab(self, url: str) -> str:
        """Opens a new tab and navigates to url. Returns final URL."""
        browser = await self._get_browser()
        contexts = browser.contexts
        if not contexts:
            ctx = await browser.new_context()
        else:
            ctx = contexts[0]
        page = await ctx.new_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=15_000)
        return page.url

    async def screenshot_base64(self) -> str | None:
        """Returns base64-encoded PNG screenshot."""
        data = await self.screenshot()
        if data is None:
            return None
        return base64.b64encode(data).decode()

    async def inject_text(self, selector: str, text: str, *, clear_first: bool = True) -> bool:
        """
        Вставляет текст в элемент по CSS-селектору.

        Пробует fill() → затем JS-inject как fallback.
        Возвращает True при успехе.
        """
        try:
            page = await self._active_page()
            locator = page.locator(selector).first
            await locator.wait_for(state="visible", timeout=8_000)
            if clear_first:
                await locator.clear()
            await locator.fill(text)
            return True
        except Exception as exc:
            logger.warning("browser_inject_text_failed", selector=selector, error=str(exc))
            # Fallback: JS clipboard-style inject для contenteditable
            try:
                page = await self._active_page()
                await page.evaluate(
                    """([sel, txt]) => {
                        const el = document.querySelector(sel);
                        if (!el) return false;
                        el.focus();
                        el.textContent = txt;
                        el.dispatchEvent(new Event('input', {bubbles: true}));
                        return true;
                    }""",
                    [selector, text],
                )
                return True
            except Exception:
                return False

    async def click_element(self, selector: str, *, timeout: float = 5_000) -> bool:
        """Кликает на элемент по CSS-селектору. Возвращает True при успехе."""
        try:
            page = await self._active_page()
            locator = page.locator(selector).first
            await locator.wait_for(state="visible", timeout=timeout)
            await locator.click()
            return True
        except Exception as exc:
            logger.warning("browser_click_element_failed", selector=selector, error=str(exc))
            return False

    async def wait_for_stable_text(
        self,
        selector: str,
        *,
        stable_ms: float = 2000.0,
        poll_ms: float = 500.0,
        max_wait_ms: float = 120_000.0,
    ) -> str:
        """
        Ждёт пока текст в selector стабилизируется (не меняется stable_ms мс).

        Используется для определения конца генерации ответа AI.
        Возвращает итоговый текст.
        """
        import time
        page = await self._active_page()
        last_text = ""
        last_change_time = time.monotonic()
        start_time = time.monotonic()
        poll_sec = poll_ms / 1000.0
        stable_sec = stable_ms / 1000.0
        max_sec = max_wait_ms / 1000.0

        while True:
            elapsed = time.monotonic() - start_time
            if elapsed > max_sec:
                logger.warning("browser_wait_stable_text_timeout", selector=selector, elapsed=elapsed)
                break
            await asyncio.sleep(poll_sec)
            try:
                current_text = await page.inner_text(selector)
            except Exception:
                current_text = last_text

            if current_text != last_text:
                last_text = current_text
                last_change_time = time.monotonic()
            elif last_text and (time.monotonic() - last_change_time) >= stable_sec:
                break

        return last_text.strip()

    async def find_tab_by_url_fragment(self, fragment: str):
        """Возвращает страницу (Page), URL которой содержит fragment."""
        try:
            browser = await self._get_browser()
            for ctx in browser.contexts:
                for page in ctx.pages:
                    if fragment in page.url:
                        return page
        except Exception:
            pass
        return None

    async def get_or_open_tab(self, url: str, url_fragment: str):
        """Возвращает существующую вкладку по fragment или открывает новую с url."""
        existing = await self.find_tab_by_url_fragment(url_fragment)
        if existing is not None:
            await existing.bring_to_front()
            return existing
        browser = await self._get_browser()
        contexts = browser.contexts
        ctx = contexts[0] if contexts else await browser.new_context()
        page = await ctx.new_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=20_000)
        return page


browser_bridge = BrowserBridge()
