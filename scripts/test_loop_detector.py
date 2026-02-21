import asyncio
import json
import collections
import sys
import os

# Добавляем путь к проекту
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.core.stream_client import CircularRepetitionDetector

async def test_repetition_detector():
    print("🚀 Тестирование CircularRepetitionDetector...")
    
    # Инициализация (порог 3 повтора)
    detector = CircularRepetitionDetector(window_size=5, threshold=3)
    
    test_chunks = [
        "Привет",
        " меня зовут ",
        "Краб",
        ". ",
        "Как дела?",
        "Как дела?", # 2-й раз
        "Как дела?", # 3-й раз -> ДОЛЖЕН СРАБОТАТЬ
    ]
    
    triggered = False
    for chunk in test_chunks:
        if detector.is_repeating(chunk):
            print(f"✅ Успех: Детектор сработал на чанке: '{chunk}'")
            triggered = True
            break
        else:
            print(f"   Обработан чанк: '{chunk}'")
            
    if not triggered:
        print("❌ Ошибка: Детектор не сработал на повторе 3 раза.")
        
    # Тест на длинные фразы (как в скриншоте пользователя)
    print("\n📝 Тест на длинные повторяющиеся фразы...")
    detector = CircularRepetitionDetector(window_size=5, threshold=2)
    long_loop = "В лесу Иван вернулся к себе домой, и его собака Лиса была рядом с ним. Она была очень умной и ловкой собакой."
    
    chunks = [long_loop for _ in range(5)]
    
    triggered = False
    for i, chunk in enumerate(chunks, 1):
        if detector.is_repeating(chunk):
            print(f"✅ Успех: Детектор сработал на повторе длиного текста (попытка {i})")
            triggered = True
            break
        else:
            print(f"   Попытка {i} принята")

    if not triggered:
        print("❌ Ошибка: Детектор не сработал на длинном повторе.")

if __name__ == "__main__":
    asyncio.run(test_repetition_detector())
