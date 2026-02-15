# -*- coding: utf-8 -*-
"""
[PHASE 15.2] OpenClaw WebSocket Stream Client
Обеспечивает потоковую передачу данных через WebSocket для мгновенных ответов.
"""
import json
import asyncio
import aiohttp
from typing import AsyncGenerator, Optional, Dict, Any
import structlog
import collections

logger = structlog.get_logger(__name__)

class CircularRepetitionDetector:
    """
    Детектор циклических повторений в потоке текста.
    """
    def __init__(self, window_size=200, threshold=3):
        self.window = collections.deque(maxlen=window_size)
        self.threshold = threshold
        self.repetitions = collections.defaultdict(int)

    def is_repeating(self, text: str) -> bool:
        if not text: return False
        
        # Очищаем от лишних пробелов для лучшего сравнения
        clean_text = text.strip()
        if len(clean_text) < 5: return False # Игнорируем совсем короткие фразы (эмодзи, знаки)
        
        # Проверяем, есть ли уже такой текст в окне
        if clean_text in self.window:
            self.repetitions[clean_text] += 1
            if self.repetitions[clean_text] >= self.threshold:
                return True
        else:
            # Если текст новый, добавляем его в окно и сбрасываем счетчик
            # (или инициализируем, если его не было)
            self.window.append(clean_text)
            self.repetitions[clean_text] = 1
            # Удаляем старые записи, которые вышли за пределы окна
            # (deque сам управляет размером, но repetitions нужно чистить вручную)
            # Это упрощенная логика, для точной очистки нужно отслеживать элементы при удалении из deque
            # Для текущей задачи, где важен только "повтор", это может быть приемлемо.
            # Более строгая реализация потребовала бы более сложного управления self.repetitions
        return False

class OpenClawStreamClient:
    """
    WebSocket клиент для стриминга ответов из OpenClaw / LM Studio.
    """
    def __init__(self, base_url: str, api_key: str = "none"):
        self.base_url = base_url.replace("http://", "ws://").replace("https://", "wss://")
        if "/v1" not in self.base_url:
            self.base_url = self.base_url.rstrip("/") + "/v1"
        self.ws_url = f"{self.base_url}/chat/completions"
        self.api_key = api_key

    async def stream_chat(self, payload: Dict[str, Any]) -> AsyncGenerator[str, None]:
        """
        Открывает WebSocket соединение и возвращает генератор чанков текста.
        """
        # Гарантируем, что стриминг включен в payload
        payload["stream"] = True
        
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

        try:
            async with aiohttp.ClientSession() as session:
                logger.info("📡 Starting stream request", url=self.ws_url, model=payload.get("model"))
                
                async with session.post(
                    self.ws_url.replace("ws://", "http://").replace("wss://", "https://"),
                    json=payload,
                    headers=headers
                ) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        logger.error(f"Stream error {response.status}: {error_text}")
                        yield f"❌ Ошибка API ({response.status})"
                        return

                    collected_chars = 0
                    collected_reasoning = 0
                    MAX_CHARS_LIMIT = 4000 
                    MAX_REASONING_LIMIT = 2000 # Лимит на скрытые размышления
                    detector = CircularRepetitionDetector(window_size=10, threshold=3)

                    # Читаем SSE поток
                    async for line in response.content:
                        line = line.decode('utf-8').strip()
                        if not line or line == "data: [DONE]":
                            continue
                        
                        if line.startswith("data: "):
                            try:
                                data = json.loads(line[6:])
                                choices = data.get("choices", [])
                                if choices:
                                    delta = choices[0].get("delta", {})
                                    
                                    # Обработка размышлений (Reasoning)
                                    reasoning = delta.get("reasoning_content") or delta.get("reasoning")
                                    if reasoning:
                                        if detector.is_repeating(reasoning):
                                            logger.warning("⚠️ REASONING LOOP DETECTED. Breaking stream.")
                                            yield "\n\n⚠️ [Обнаружено зацикливание в размышлениях — генерация остановлена]"
                                            return
                                            
                                        collected_reasoning += len(reasoning)
                                        if collected_reasoning > MAX_REASONING_LIMIT:
                                            logger.warning(f"⚠️ REASONING TRUNCATION: Exceeded {MAX_REASONING_LIMIT}. Stopping stream.")
                                            yield "\n\n⚠️ [Превышен лимит размышлений — поток остановлен]"
                                            return

                                    # Обработка основного контента
                                    chunk = delta.get("content")
                                    if chunk:
                                        if detector.is_repeating(chunk):
                                            logger.warning(f"⚠️ CONTENT LOOP DETECTED: Repetitive chunk found. Breaking.")
                                            yield "\n\n⚠️ [Обнаружено зацикливание — генерация остановлена]"
                                            return

                                        yield chunk
                                        collected_chars += len(chunk)
                                        
                                        if collected_chars > MAX_CHARS_LIMIT:
                                            logger.warning(f"⚠️ HARD TRUNCATION: Stream exceeded {MAX_CHARS_LIMIT} chars. Breaking.")
                                            yield "\n\n⚠️ [Генерация прервана лимитом символов]"
                                            return
                            except Exception as e:
                                logger.debug(f"Failed to parse SSE line: {line} | Error: {e}")

        except asyncio.CancelledError:
            logger.info("Stream cancelled by user/client")
            raise
        except Exception as e:
            logger.error(f"WebSocket/Stream connection failed: {e}")
            yield f"⚠️ Ошибка соединения: {str(e)}"
