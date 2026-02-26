
import asyncio
import os
import sys

# Добавляем путь к src, чтобы импортировать perceptor
sys.path.append(os.path.join(os.getcwd(), "src"))

try:
    from modules.perceptor import Perceptor
except ImportError as e:
    print(f"Import error: {e}")
    # Пробуем вывести содержимое src для отладки
    print(f"Current dir: {os.getcwd()}")
    print(f"Contents of src: {os.listdir('src') if os.path.exists('src') else 'NOT FOUND'}")
    sys.exit(1)

async def test_voice():
    print("Testing edge-tts integration...")
    perceptor = Perceptor(config={})
    
    test_text = "Привет! Я Краб. Теперь я говорю качественным нейронным голосом от Microsoft Edge. Послушай, как чисто звучит моя русская речь!"
    
    # Путь будет в artifacts/downloads/
    ogg_path = await perceptor.speak(test_text)
    
    if ogg_path and os.path.exists(ogg_path):
        print(f"✅ Success! Voice file generated at: {ogg_path}")
        print(f"File size: {os.path.getsize(ogg_path)} bytes")
    else:
        print("❌ Failed to generate voice file.")

if __name__ == "__main__":
    asyncio.run(test_voice())
