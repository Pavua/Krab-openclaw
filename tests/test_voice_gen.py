import asyncio
import os
import sys

# Add src to path
sys.path.append(os.path.join(os.getcwd(), "src"))

from voice_engine import text_to_speech


async def test_voice():
    print("🎙️ Testing Voice Engine...")
    text = "Привет, это тестовое голосовое сообщение от Краба. Проверка скорости 1.5x."

    try:
        path = await text_to_speech(text, filename="test_voice.ogg")
        if path and os.path.exists(path):
            size = os.path.getsize(path)
            print(f"✅ Success! File generated at: {path}")
            print(f"📊 Size: {size} bytes")
        else:
            print("❌ Failed! File not found.")
    except Exception as e:
        print(f"❌ Error: {e}")


if __name__ == "__main__":
    asyncio.run(test_voice())
