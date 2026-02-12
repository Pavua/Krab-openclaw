# -*- coding: utf-8 -*-
"""
Image Generation Manager.
Поддерживает генерацию через локальный ComfyUI (FLUX) или облачную модель (Gemini Imagen 3).
"""

import os
import asyncio
import aiohttp
import uuid
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger("ImageManager")

class ImageManager:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.comfy_url = config.get("COMFY_URL", "http://localhost:8188")
        self.gemini_key = config.get("GEMINI_API_KEY") or os.getenv("GEMINI_API_KEY")
        
        # Модель для генерации (Imagen 3)
        self.cloud_model = "imagen-3.0-generate-001" 

    async def generate(self, prompt: str, aspect_ratio: str = "1:1") -> Optional[str]:
        """
        Основной метод генерации изображения.
        Сначала пробует локальный ComfyUI, затем облачный Imagen.
        Returns: Path to the generated image.
        """
        # 1. Пробуем локально через ComfyUI (если запущен)
        # TODO: Реализовать полноценный ComfyUI API Client
        # Пока сделаем заглушку, проверяющую доступность порта
        if await self._is_comfy_online():
            logger.info("🎨 Attempting local generation via ComfyUI...")
            # Здесь могла бы быть логика FLUX-воркфлоу
            # Но для начала сделаем фолбек на облако, пока не настроим воркфлоу
            pass

        # 2. Облачная генерация (Gemini Imagen)
        return await self._generate_cloud(prompt, aspect_ratio)

    async def _is_comfy_online(self) -> bool:
        try:
            timeout = aiohttp.ClientTimeout(total=1)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(self.comfy_url) as response:
                    return response.status == 200
        except:
            return False

    async def _generate_cloud(self, prompt: str, aspect_ratio: str) -> Optional[str]:
        """Генерация через Google Imagen API."""
        try:
            from google import genai
            from google.genai import types
            
            if not self.gemini_key:
                logger.error("No Gemini API key for image generation.")
                return None
                
            client = genai.Client(api_key=self.gemini_key)
            
            logger.info(f"☁️ Generating image via Imagen: {prompt[:50]}...")
            
            # Imagen 3 Request
            # Примечание: Imagen API может отличаться в разных версиях SDK
            # В v1.0 это обычно models.generate_image или через generate_content с модальностью
            
            # Для простоты используем generate_image если оно доступно в SDK
            # Если нет, используем асинхронный вызов через thread
            response = await asyncio.to_thread(
                client.models.generate_image,
                model=self.cloud_model,
                prompt=prompt,
                config=types.GenerateImageConfig(
                    number_of_images=1,
                    include_rai_reasoning=True,
                    # aspect_ratio=aspect_ratio # Не все версии поддерживают
                )
            )
            
            if response and response.generated_images:
                img_data = response.generated_images[0].image.image_bytes
                
                os.makedirs("artifacts/downloads", exist_ok=True)
                file_path = f"artifacts/downloads/gen_{uuid.uuid4().hex[:8]}.png"
                
                with open(file_path, "wb") as f:
                    f.write(img_data)
                
                logger.info(f"✅ Image generated and saved: {file_path}")
                return file_path
                
            return None

        except Exception as e:
            logger.error(f"Cloud Image Gen Error: {e}")
            return None
