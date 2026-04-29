"""
Thread Coherence Detector — Feature K (Session 28).

Назначение
==========
Отслеживает связность длинных reply-цепочек в чате и сигнализирует, когда тема
"поплыла" относительно начального сообщения треда. Используется для:

* observability (тихий лог + метрики),
* опционального context reset (drop earlier messages из prompt),
* опционального insert "kontext break" notice.

Pipeline
--------
1. ``score_thread_coherence(messages, current_msg)`` — два косинусных сравнения:
   - similarity к первому сообщению треда (long-term topic anchor),
   - similarity к скользящему окну последних 3 сообщений (short-term drift).
   Финальный score = взвешенная сумма (0.6 anchor + 0.4 window).
2. ``should_break_context(score, threshold)`` — простая проверка порога.
3. ``format_break_notice(original_topic, current_msg)`` — текст уведомления.

Скип-кейсы
----------
* Тред короче ``min_messages`` (default 5) → ``ThreadCoherenceResult.skipped``.
* Все сообщения от одного автора (монолог) → skip.
* Явные ключевые слова смены темы ("кстати", "другая тема", "btw", "off-topic"
  и т.п.) — coherence считается, но ``should_break_context`` подавляется
  (намеренный switch не должен флагаться как drift).

Embeddings
----------
Используем существующий ``encode_text`` из ``memory_embeddings``. На уровне
детектора — LRU-кэш (по hash(text)) чтобы не пересчитывать эмбеддинги для
одних и тех же сообщений на каждом тике треда.

Конфигурация
------------
* ``KRAB_COHERENCE_DETECTION_ENABLED`` — мастер-флаг (default True).
* ``KRAB_COHERENCE_INSERT_BREAK_NOTICE`` — реально вставлять notice (default False).
* ``KRAB_COHERENCE_BREAK_THRESHOLD`` — порог 0..1 (default 0.4).

Внимание: модуль pure (никаких side-effects, никаких hooks в bridge/llm_flow).
Wire-up — отдельным шагом.
"""

from __future__ import annotations

import logging
import os
import re
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Callable, Iterable, Sequence

import numpy as np

from src.core.memory_embeddings import cosine_similarity, encode_text

logger = logging.getLogger(__name__)


# ---------- Конфиг ----------

DEFAULT_MIN_MESSAGES = 5
DEFAULT_THRESHOLD = 0.4
DEFAULT_ANCHOR_WEIGHT = 0.6
DEFAULT_WINDOW_SIZE = 3
DEFAULT_CACHE_SIZE = 256

# Ключевые слова явной смены темы — подавляют break notice.
TOPIC_SWITCH_MARKERS: tuple[str, ...] = (
    "кстати",
    "другая тема",
    "к слову",
    "оффтоп",
    "офф-топ",
    "off-topic",
    "offtopic",
    "btw",
    "by the way",
    "не по теме",
    "сменим тему",
    "новая тема",
)

_SWITCH_RE = re.compile(
    r"\b(" + "|".join(re.escape(m) for m in TOPIC_SWITCH_MARKERS) + r")\b",
    re.IGNORECASE,
)


def _env_flag(key: str, default: bool) -> bool:
    raw = os.environ.get(key)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(key: str, default: float) -> float:
    raw = os.environ.get(key)
    if raw is None:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


# ---------- Модели данных ----------


@dataclass(frozen=True)
class ThreadMessage:
    """Минимальное представление сообщения для анализа когерентности."""

    text: str
    author_id: int | str
    msg_id: int | str | None = None


@dataclass
class ThreadCoherenceResult:
    """Результат анализа треда."""

    score: float
    anchor_similarity: float
    window_similarity: float
    skipped: bool = False
    skip_reason: str = ""
    explicit_switch: bool = False
    original_topic: str = ""
    metadata: dict[str, float] = field(default_factory=dict)

    def is_coherent(self, threshold: float = DEFAULT_THRESHOLD) -> bool:
        if self.skipped:
            return True
        return self.score >= threshold


# ---------- Детектор ----------


class ThreadCoherenceDetector:
    """
    Pure-detector: считает coherence по тредам, кеширует эмбеддинги.

    Не лезет в Telegram, не пишет в БД. Singleton-инстанс ниже файла.
    """

    def __init__(
        self,
        *,
        min_messages: int = DEFAULT_MIN_MESSAGES,
        threshold: float | None = None,
        anchor_weight: float = DEFAULT_ANCHOR_WEIGHT,
        window_size: int = DEFAULT_WINDOW_SIZE,
        cache_size: int = DEFAULT_CACHE_SIZE,
        embedder: Callable[[str], np.ndarray] | None = None,
        enabled: bool | None = None,
    ) -> None:
        self.min_messages = max(2, int(min_messages))
        # Порог: явный аргумент > env > дефолт.
        env_threshold = _env_float("KRAB_COHERENCE_BREAK_THRESHOLD", DEFAULT_THRESHOLD)
        self.threshold = float(threshold) if threshold is not None else env_threshold
        self.anchor_weight = max(0.0, min(1.0, float(anchor_weight)))
        self.window_weight = 1.0 - self.anchor_weight
        self.window_size = max(1, int(window_size))
        self.cache_size = max(1, int(cache_size))
        self._embedder = embedder or encode_text
        if enabled is None:
            self.enabled = _env_flag("KRAB_COHERENCE_DETECTION_ENABLED", True)
        else:
            self.enabled = bool(enabled)
        self._cache: "OrderedDict[str, np.ndarray]" = OrderedDict()
        # Контр-метрики кеша (для тестов и /api/stats в будущем).
        self.cache_hits = 0
        self.cache_misses = 0

    # ---------- API ----------

    def score_thread_coherence(
        self,
        messages: Sequence[ThreadMessage] | Sequence[str],
        current_msg: ThreadMessage | str,
    ) -> ThreadCoherenceResult:
        """
        Посчитать coherence-score для current_msg в контексте messages.

        ``messages`` — тред БЕЗ ``current_msg`` (текущее сообщение передаётся
        отдельно). Возвращает ``ThreadCoherenceResult``.
        """
        if not self.enabled:
            return ThreadCoherenceResult(
                score=1.0,
                anchor_similarity=1.0,
                window_similarity=1.0,
                skipped=True,
                skip_reason="detection_disabled",
            )

        thread = [self._coerce(m) for m in messages]
        current = self._coerce(current_msg)

        # Скип: короткий тред.
        if len(thread) < self.min_messages - 1:
            return ThreadCoherenceResult(
                score=1.0,
                anchor_similarity=1.0,
                window_similarity=1.0,
                skipped=True,
                skip_reason="thread_too_short",
                original_topic=thread[0].text if thread else "",
            )

        # Скип: монолог (все сообщения, включая current, от одного автора).
        all_authors = {m.author_id for m in thread} | {current.author_id}
        if len(all_authors) <= 1:
            return ThreadCoherenceResult(
                score=1.0,
                anchor_similarity=1.0,
                window_similarity=1.0,
                skipped=True,
                skip_reason="monologue",
                original_topic=thread[0].text,
            )

        # Явная смена темы — считаем similarity, но пометим explicit_switch.
        explicit_switch = bool(_SWITCH_RE.search(current.text or ""))

        anchor = thread[0]
        window = thread[-self.window_size :]

        anchor_vec = self._embed(anchor.text)
        current_vec = self._embed(current.text)
        anchor_sim = max(0.0, cosine_similarity(anchor_vec, current_vec))

        # Window: средняя similarity к каждому из последних N сообщений.
        window_sims: list[float] = []
        for m in window:
            v = self._embed(m.text)
            window_sims.append(max(0.0, cosine_similarity(v, current_vec)))
        window_sim = sum(window_sims) / len(window_sims) if window_sims else 0.0

        score = self.anchor_weight * anchor_sim + self.window_weight * window_sim
        # Зажмём в [0, 1] на всякий случай (negatively correlated векторов мы уже cap-ed).
        score = max(0.0, min(1.0, score))

        result = ThreadCoherenceResult(
            score=score,
            anchor_similarity=anchor_sim,
            window_similarity=window_sim,
            skipped=False,
            skip_reason="",
            explicit_switch=explicit_switch,
            original_topic=anchor.text,
            metadata={
                "thread_len": float(len(thread)),
                "window_len": float(len(window)),
                "cache_hits": float(self.cache_hits),
                "cache_misses": float(self.cache_misses),
            },
        )

        if score < self.threshold:
            logger.info(
                "thread_coherence_drift_detected",
                extra={
                    "score": round(score, 3),
                    "anchor_similarity": round(anchor_sim, 3),
                    "window_similarity": round(window_sim, 3),
                    "thread_len": len(thread),
                    "explicit_switch": explicit_switch,
                },
            )
        return result

    def should_break_context(
        self,
        result_or_score: ThreadCoherenceResult | float,
        threshold: float | None = None,
    ) -> bool:
        """
        True, если тема "поплыла" и стоит резать контекст.

        Skipped и explicit_switch результаты возвращают False (намеренно):
        не флагаем монолог/короткий тред/явный switch как drift.
        """
        thr = float(threshold) if threshold is not None else self.threshold
        if isinstance(result_or_score, ThreadCoherenceResult):
            if result_or_score.skipped or result_or_score.explicit_switch:
                return False
            return result_or_score.score < thr
        return float(result_or_score) < thr

    def format_break_notice(self, original_topic: str, current_msg: str) -> str:
        """Форматирует "kontext break" notice для пользователя."""
        topic_preview = (original_topic or "").strip().splitlines()[0][:80]
        if not topic_preview:
            topic_preview = "исходную тему"
        return f"Кажется, мы отошли от темы «{topic_preview}». О чём именно ты сейчас спрашиваешь?"

    # ---------- Внутренности ----------

    def _coerce(self, m: ThreadMessage | str) -> ThreadMessage:
        if isinstance(m, ThreadMessage):
            return m
        return ThreadMessage(text=str(m or ""), author_id="anon")

    def _embed(self, text: str) -> np.ndarray:
        key = text or ""
        cached = self._cache.get(key)
        if cached is not None:
            self.cache_hits += 1
            # LRU bump.
            self._cache.move_to_end(key)
            return cached
        self.cache_misses += 1
        vec = self._embedder(key)
        self._cache[key] = vec
        if len(self._cache) > self.cache_size:
            self._cache.popitem(last=False)
        return vec

    def reset_cache(self) -> None:
        self._cache.clear()
        self.cache_hits = 0
        self.cache_misses = 0


# ---------- Module-level singleton ----------


thread_coherence_detector = ThreadCoherenceDetector()


# ---------- Удобные обёртки ----------


def score_thread_coherence(
    messages: Iterable[ThreadMessage | str],
    current_msg: ThreadMessage | str,
) -> ThreadCoherenceResult:
    """Шорткат к singleton-детектору."""
    return thread_coherence_detector.score_thread_coherence(list(messages), current_msg)


def should_break_context(
    score: float | ThreadCoherenceResult, threshold: float = DEFAULT_THRESHOLD
) -> bool:
    """Шорткат к singleton-детектору."""
    return thread_coherence_detector.should_break_context(score, threshold)


def format_break_notice(original_topic: str, current_msg: str) -> str:
    """Шорткат к singleton-детектору."""
    return thread_coherence_detector.format_break_notice(original_topic, current_msg)


def insert_break_notice_enabled() -> bool:
    """Включена ли реальная вставка break notice в ответ."""
    return _env_flag("KRAB_COHERENCE_INSERT_BREAK_NOTICE", False)
