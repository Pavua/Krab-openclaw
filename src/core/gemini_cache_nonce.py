# -*- coding: utf-8 -*-
"""
Gemini prompt-cache nonce registry.

Стратегия: чтобы инвалидировать Gemini prompt cache для конкретного чата
без перезапуска рантайма и без изменения контента диалога, добавляем
уникальный nonce в system_prompt. При следующем LLM-вызове system_prompt
будет отличаться → cache miss → новый prefill.

Вынесено в отдельный модуль во избежание циклических импортов между
`handlers.command_handlers` (где живёт !reset) и `openclaw_client`
(где формируется system_prompt и шлётся request к Gemini).
"""

from __future__ import annotations

import uuid

# Chat-id → nonce. Volatile (in-memory). Рестарт Краба сбросит map,
# но это ок: после рестарта Gemini cache всё равно не актуален.
_GEMINI_NONCE_MAP: dict[str, str] = {}


def invalidate_gemini_cache_for_chat(chat_id: str) -> str:
    """Генерирует новый UUID-nonce для чата.

    При следующем LLM-вызове system_prompt изменится → Gemini promtp cache miss.
    Возвращает сгенерированный nonce (для логирования).
    """
    nonce = uuid.uuid4().hex
    _GEMINI_NONCE_MAP[str(chat_id)] = nonce
    return nonce


def get_gemini_nonce(chat_id: str) -> str:
    """Возвращает текущий nonce для чата (или пустую строку, если не задан)."""
    return _GEMINI_NONCE_MAP.get(str(chat_id), "")


def clear_gemini_nonce(chat_id: str) -> None:
    """Удаляет nonce для чата (ресет без инвалидации)."""
    _GEMINI_NONCE_MAP.pop(str(chat_id), None)


def _reset_all_nonces_for_tests() -> None:
    """Только для тестов: полный сброс nonce-реестра."""
    _GEMINI_NONCE_MAP.clear()
