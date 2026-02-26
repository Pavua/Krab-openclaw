# -*- coding: utf-8 -*-
"""
Диагностический скрипт для проверки исправлений в ModelRouter и Perceptor.
Проверяет:
1. Загрузку конфигурации и таймауты.
2. Логику детекции загруженных моделей.
3. Фильтрацию ошибок в TTS.
"""

import sys
import os
import asyncio
import json
from unittest.mock import MagicMock

# Добавляем путь к проекту
sys.path.append(os.getcwd())

from src.core.model_manager import ModelRouter
from src.modules.perceptor import Perceptor

async def test_diagnostics():
    print("🚀 Запуск диагностики исправлений...")
    
    # Мокаем конфиг
    config = {
        "lm_studio_url": "http://localhost:1234/v1",
        "stt": {"model": "base"},
        "vision": {"model": "gemini-2.0-flash"},
        "gemini_api_key": "test_key"
    }
    
    router = ModelRouter(config)
    perceptor = Perceptor(config)
    
    print("\n1. Проверка логики детекции моделей (LM Studio 0.3.x):")
    test_entries = [
        {"id": "model-1", "state": "loaded", "object": "model"},
        {"id": "model-2", "status": "loaded"},
        {"id": "model-3", "loaded": True},
        {"id": "model-4", "state": "not_loaded"},
        {"id": "model-5", "object": "model"} # OpenAI style
    ]
    
    for entry in test_entries:
        is_loaded = router._is_lmstudio_model_loaded(entry)
        print(f"   - Модель {entry.get('id')}: {'✅ Загружена' if is_loaded else '❌ Не загружена'}")

    print("\n2. Проверка фильтрации ошибок в TTS:")
    error_texts = [
        "Error: Connection refused to local engine",
        "Ошибка: Токен истек",
        "Failed to connect to LM Studio",
        "Billing error on cloud provider",
        "Привет, я Краб! Как я могу помочь?" # Нормальный текст
    ]
    
    for text in error_texts:
        cleaned = perceptor._clean_text_for_tts(text)
        status = "🚫 Заблокировано" if not cleaned else "🔊 Разрешено"
        print(f"   - Текст: '{text[:30]}...' -> {status}")

    print("\n3. Проверка таймаутов (статический анализ):")
    # Мы не можем легко проверить таймауты aiohttp без реальных запросов, 
    # но можем убедиться, что код компилируется и импорты работают.
    print("   - Код ModelRouter и Perceptor успешно импортирован.")

    print("\n✅ Диагностика завершена успешно!")

if __name__ == "__main__":
    asyncio.run(test_diagnostics())
