# -*- coding: utf-8 -*-
"""
Browser Agent v1.0 (Phase 9.2).
Управляет headless-браузером Playwright для навигации, чтения страниц и взаимодействия.
"""

import asyncio
import structlog
import os
from datetime import datetime
from typing import Optional, Dict, Any
from playwright.async_api import async_playwright, Browser, BrowserContext, Page

logger = structlog.get_logger("BrowserAgent")

class BrowserAgent:
    def __init__(self, headless: bool = True):
        self.headless = headless
        self.playwright = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self.screenshot_dir = "artifacts/screenshots"
        os.makedirs(self.screenshot_dir, exist_ok=True)

    async def start(self):
        """Запуск браузера."""
        if self.playwright:
            return

        logger.info("🌐 Starting Browser Agent...")
        try:
            self.playwright = await async_playwright().start()
            self.browser = await self.playwright.chromium.launch(
                headless=self.headless,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-blink-features=AutomationControlled",
                ]
            )
            # Эмуляция реального пользователя
            self.context = await self.browser.new_context(
                viewport={"width": 1280, "height": 720},
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
            )
            self.page = await self.context.new_page()
            logger.info("✅ Browser Agent Ready")
        except Exception as e:
            logger.error("❌ Failed to start browser", error=str(e))
            raise

    async def browse(self, url: str) -> Dict[str, Any]:
        """Открывает URL и возвращает контент."""
        if not self.page:
            await self.start()

        logger.info(f"🌍 Navigating to: {url}")
        try:
            await self.page.goto(url, wait_until="domcontentloaded", timeout=30000)
            
            # Ждем немного для подгрузки динамики
            await asyncio.sleep(2)
            
            title = await self.page.title()
            
            # Извлекаем текст (пока просто body.innerText)
            # В будущем можно использовать readability.js
            content = await self.page.evaluate("document.body.innerText")
            
            # Скриншот
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"screenshot_{timestamp}.png"
            path = os.path.join(self.screenshot_dir, filename)
            await self.page.screenshot(path=path, full_page=False)
            
            return {
                "title": title,
                "url": url,
                "content": content[:10000], # Ограничиваем длину
                "screenshot_path": path
            }
        except Exception as e:
            logger.error(f"❌ Browse error: {url}", error=str(e))
            return {"error": str(e), "url": url}

    async def stop(self):
        """Остановка браузера."""
        if self.context:
            await self.context.close()
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()
        
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None
        logger.info("🛑 Browser Agent Stopped")

    async def screenshot_only(self, url: str) -> Optional[str]:
        """Только скриншот (быстрее)."""
        res = await self.browse(url) # Пока используем общий метод
        return res.get("screenshot_path")
