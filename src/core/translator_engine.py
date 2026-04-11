# -*- coding: utf-8 -*-
"""
translator_engine.py — движок перевода текста через OpenClaw LLM.

Stateless single-shot запрос без session history.
Используется в Translator MVP для перевода транскриптов voice notes.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..openclaw_client import OpenClawClient


# Языки для промпта
_LANG_NAMES: dict[str, str] = {
    "es": "испанского",
    "en": "английского",
    "ru": "русского",
    "fr": "французского",
    "de": "немецкого",
    "it": "итальянского",
    "pt": "португальского",
    "uk": "украинского",
}

_LANG_NAMES_TO: dict[str, str] = {
    "es": "испанский",
    "en": "английский",
    "ru": "русский",
    "fr": "французский",
    "de": "немецкий",
    "it": "итальянский",
    "pt": "португальский",
    "uk": "украинский",
}


@dataclass
class TranslationResult:
    """Результат перевода."""

    original: str
    translated: str
    src_lang: str
    tgt_lang: str
    latency_ms: int
    model_id: str


def build_translation_prompt(text: str, src_lang: str, tgt_lang: str) -> str:
    """Строит промпт для LLM-перевода."""
    src_name = _LANG_NAMES.get(src_lang, src_lang)
    tgt_name = _LANG_NAMES_TO.get(tgt_lang, tgt_lang)
    return (
        f"Переведи следующий текст с {src_name} на {tgt_name}. "
        f"Верни ТОЛЬКО перевод, без пояснений, комментариев и кавычек.\n\n"
        f"{text}"
    )


async def translate_text(
    text: str,
    src_lang: str,
    tgt_lang: str,
    *,
    openclaw_client: "OpenClawClient",
    chat_id: str = "translator_mvp",
) -> TranslationResult:
    """
    Переводит текст через OpenClaw LLM (flash tier).

    Использует send_message_stream с выделенным chat_id,
    чтобы не загрязнять основную session history.
    """
    prompt = build_translation_prompt(text, src_lang, tgt_lang)
    system = (
        "Ты — профессиональный переводчик. Переводи точно, сохраняя смысл и стиль. "
        "Не добавляй пояснений, не повторяй оригинал. Только перевод."
    )

    start = time.monotonic()
    chunks: list[str] = []

    async for chunk in openclaw_client.send_message_stream(
        message=prompt,
        chat_id=chat_id,
        system_prompt=system,
        force_cloud=True,
        preferred_model="google/gemini-3-flash-preview",  # flash tier для скорости перевода
        max_output_tokens=2048,
        disable_tools=True,
    ):
        chunks.append(chunk)

    elapsed_ms = int((time.monotonic() - start) * 1000)
    translated = "".join(chunks).strip()

    # Убираем возможные кавычки
    if translated.startswith('"') and translated.endswith('"'):
        translated = translated[1:-1]

    # Получаем model_id из last_runtime_route
    model_id = "unknown"
    try:
        route = getattr(openclaw_client, "_last_runtime_route", None)
        if route and isinstance(route, dict):
            model_id = route.get("model", "unknown")
    except Exception:
        pass

    # Чистим session после single-shot (не накапливаем history)
    try:
        openclaw_client.clear_session(chat_id)
    except Exception:
        pass

    return TranslationResult(
        original=text,
        translated=translated,
        src_lang=src_lang,
        tgt_lang=tgt_lang,
        latency_ms=elapsed_ms,
        model_id=model_id,
    )
