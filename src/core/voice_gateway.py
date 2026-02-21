# -*- coding: utf-8 -*-
"""
VoiceGateway (Phase 15.3) - Голосовой мост между Krab Ear и AI.
Отвечает за прием транскрипций через IPC/HTTP и запуск цепочки AI -> TTS.
"""
import asyncio
import structlog
from typing import Optional, Any
from src.core.model_manager import ModelRouter
from src.core.context_manager import ContextKeeper

logger = structlog.get_logger("VoiceGateway")

class VoiceGateway:
    def __init__(self, router: ModelRouter, memory: ContextKeeper, perceptor: Any):
        self.router = router
        self.memory = memory
        self.perceptor = perceptor
        self.is_running = False
        self._server_task: Optional[asyncio.Task] = None

    async def start(self):
        """Запуск слушателя IPC (симуляция через HTTP для начала)."""
        if self.is_running:
            return
        self.is_running = True
        logger.info("🎙️ VoiceGateway listening for events from Krab Ear")
        # В реальном сценарии здесь будет aiohttp.web сервер или IPC socket
        # Для Phase 15.3 мы создаем базу для этого потока.

    async def process_voice_input(self, text: str, chat_id: int):
        """
        Основной цикл: STT (уже получен) -> AI Response -> TTS.
        """
        logger.info("🎤 Voice input received", text=text, chat_id=chat_id)
        
        # 1. Получаем контекст
        context = self.memory.get_token_aware_context(chat_id, max_tokens=2048)
        
        # 2. Генерируем ответ (стримингом для логов, но для TTS нужен полный текст)
        full_response = ""
        try:
            async for chunk in self.router.route_stream(
                prompt=text,
                task_type="chat",
                context=context,
                chat_type="private",
                is_owner=True
            ):
                full_response += chunk
            
            if not full_response:
                return

            logger.info("🤖 AI Voice Response ready", length=len(full_response))

            # 3. Синтез речи (TTS)
            if self.perceptor:
                audio_file = await self.perceptor.speak(full_response)
                if audio_file:
                    logger.info("🔊 TTS Generated", file=audio_file)
                    # Здесь должна быть логика воспроизведения на Mac
                    # или отправки в Telegram (в зависимости от режима)
                    return audio_file

        except Exception as e:
            logger.error("Failed to process voice flow", error=str(e))

    async def stop(self):
        self.is_running = False
        if self._server_task:
            self._server_task.cancel()
        logger.info("🎙️ VoiceGateway stopped")
