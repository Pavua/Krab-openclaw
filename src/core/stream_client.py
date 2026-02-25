# -*- coding: utf-8 -*-
"""
[PHASE 17.8] Local Stream Client с guardrails и структурированными причинами сбоев.

Зачем:
1. Защитить Telegram-диалог от зацикленных reasoning/content чанков.
2. Останавливать зависшие потоки по таймауту и лимитам.
3. Возвращать машиночитаемую причину сбоя для fallback на cloud в ModelRouter.
"""

import json
import asyncio
import os
import time
import aiohttp
import re
from typing import AsyncGenerator, Dict, Any
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


class StreamFailure(RuntimeError):
    """
    Ошибка потока с типизированной причиной.

    reason:
    - connection_error
    - reasoning_limit
    - reasoning_loop
    - content_loop
    - stream_timeout
    """

    def __init__(self, reason: str, technical_message: str):
        self.reason = reason
        self.technical_message = technical_message
        super().__init__(f"{reason}: {technical_message}")


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
        self.default_max_chars = self._read_int_env("LOCAL_STREAM_MAX_CHARS", 20000)
        self.default_max_reasoning_chars = self._read_int_env("LOCAL_REASONING_MAX_CHARS", 2000)
        # Для тяжёлых MLX-моделей на Mac первый токен может идти заметно дольше.
        # Делаем щадящие дефолты, чтобы не рвать локальный канал раньше времени.
        self.default_total_timeout_seconds = self._read_float_env("LOCAL_STREAM_TOTAL_TIMEOUT_SECONDS", 240.0)
        self.default_sock_read_timeout_seconds = self._read_float_env("LOCAL_STREAM_SOCK_READ_TIMEOUT_SECONDS", 90.0)

    @staticmethod
    def _read_int_env(name: str, default: int) -> int:
        raw = str(os.getenv(name, default)).strip()
        try:
            value = int(raw)
            return value if value > 0 else default
        except Exception:
            return default

    @staticmethod
    def _read_float_env(name: str, default: float) -> float:
        raw = str(os.getenv(name, default)).strip()
        try:
            value = float(raw)
            return value if value > 0 else default
        except Exception:
            return default

    @staticmethod
    def _pop_positive_int(payload: Dict[str, Any], key: str, default: int) -> int:
        raw = payload.pop(key, default)
        try:
            value = int(raw)
            return value if value > 0 else default
        except Exception:
            return default

    @staticmethod
    def _pop_positive_float(payload: Dict[str, Any], key: str, default: float) -> float:
        raw = payload.pop(key, default)
        try:
            value = float(raw)
            return value if value > 0 else default
        except Exception:
            return default

    @staticmethod
    def _normalize_for_loop(text: str) -> str:
        """Нормализует текст для устойчивой проверки циклических повторов."""
        if not text:
            return ""
        normalized = re.sub(r"\s+", " ", str(text)).strip().lower()
        return normalized

    @classmethod
    def _has_repeated_tail_loop(cls, content: str) -> bool:
        """
        Проверяет повтор хвоста контента, чтобы ловить циклы,
        когда модель повторяет абзац с другими чанк-границами.
        """
        normalized = cls._normalize_for_loop(content)
        if len(normalized) < 360:
            return False

        # Несколько длин блока, чтобы поймать и короткие, и длинные повторы.
        for block_len in (80, 120, 160, 220):
            tail = normalized[-block_len:]
            if len(tail) < block_len:
                continue
            if normalized.endswith(tail * 3):
                return True
        return False

    async def stream_chat(self, payload: Dict[str, Any]) -> AsyncGenerator[str, None]:
        """
        Открывает WebSocket соединение и возвращает генератор чанков текста.
        """
        request_payload = dict(payload)
        # Внутренние поля не должны уходить в LM Studio.
        max_chars_limit = self._pop_positive_int(
            request_payload, "_krab_max_chars", self.default_max_chars
        )
        max_reasoning_limit = self._pop_positive_int(
            request_payload, "_krab_max_reasoning_chars", self.default_max_reasoning_chars
        )
        total_timeout_seconds = self._pop_positive_float(
            request_payload, "_krab_total_timeout_seconds", self.default_total_timeout_seconds
        )
        sock_read_timeout_seconds = self._pop_positive_float(
            request_payload, "_krab_sock_read_timeout_seconds", self.default_sock_read_timeout_seconds
        )

        request_payload["stream"] = True

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

        timeout = aiohttp.ClientTimeout(
            total=max(total_timeout_seconds + 5.0, sock_read_timeout_seconds + 5.0),
            sock_read=sock_read_timeout_seconds,
        )

        try:
            started_at = time.monotonic()
            async with aiohttp.ClientSession(timeout=timeout) as session:
                logger.info("📡 Starting stream request", url=self.ws_url, model=payload.get("model"))

                async with session.post(
                    self.ws_url.replace("ws://", "http://").replace("wss://", "https://"),
                    json=request_payload,
                    headers=headers
                ) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        raise StreamFailure(
                            "connection_error",
                            f"HTTP {response.status}: {error_text[:250]}",
                        )

                    collected_chars = 0
                    collected_reasoning = 0
                    detector = CircularRepetitionDetector(window_size=10, threshold=3)
                    collected_content = ""

                    async for line in response.content:
                        if (time.monotonic() - started_at) > total_timeout_seconds:
                            raise StreamFailure(
                                "stream_timeout",
                                f"total timeout>{total_timeout_seconds:.1f}s",
                            )

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
                                            raise StreamFailure(
                                                "reasoning_loop",
                                                "detected repetitive reasoning chunks",
                                            )

                                        collected_reasoning += len(reasoning)
                                        if collected_reasoning > max_reasoning_limit:
                                            raise StreamFailure(
                                                "reasoning_limit",
                                                f"reasoning>{max_reasoning_limit}",
                                            )

                                    # Обработка основного контента
                                    chunk = delta.get("content")
                                    if chunk:
                                        if detector.is_repeating(chunk):
                                            raise StreamFailure(
                                                "content_loop",
                                                "detected repetitive content chunks",
                                            )

                                        collected_content += chunk
                                        if self._has_repeated_tail_loop(collected_content):
                                            raise StreamFailure(
                                                "content_loop",
                                                "detected repetitive content tail loop",
                                            )

                                        yield chunk
                                        collected_chars += len(chunk)

                                        if collected_chars > max_chars_limit:
                                            logger.warning(
                                                "⚠️ HARD TRUNCATION: stream exceeded char limit",
                                                max_chars=max_chars_limit,
                                            )
                                            return
                            except StreamFailure:
                                raise
                            except Exception as e:
                                logger.debug(f"Failed to parse SSE line: {line} | Error: {e}")

        except asyncio.CancelledError:
            logger.info("Stream cancelled by user/client")
            raise
        except StreamFailure:
            raise
        except asyncio.TimeoutError as e:
            raise StreamFailure("stream_timeout", f"{type(e).__name__}: {e}") from e
        except aiohttp.ClientError as e:
            raise StreamFailure("connection_error", f"{type(e).__name__}: {e}") from e
        except Exception as e:
            raise StreamFailure("connection_error", f"{type(e).__name__}: {e}") from e
