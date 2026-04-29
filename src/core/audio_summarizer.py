# -*- coding: utf-8 -*-
"""
AudioSummarizer — bullet-сводка длинных голосовых транскриптов.

Idea 35 (Session 28). Долгие voice-сообщения обычно дают «простыню» текста —
после транскрипции владельцу удобнее видеть 3-5 буллетов вместо стены.

Pure модуль: получает готовый transcript, делает короткий LLM-вызов в LM Studio
(дёшево/локально, без Cloud-cost), возвращает AudioSummary. Wire-up в audio
handler — отдельная задача (см. backlog `Idea 35 wire-up`).

Skip-эвристики:
- Слишком короткий transcript (< MIN_LEN) — нечего сжимать.
- Уже структурированный текст (много цифр / маркеров списка) — bullets не нужны.

Кэш: in-memory LRU по sha256(transcript) — повторные срабатывания (например,
forwarded voice) не уходят в LLM. TTL не нужен — transcript-хэш стабилен.

На любую ошибку LLM/JSON — fail-open: возвращаем None, caller сам решает,
показать ли raw transcript или skip-сводку.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from collections import OrderedDict
from dataclasses import asdict, dataclass

import httpx
import structlog

logger = structlog.get_logger(__name__)

# --- Конфигурация по умолчанию --------------------------------------------

LM_STUDIO_URL = "http://localhost:1234/v1/chat/completions"
LM_STUDIO_TIMEOUT = 5.0  # секунды; summarization чуть дольше intent
CACHE_MAX_SIZE = 200

# Минимальная длина transcript в символах, ниже которой summary не делается
MIN_TRANSCRIPT_CHARS = 100
# Если bullet-маркеров (•, -, 1., 2.) или цифр выше этого порога — текст уже
# структурирован, нет смысла переписывать
STRUCTURED_RATIO_THRESHOLD = 0.08

# Поддерживаемые языки → подсказка в промпт
_LANG_HINT = {
    "ru": "русском",
    "en": "English",
}


@dataclass(frozen=True)
class AudioSummary:
    """Результат суммаризации голосового сообщения."""

    bullets: list[str]
    topic: str
    sentiment: str  # neutral / positive / negative / mixed
    length_chars: int
    cached: bool = False
    latency_ms: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


# --- Эвристики -------------------------------------------------------------

_BULLET_MARKERS = re.compile(r"(?m)^\s*(?:[-*•]|\d+[.)])\s+")
_DIGIT_RUN = re.compile(r"\d+")


def _looks_structured(text: str) -> bool:
    """Текст уже выглядит как список / структура — суммировать не надо."""
    if not text:
        return False
    bullets = len(_BULLET_MARKERS.findall(text))
    if bullets >= 3:
        return True
    # Доля цифр — счета, прайс-листы, расписания
    digit_chars = sum(len(m.group()) for m in _DIGIT_RUN.finditer(text))
    if digit_chars and digit_chars / max(1, len(text)) >= STRUCTURED_RATIO_THRESHOLD:
        return True
    return False


def _detect_lang(text: str) -> str:
    """Простой детектор: кириллица → ru, иначе en. Достаточно для подсказки в промпт."""
    cyr = sum(1 for ch in text if "а" <= ch.lower() <= "я" or ch.lower() == "ё")
    return "ru" if cyr * 2 >= len([c for c in text if c.isalpha()] or [1]) else "en"


# --- Класс ----------------------------------------------------------------


class AudioSummarizer:
    """Сжимает голосовые транскрипты в bullet-сводку через LM Studio."""

    def __init__(
        self,
        *,
        lm_url: str = LM_STUDIO_URL,
        timeout: float = LM_STUDIO_TIMEOUT,
        cache_max_size: int = CACHE_MAX_SIZE,
        min_transcript_chars: int = MIN_TRANSCRIPT_CHARS,
    ) -> None:
        self._url = lm_url
        self._timeout = timeout
        self._cache_max_size = cache_max_size
        self._min_chars = min_transcript_chars
        self._cache: OrderedDict[str, AudioSummary] = OrderedDict()
        self._lock = asyncio.Lock()

    # ----- public API ------------------------------------------------------

    async def summarize(
        self,
        transcript: str,
        *,
        max_bullets: int = 5,
        language: str = "ru",
    ) -> AudioSummary | None:
        """Возвращает AudioSummary или None если суммировать нечего/не получилось.

        None означает: caller должен показать raw transcript (fail-open).
        """
        if not transcript or not transcript.strip():
            return None
        text = transcript.strip()
        # Skip короткие транскрипты — нечего сжимать
        if len(text) < self._min_chars:
            logger.debug(
                "audio_summarizer_skipped",
                reason="too_short",
                length_chars=len(text),
            )
            return None
        # Skip уже структурированный текст
        if _looks_structured(text):
            logger.debug(
                "audio_summarizer_skipped",
                reason="already_structured",
                length_chars=len(text),
            )
            return None

        # Авто-детект, если caller передал 'auto' / неизвестный
        lang = language if language in _LANG_HINT else _detect_lang(text)

        cache_key = self._make_cache_key(text, max_bullets, lang)
        async with self._lock:
            cached = self._cache.get(cache_key)
            if cached is not None:
                self._cache.move_to_end(cache_key)
                return AudioSummary(
                    bullets=list(cached.bullets),
                    topic=cached.topic,
                    sentiment=cached.sentiment,
                    length_chars=cached.length_chars,
                    cached=True,
                    latency_ms=cached.latency_ms,
                )

        prompt = self._build_prompt(text, max_bullets=max_bullets, lang=lang)
        start = time.time()
        try:
            summary = await self._call_lm_studio(
                prompt, max_bullets=max_bullets, length_chars=len(text)
            )
        except Exception as exc:  # fail-open
            logger.warning(
                "audio_summarizer_error",
                error=str(exc),
                error_type=type(exc).__name__,
                length_chars=len(text),
            )
            return None

        summary = AudioSummary(
            bullets=summary.bullets,
            topic=summary.topic,
            sentiment=summary.sentiment,
            length_chars=summary.length_chars,
            cached=False,
            latency_ms=(time.time() - start) * 1000,
        )
        async with self._lock:
            self._cache[cache_key] = summary
            while len(self._cache) > self._cache_max_size:
                self._cache.popitem(last=False)
        logger.info(
            "audio_summarizer_done",
            length_chars=summary.length_chars,
            bullets=len(summary.bullets),
            sentiment=summary.sentiment,
            latency_ms=round(summary.latency_ms, 1),
        )
        return summary

    def cache_size(self) -> int:
        return len(self._cache)

    def clear_cache(self) -> None:
        self._cache.clear()

    # ----- internals -------------------------------------------------------

    @staticmethod
    def _make_cache_key(transcript: str, max_bullets: int, lang: str) -> str:
        raw = f"{lang}|{max_bullets}|{transcript}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _build_prompt(text: str, *, max_bullets: int, lang: str) -> str:
        lang_word = _LANG_HINT.get(lang, "русском")
        # Truncate до разумного — LM Studio модельки маленькие
        clipped = text if len(text) <= 4000 else text[:4000] + "…"
        return f"""Ты сжимаешь транскрипт голосового сообщения в краткую сводку.

ТРАНСКРИПТ:
\"\"\"{clipped}\"\"\"

Верни СТРОГО JSON (без markdown, без комментариев) на {lang_word} языке:
{{
  "bullets": ["..."],          // до {max_bullets} коротких пунктов (≤120 chars каждый)
  "topic": "...",              // 3-7 слов: о чём речь
  "sentiment": "neutral"        // одно из: neutral, positive, negative, mixed
}}

Правила:
- bullets — суть, не пересказ; каждый пункт — отдельная мысль/факт.
- Никаких приветствий и filler-слов («ну», «эээ»).
- Если язык транскрипта другой — переводи на {lang_word}."""

    async def _call_lm_studio(
        self, prompt: str, *, max_bullets: int, length_chars: int
    ) -> AudioSummary:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                self._url,
                json={
                    "model": "auto",
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.2,
                    "max_tokens": 400,
                    "stream": False,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"].strip()

        # Strip markdown fences ```json ... ```
        if content.startswith("```"):
            parts = content.split("```")
            if len(parts) >= 2:
                content = parts[1]
                if content.startswith("json"):
                    content = content[4:]
                content = content.strip()

        parsed = json.loads(content)
        raw_bullets = parsed.get("bullets") or []
        if not isinstance(raw_bullets, list):
            raise ValueError("bullets_not_list")
        bullets: list[str] = []
        for item in raw_bullets[:max_bullets]:
            if not item:
                continue
            txt = str(item).strip().lstrip("-•* ").strip()
            if txt:
                bullets.append(txt[:200])
        if not bullets:
            raise ValueError("empty_bullets")

        topic = str(parsed.get("topic", "")).strip()[:120] or "—"
        sentiment_raw = str(parsed.get("sentiment", "neutral")).strip().lower()
        sentiment = (
            sentiment_raw
            if sentiment_raw
            in {
                "neutral",
                "positive",
                "negative",
                "mixed",
            }
            else "neutral"
        )

        return AudioSummary(
            bullets=bullets,
            topic=topic,
            sentiment=sentiment,
            length_chars=length_chars,
        )


# --- Singleton ------------------------------------------------------------

_default_summarizer: AudioSummarizer | None = None


def get_summarizer() -> AudioSummarizer:
    global _default_summarizer
    if _default_summarizer is None:
        _default_summarizer = AudioSummarizer()
    return _default_summarizer


def reset_summarizer() -> None:
    """Для тестов: сбросить singleton."""
    global _default_summarizer
    _default_summarizer = None
