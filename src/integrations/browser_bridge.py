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


browser_bridge = BrowserBridge()
