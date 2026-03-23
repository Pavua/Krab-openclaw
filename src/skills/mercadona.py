# -*- coding: utf-8 -*-
"""
Mercadona Skill — поиск товаров и цен на актуальном web-flow Mercadona.

Сейчас сайт больше не даёт надёжно входить через старый URL вида
`/search?query=...`, поэтому основной сценарий такой:
- открыть домашнюю страницу;
- принять cookies и закрыть стартовую модалку;
- ввести запрос в штатное поле поиска;
- дождаться `search-results` и извлечь карточки товаров.

Если внутренний API Mercadona всё же отдаёт JSON, мы по-прежнему умеем его
собирать. Но основным truth-path считается именно живой пользовательский flow.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Антибот: JS-патч, скрывающий следы автоматизации Playwright/CDP
# ---------------------------------------------------------------------------
_STEALTH_SCRIPT = """
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
    Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
    Object.defineProperty(navigator, 'languages', { get: () => ['es-ES', 'es', 'en'] });
    window.chrome = { runtime: {} };
    const originalQuery = window.navigator.permissions.query;
    window.navigator.permissions.query = (parameters) =>
        parameters.name === 'notifications'
            ? Promise.resolve({ state: Notification.permission })
            : originalQuery(parameters);
"""

_MERCADONA_BASE = "https://tienda.mercadona.es"
_HOME_URL = f"{_MERCADONA_BASE}/"
_SEARCH_RESULTS_URL_RE = re.compile(r"/search-results(?:\?|$)")

# Паттерны URL API Mercadona, которые содержат данные о товарах
_API_PATTERNS = (
    "/api/search",
    "/api/categories",
    "/api/products",
)


async def search_mercadona(query: str, max_results: int = 10) -> str:
    """
    Ищет товары на Mercadona через перехват JSON-ответов их API.

    Алгоритм:
    1. Запускает headless Chromium с антибот-патчем.
    2. Навигирует на домашнюю страницу и закрывает обязательные оверлеи.
    3. Выполняет реальный пользовательский поиск через searchbox.
    4. Если есть JSON API-ответы, берёт их; иначе читает DOM карточек.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return "❌ playwright не установлен. Запусти: pip install playwright && playwright install chromium"

    collected_products: list[dict[str, Any]] = []
    api_data_received = asyncio.Event()

    async def handle_response(response: Any) -> None:
        """Перехватчик ответов — собирает JSON из API Mercadona."""
        url = response.url
        if not any(pattern in url for pattern in _API_PATTERNS):
            return
        # Принимаем только успешные JSON-ответы
        if response.status < 200 or response.status >= 300:
            return
        content_type = response.headers.get("content-type", "")
        if "json" not in content_type:
            return
        try:
            data = await response.json()
            _extract_products(data, collected_products)
            if collected_products:
                api_data_received.set()
        except Exception as exc:
            logger.debug("mercadona_response_parse_error", url=url, error=repr(exc))

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-web-security",
                "--lang=es-ES",
            ],
        )
        context = await browser.new_context(
            locale="es-ES",
            timezone_id="Europe/Madrid",
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
        )

        # Патчим navigator.webdriver и прочие маркеры автоматизации
        await context.add_init_script(_STEALTH_SCRIPT)

        page = await context.new_page()

        # Регистрируем обработчик ответов ДО навигации
        page.on("response", lambda resp: asyncio.ensure_future(handle_response(resp)))

        logger.info("mercadona_navigating", url=_HOME_URL, query=query)

        try:
            await page.goto(_HOME_URL, wait_until="domcontentloaded", timeout=20_000)
        except Exception as exc:
            logger.warning("mercadona_goto_timeout", error=repr(exc))
        await page.wait_for_timeout(1_500)
        await _accept_cookies_if_present(page)
        await _dismiss_entry_modal(page)
        if not await _submit_search_query(page, query):
            await page.close()
            await context.close()
            await browser.close()
            return f"🛒 Не удалось открыть поиск Mercadona для запроса «{query}»."

        # Даём сайту время на XHR/SPA-навигацию.
        try:
            await page.wait_for_url("**/search-results**", timeout=10_000)
        except Exception:
            pass
        try:
            await asyncio.wait_for(api_data_received.wait(), timeout=4.0)
        except asyncio.TimeoutError:
            logger.warning("mercadona_api_timeout", query=query)

        # Если JSON не пришёл — берём уже отрисованный DOM search-results.
        if not collected_products and _SEARCH_RESULTS_URL_RE.search(page.url):
            collected_products = await _extract_products_from_dom(page)

        # Последний резерв: прямой API fetch из браузерного контекста.
        if not collected_products:
            logger.info("mercadona_fallback_direct_api", query=query)
            collected_products = await _direct_api_search(page, query)

        await page.close()
        await context.close()
        await browser.close()

    if not collected_products:
        return f"🛒 Товары по запросу «{query}» не найдены на Mercadona."

    return _format_results(query, collected_products[:max_results])


async def _accept_cookies_if_present(page: Any) -> None:
    """Аккуратно принимает cookies, если баннер ещё виден."""
    for label in ("Aceptar", "Aceptar todas", "Aceptar cookies"):
        try:
            button = page.get_by_text(label, exact=True)
            if await button.count():
                await button.first.click(timeout=2_000)
                await page.wait_for_timeout(500)
                logger.info("mercadona_cookies_accepted", label=label)
                return
        except Exception:
            continue


async def _dismiss_entry_modal(page: Any) -> None:
    """
    Закрывает стартовую модалку поверх поиска.

    На текущем Mercadona поверх `searchbox` висит mask с postal/login-оверлеем.
    Самый стабильный путь в headless-режиме — отправить `Escape`, а затем
    при необходимости попробовать кликнуть по click-outside mask.
    """
    await _handle_postal_code_gate(page)

    try:
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(400)
    except Exception:
        pass

    try:
        mask = page.locator('[data-testid="mask"]').first
        if await mask.count():
            await mask.click(timeout=1_500)
            await page.wait_for_timeout(400)
    except Exception:
        pass


async def _handle_postal_code_gate(page: Any) -> None:
    """
    Пытается пройти postal-code gate штатным способом.

    Mercadona теперь завязывает каталог и индекс поиска на выбранный центр,
    поэтому сначала даём сайту шанс открыть поиск «по правилам». Если modal
    отсутствует или не проходит, ниже остаётся мягкий fallback через `Escape`.
    """
    postal_input = page.locator('input[name="postalCode"]').first
    postal_button = page.locator('[data-testid="postal-code-checker-button"]').first
    try:
        if not await postal_input.count():
            return
        postal_code = _resolve_postal_code()
        await postal_input.fill(postal_code, timeout=2_000)
        if await postal_button.count():
            await postal_button.click(timeout=2_000)
            await page.wait_for_timeout(800)
            logger.info("mercadona_postal_gate_submitted", postal_code=postal_code)
    except Exception as exc:
        logger.debug("mercadona_postal_gate_failed", error=repr(exc))


def _resolve_postal_code() -> str:
    """
    Возвращает почтовый индекс для Mercadona.

    По умолчанию используем `28001` как стабильный испанский центр для headless
    smoke. При необходимости пользователь может переопределить это через
    `MERCADONA_POSTAL_CODE`.
    """
    value = (os.environ.get("MERCADONA_POSTAL_CODE") or "28001").strip()
    return value or "28001"


async def _submit_search_query(page: Any, query: str) -> bool:
    """Ищет штатное поле поиска, вводит запрос и запускает поиск."""
    candidates = (
        page.get_by_role("searchbox"),
        page.get_by_placeholder("Buscar productos"),
        page.locator('input[name="search"]'),
        page.locator('input[placeholder*="Buscar"]'),
    )
    target = None
    for candidate in candidates:
        try:
            if await candidate.count():
                target = candidate.first
                break
        except Exception:
            continue

    if target is None:
        logger.warning("mercadona_searchbox_missing", query=query)
        return False

    try:
        await target.fill(query, timeout=3_000)
        await target.press("Enter")
        logger.info("mercadona_query_submitted", query=query)
        return True
    except Exception as exc:
        logger.warning("mercadona_search_submit_failed", query=query, error=repr(exc))
        return False


async def _extract_products_from_dom(page: Any) -> list[dict[str, Any]]:
    """Читает карточки товаров со страницы `search-results`, если API-маршрут скрыт от нас."""
    try:
        await page.wait_for_selector("button.product-cell__content-link", timeout=8_000)
    except Exception as exc:
        logger.warning("mercadona_dom_results_missing", error=repr(exc), url=page.url)
        return []

    cards = await page.evaluate(
        """() => {
            const items = [];
            for (const el of document.querySelectorAll('button.product-cell__content-link')) {
                items.push({
                    text: (el.innerText || '').trim().replace(/\\s+/g, ' '),
                    aria: el.getAttribute('aria-label') || '',
                });
                if (items.length >= 40) break;
            }
            return items;
        }"""
    )

    products: list[dict[str, Any]] = []
    for card in cards:
        product = _normalize_dom_product_card(card)
        if product:
            products.append(product)
    return products


def _normalize_dom_product_card(card: Any) -> dict[str, Any] | None:
    """Нормализует карточку товара из DOM search-results в общий формат."""
    if not isinstance(card, dict):
        return None

    aria = str(card.get("aria") or "").strip()
    text = str(card.get("text") or "").strip()

    name = ""
    detail = ""
    price = ""
    unit = ""

    if aria:
        aria_match = re.match(
            r"^(?P<name>.+?),\s*(?P<detail>.+?),\s*(?P<price>\d+,\d+)€\s+por\s+(?P<unit>.+)$",
            aria,
            flags=re.IGNORECASE,
        )
        if aria_match:
            name = aria_match.group("name").strip()
            detail = aria_match.group("detail").strip()
            price = aria_match.group("price").strip()
            unit = aria_match.group("unit").strip().lower()

    if not name and text:
        price_match = re.search(r"(\d+,\d+)\s*€", text)
        if price_match:
            price = price or price_match.group(1)
            name = text[:price_match.start()].strip()
            rest = text[price_match.end():].strip()
            unit_match = re.search(r"/\s*([^\s]+)", rest)
            if unit_match:
                unit = unit or unit_match.group(1).strip().lower()
        else:
            name = text

    if not name:
        return None

    rendered_name = name if not detail else f"{name} — {detail}"
    return {
        "name": rendered_name.strip(),
        "price": price.strip(),
        "unit": unit.strip(),
        "reference_price": "",
        "reference_unit": "",
        "thumbnail": "",
    }


def _extract_products(data: Any, out: list[dict[str, Any]]) -> None:
    """Извлекает товары из различных форматов ответа Mercadona API."""
    if isinstance(data, list):
        for item in data:
            _extract_products(item, out)
        return

    if not isinstance(data, dict):
        return

    # Формат /api/search: {"results": [...]}
    if "results" in data:
        for item in data["results"]:
            product = _normalize_product(item)
            if product:
                out.append(product)
        return

    # Формат /api/products: массив напрямую или {"products": [...]}
    if "products" in data:
        for item in data["products"]:
            product = _normalize_product(item)
            if product:
                out.append(product)
        return

    # Может быть одиночный продукт
    product = _normalize_product(data)
    if product:
        out.append(product)


def _normalize_product(item: Any) -> dict[str, Any] | None:
    """Нормализует один товар из API в унифицированный словарь."""
    if not isinstance(item, dict):
        return None

    name = (
        item.get("display_name")
        or item.get("name")
        or item.get("title")
        or ""
    )
    if not name:
        return None

    # Цена: price_instructions / price / bulk_price
    price_info = item.get("price_instructions") or {}
    price = (
        price_info.get("unit_price")
        or price_info.get("bulk_price")
        or item.get("price")
        or item.get("unit_price")
        or ""
    )
    unit = price_info.get("unit_name") or item.get("unit_size") or ""
    reference_price = price_info.get("reference_price") or ""
    reference_unit = price_info.get("reference_format") or ""

    thumbnail = (item.get("thumbnail") or "").strip()

    return {
        "name": str(name).strip(),
        "price": str(price).strip(),
        "unit": str(unit).strip(),
        "reference_price": str(reference_price).strip(),
        "reference_unit": str(reference_unit).strip(),
        "thumbnail": thumbnail,
    }


async def _direct_api_search(page: Any, query: str) -> list[dict[str, Any]]:
    """
    Резервный метод: прямой запрос к API Mercadona через page.evaluate().
    Работает из контекста браузера, минуя CORS.
    """
    try:
        result = await page.evaluate(
            """async (q) => {
                try {
                    const resp = await fetch(
                        `https://tienda.mercadona.es/api/search/?query=${encodeURIComponent(q)}&lang=es&limit=24`,
                        { headers: { 'Accept': 'application/json, text/plain, */*' } }
                    );
                    if (!resp.ok) return null;
                    return await resp.json();
                } catch(e) {
                    return null;
                }
            }""",
            query,
        )
        if result:
            products: list[dict[str, Any]] = []
            _extract_products(result, products)
            return products
    except Exception as exc:
        logger.debug("mercadona_direct_api_failed", error=repr(exc))
    return []


def _format_results(query: str, products: list[dict[str, Any]]) -> str:
    """Форматирует список товаров для вывода в Telegram."""
    lines = [f"🛒 **Mercadona** — результаты по «{query}»:\n"]
    for i, p in enumerate(products, 1):
        price_str = f"{p['price']} €" if p["price"] else "цена неизвестна"
        if p["unit"]:
            price_str += f" / {p['unit']}"
        ref = ""
        if p["reference_price"] and p["reference_unit"]:
            ref = f" _(~{p['reference_price']} €/{p['reference_unit']})_"
        lines.append(f"{i}. **{p['name']}** — {price_str}{ref}")
    return "\n".join(lines)
