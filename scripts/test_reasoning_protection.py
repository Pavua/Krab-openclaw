# -*- coding: utf-8 -*-
import json
import asyncio
import collections
from typing import AsyncGenerator, Dict, Any

class CircularRepetitionDetector:
    def __init__(self, window_size=10, threshold=3):
        self.window = collections.deque(maxlen=window_size)
        self.threshold = threshold
        self.repetitions = collections.defaultdict(int)

    def is_repeating(self, text: str) -> bool:
        if not text: return False
        clean_text = text.strip()
        if len(clean_text) < 5: return False
        if clean_text in self.window:
            self.repetitions[clean_text] += 1
            if self.repetitions[clean_text] >= self.threshold:
                return True
        else:
            self.window.append(clean_text)
            self.repetitions[clean_text] = 1
        return False

async def mock_sse_reasoning_loop():
    # Симулируем зацикливание в REASONING
    for i in range(5):
        data = {
            "choices": [{
                "delta": {"reasoning_content": "Я думаю о лесе. "}
            }]
        }
        yield f"data: {json.dumps(data)}\n\n".encode('utf-8')
    # Потом должен пойти контент, но мы должны обрезать раньше
    data = {"choices": [{"delta": {"content": "Привет!"}}]}
    yield f"data: {json.dumps(data)}\n\n".encode('utf-8')
    yield b"data: [DONE]\n\n"

async def test_reasoning_protection():
    print("🚀 Тестирование защиты Reasoning...")
    detector = CircularRepetitionDetector(window_size=10, threshold=3)
    collected_reasoning = 0
    MAX_REASONING_LIMIT = 50 # Маленький лимит для теста
    
    async for line_bytes in mock_sse_reasoning_loop():
        line = line_bytes.decode('utf-8').strip()
        if not line or line == "data: [DONE]": continue
        if line.startswith("data: "):
            data = json.loads(line[6:])
            delta = data["choices"][0]["delta"]
            
            reasoning = delta.get("reasoning_content")
            if reasoning:
                if detector.is_repeating(reasoning):
                    print(f"✅ Успех: Обнаружен повтор в Reasoning: '{reasoning.strip()}'")
                    continue
                
                collected_reasoning += len(reasoning)
                if collected_reasoning > MAX_REASONING_LIMIT:
                    print(f"✅ Успех: Превышен лимит Reasoning ({collected_reasoning} > {MAX_REASONING_LIMIT})")
                    break
            
            content = delta.get("content")
            if content:
                print(f"❌ Ошибка: Дошли до контента '{content}', хотя должны были сорваться на reasoning")

if __name__ == "__main__":
    asyncio.run(test_reasoning_protection())
