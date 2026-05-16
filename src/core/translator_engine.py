# -*- coding: utf-8 -*-
"""
translator_engine.py — движок перевода текста через OpenClaw LLM.

Stateless single-shot запрос без session history.
Используется в Translator MVP для перевода транскриптов voice notes.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

import httpx
import structlog

from .translation_cache import translation_cache

if TYPE_CHECKING:
    from ..openclaw_client import OpenClawClient

logger = structlog.get_logger("Krab.core.translator_engine")


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


async def _translate_via_lmstudio(
    text: str,
    src_lang: str,
    tgt_lang: str,
    *,
    timeout_sec: float = 30.0,
) -> tuple[str, str]:
    """Session 52 P2: translator через локальный LM Studio (Gemma 4 26B vanilla).

    Reuse'ит same loaded model что и vision describe (`krab-vision-primary` =
    `gemma-4-26b-a4b-it@4bit`, ~15.6 GB RAM). Translator — highest-frequency
    cloud-burner (auto-translate потоков, voice transcript translation),
    переход на local даёт **biggest cost savings** without RAM increase
    (model already loaded для vision).

    Bench S52: Gemma 4 26B 26B vanilla = clean Russian translations + accurate
    formal style preserved ("Быстрая бурая лиса перепрыгивает через ленивую
    собаку" formal idiomatic).

    Env config (reuse vision env + dedicated translator override):
    - ``KRAB_LOCAL_VISION_URL`` (LM Studio endpoint, default :1234)
    - ``KRAB_LOCAL_TRANSLATOR_MODEL`` или fallback ``KRAB_LOCAL_VISION_MODEL``
      (default ``gemma-4-26b-a4b-it@4bit``)
    - ``LM_STUDIO_API_KEY`` — Bearer auth (existing env)

    Returns: ``(translated_text, model_id)``. Empty translated string при
    любой ошибке — caller fall-back на cloud path.
    """
    url = os.getenv("KRAB_LOCAL_VISION_URL", "http://127.0.0.1:1234").rstrip("/")
    model = os.getenv(
        "KRAB_LOCAL_TRANSLATOR_MODEL",
        os.getenv("KRAB_LOCAL_VISION_MODEL", "gemma-4-26b-a4b-it@4bit"),
    )
    api_key = os.getenv("LM_STUDIO_API_KEY", "")

    prompt = build_translation_prompt(text, src_lang, tgt_lang)
    system = (
        "Ты — профессиональный переводчик. Переводи точно, сохраняя смысл и стиль. "
        "Не добавляй пояснений, не повторяй оригинал. Только перевод."
    )

    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": 512,
        "temperature": 0.0,
        "stream": False,
    }
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        async with httpx.AsyncClient(timeout=timeout_sec) as client:
            resp = await client.post(
                f"{url}/v1/chat/completions",
                json=body,
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()
        msg = (data.get("choices") or [{}])[0].get("message", {})
        translated = msg.get("content") or msg.get("reasoning") or ""
        return translated.strip(), f"lmstudio/{model}"
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "lmstudio_translate_failed",
            error=str(exc)[:200],
            error_type=type(exc).__name__,
            src_lang=src_lang,
            tgt_lang=tgt_lang,
        )
        return "", "lmstudio_error"


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

    # Wave 95: content-hash cache — на повторяющиеся phrases экономим LLM-вызов.
    # Cache lookup делаем ДО session clear / API call. Если hit — возвращаем
    # сразу, model_id маркируем как "translation_cache" чтобы было видно в stats.
    cached = translation_cache.lookup(text, tgt_lang)
    if cached is not None:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        return TranslationResult(
            original=text,
            translated=cached,
            src_lang=src_lang,
            tgt_lang=tgt_lang,
            latency_ms=elapsed_ms,
            model_id="translation_cache",
        )

    chunks: list[str] = []

    # Session 52 P2: local translator routing (default off → safe roll-out).
    # Reuse'ит loaded vision model `krab-vision-primary` — нулевой RAM cost,
    # closes biggest cloud-burner (auto-translate потоков).
    if os.getenv("KRAB_LOCAL_TRANSLATOR_ENABLED", "0") == "1":
        translated_local, model_id_local = await _translate_via_lmstudio(text, src_lang, tgt_lang)
        if translated_local:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            logger.info(
                "translate_local_success",
                src_lang=src_lang,
                tgt_lang=tgt_lang,
                latency_ms=elapsed_ms,
                char_count=len(translated_local),
            )
            if translated_local.startswith('"') and translated_local.endswith('"'):
                translated_local = translated_local[1:-1]
            try:
                translation_cache.store(text, tgt_lang, translated_local)
            except Exception:
                pass
            return TranslationResult(
                original=text,
                translated=translated_local,
                src_lang=src_lang,
                tgt_lang=tgt_lang,
                latency_ms=elapsed_ms,
                model_id=model_id_local,
            )
        logger.info(
            "translate_local_empty_fallthrough",
            src_lang=src_lang,
            tgt_lang=tgt_lang,
        )

    # Cloud path (existing, used when local disabled либо local empty).
    # Предварительно очищаем session чтобы не тратить время на history lookup
    try:
        openclaw_client.clear_session(chat_id)
    except Exception:
        pass

    async for chunk in openclaw_client.send_message_stream(
        message=prompt,
        chat_id=chat_id,
        system_prompt=system,
        force_cloud=True,
        preferred_model="google/gemini-3-flash-preview",
        max_output_tokens=512,  # переводы коротких фраз, 2048 избыточно
        disable_tools=True,
    ):
        chunks.append(chunk)

    elapsed_ms = int((time.monotonic() - start) * 1000)
    translated = "".join(chunks).strip()

    if translated.startswith('"') and translated.endswith('"'):
        translated = translated[1:-1]

    model_id = "unknown"
    try:
        route = getattr(openclaw_client, "_last_runtime_route", None)
        if route and isinstance(route, dict):
            model_id = route.get("model", "unknown")
    except Exception:
        pass

    # Чистим session после single-shot
    try:
        openclaw_client.clear_session(chat_id)
    except Exception:
        pass

    # Wave 95: сохраняем successful translation в content-hash cache.
    if translated:
        try:
            translation_cache.store(text, tgt_lang, translated)
        except Exception:
            # Cache write не должен влиять на translator response.
            pass

    return TranslationResult(
        original=text,
        translated=translated,
        src_lang=src_lang,
        tgt_lang=tgt_lang,
        latency_ms=elapsed_ms,
        model_id=model_id,
    )
