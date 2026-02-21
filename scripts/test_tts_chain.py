# -*- coding: utf-8 -*-
import asyncio
import os
import sys
from pathlib import Path

# Добавляем путь к src, чтобы импортировать Perceptor
sys.path.append(str(Path(__file__).parent.parent))

from src.modules.perceptor import Perceptor

async def test_tts():
    print("🚀 Starting TTS Chain Test...")
    
    config = {
        "WHISPER_MODEL": "base", # Не важно для этого теста
    }
    
    perceptor = Perceptor(config)
    test_text = "Привет! Это проверка системы голосовых ответов Краба. Если ты это слышишь, значит всё работает."
    
    print(f"📝 Testing with text: {test_text}")
    
    ogg_path = await perceptor.speak(test_text)
    
    if ogg_path and os.path.exists(ogg_path):
        size = os.path.getsize(ogg_path)
        print(f"✅ SUCCESS: OGG generated at {ogg_path} ({size} bytes)")
        # Не удаляем файл, чтобы пользователь мог проверить вручную если захочет
    else:
        print("❌ FAILED: OGG was not generated.")

if __name__ == "__main__":
    asyncio.run(test_tts())
