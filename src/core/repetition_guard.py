# -*- coding: utf-8 -*-
"""Wave 39-A: RepetitionGuard — защита от повторных близких ответов.

Контекст: 09.05.2026 Krab ответил 3 раза подряд почти идентично в чате
YMB FAMILY FOREVER (msg 767211→767223). Нужен быстрый in-memory guard
без эмбеддингов.

Алгоритм схожести: token Jaccard.
Почему именно Jaccard:
- Детерминированный и мгновенный (без IO/network).
- Хорошо работает на natural language: два ответа «Всё хорошо, не беспокойся»
  и «Не беспокойся, всё хорошо» → Jaccard ≈ 1.0 (bag-of-words).
- Не боится порядка слов (в отличие от Levenshtein).
- Стоп-слова и эмодзи убираются до расчёта — не влияют на similarity score.
- Threshold 0.6 эмпирически: перефраз ловится, но разные темы пропускаются.
"""

from __future__ import annotations

import time
from collections import deque
from typing import Sequence

import structlog

logger = structlog.get_logger("Krab.core.repetition_guard")

# Стоп-слова (RU + EN) + служебные токены Краба — не несут семантики,
# поэтому исключаем из расчёта Jaccard similarity.
_STOP_TOKENS: frozenset[str] = frozenset(
    {
        # RU предлоги / союзы / частицы
        "и",
        "в",
        "на",
        "с",
        "к",
        "у",
        "о",
        "по",
        "из",
        "за",
        "для",
        "не",
        "но",
        "да",
        "то",
        "же",
        "ли",
        "бы",
        "а",
        "как",
        "так",
        "что",
        "это",
        "все",
        "всё",
        "от",
        "до",
        "об",
        "при",
        "или",
        "уже",
        "ещё",
        "еще",
        "вот",
        "тут",
        "там",
        "здесь",
        # EN stop words
        "the",
        "a",
        "an",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "to",
        "of",
        "in",
        "on",
        "at",
        "for",
        "with",
        "and",
        "or",
        "but",
        "not",
        "it",
        "this",
        "that",
        "i",
        "you",
        "we",
        "they",
        # Служебные токены Краба
        "🦀",
        "—",
        "-",
        "...",
        "·",
    }
)

# Максимальное количество хранимых ответов на чат (FIFO eviction)
_DEFAULT_MAX_SIZE: int = 5


def _tokenize(text: str) -> frozenset[str]:
    """Токенизация: lowercase, split по пробелам, убираем стоп-слова и пустые."""
    tokens = text.lower().split()
    return frozenset(t for t in tokens if t and t not in _STOP_TOKENS)


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    """Коэффициент Жаккара для двух множеств токенов."""
    if not a and not b:
        return 1.0  # оба пустые → идентичны
    union_size = len(a | b)
    if union_size == 0:
        return 0.0
    return len(a & b) / union_size


class RepetitionGuard:
    """In-memory guard от повторных похожих ответов Краба.

    Хранит последние N ответов на чат с временными метками.
    is_repetition() проверяет, не является ли кандидат-ответ слишком
    похожим на недавно доставленный ответ в том же чате.
    """

    def __init__(self, max_size: int = _DEFAULT_MAX_SIZE) -> None:
        self._max_size = max_size
        # chat_id (str) → deque[(tokens: frozenset, ts: float)]
        self._store: dict[str, deque[tuple[frozenset[str], float]]] = {}

    def record(
        self,
        chat_id: int | str,
        text: str,
        ts: float | None = None,
    ) -> None:
        """Сохраняет ответ text для chat_id с временной меткой ts (default now).

        FIFO eviction: при превышении max_size удаляется самый старый.
        """
        cid = str(chat_id)
        if cid not in self._store:
            self._store[cid] = deque(maxlen=self._max_size)
        tokens = _tokenize(text)
        ts_val = ts if ts is not None else time.monotonic()
        self._store[cid].append((tokens, ts_val))

    def is_repetition(
        self,
        chat_id: int | str,
        candidate: str,
        *,
        threshold: float = 0.6,
        window_sec: int = 600,
    ) -> bool:
        """True если candidate похож (Jaccard >= threshold) на любой недавний ответ.

        «Недавний» — записан не позже window_sec секунд назад.
        Пустой store → False (безопасно пропускает первый ответ).
        """
        cid = str(chat_id)
        history = self._store.get(cid)
        if not history:
            return False

        cand_tokens = _tokenize(candidate)
        now = time.monotonic()
        cutoff = now - window_sec

        for stored_tokens, ts in history:
            if ts < cutoff:
                # Запись вышла за пределы окна — пропускаем
                continue
            sim = _jaccard(cand_tokens, stored_tokens)
            if sim >= threshold:
                logger.debug(
                    "repetition_detected",
                    chat_id=cid,
                    similarity=round(sim, 3),
                    threshold=threshold,
                )
                return True
        return False

    def recent_entries(
        self, chat_id: int | str, *, window_sec: int = 600
    ) -> Sequence[tuple[frozenset[str], float]]:
        """Для тестов: список (tokens, ts) в пределах окна."""
        cid = str(chat_id)
        history = self._store.get(cid)
        if not history:
            return []
        now = time.monotonic()
        cutoff = now - window_sec
        return [(tok, ts) for tok, ts in history if ts >= cutoff]


# Singleton — используется из delivery_helpers
repetition_guard = RepetitionGuard()
