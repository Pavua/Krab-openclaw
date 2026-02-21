# -*- coding: utf-8 -*-
"""
Screen Awareness Module (Phase 11).
Позволяет Крабу "видеть" экран пользователя, делать скриншоты и анализировать их через Gemini.
"""

import asyncio
import os
import structlog
from datetime import datetime
from PIL import Image
import mss
import io

logger = structlog.get_logger("ScreenCatcher")

class ScreenCatcher:
    def __init__(self, perceptor):
        self.perceptor = perceptor
        self.tmp_dir = "temp/screens"
        os.makedirs(self.tmp_dir, exist_ok=True)

    def capture_screen(self) -> str:
        """Делает скриншот основного монитора и сохраняет во временный файл."""
        try:
           with mss.mss() as sct:
               # Capture the first monitor
               monitor = sct.monitors[1]
               sct_img = sct.grab(monitor)
               
               # Convert to PIL Image
               img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
               
               timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
               filename = f"{self.tmp_dir}/screen_{timestamp}.jpg"
               
               # Сохраняем с компрессией (чтобы быстрее отправлять)
               img.save(filename, "JPEG", quality=85)
               logger.info(f"📸 Screenshot captured: {filename}")
               return filename
        except Exception as e:
            logger.error(f"Screenshot failed: {e}")
            return None

    async def analyze_screen(self, query: str = "Что на экране?") -> str:
        """
        Захватывает экран и отправляет в Gemini через Perceptor.
        """
        path = self.capture_screen()
        if not path:
            return "❌ Не удалось сделать скриншот."

        try:
            # Загружаем файл как GenerativeAI File
            vision_response = await self.perceptor.analyze_visual(path, query)
            
            # Clean up
            os.remove(path)
            
            return f"👀 **Анализ экрана:**\n{vision_response}"
        except Exception as e:
            logger.error(f"Screen Analysis Error: {e}")
            return f"Ошибка анализа экрана: {e}"
