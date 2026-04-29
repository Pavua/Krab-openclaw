# -*- coding: utf-8 -*-
"""
Joke Calibration — учёт реакций на шутки Краба per chat (Idea 33).

Зачем это существует:

Чувство юмора у разных групп радикально различается. В одном чате smile/laugh
реакции на каламбур, в другом — те же реакции читаются как cringe и шутка
удаляется. Без feedback-loop Краб генерит «средний» юмор и одинаково раздражает
половину аудитории.

Решение: фиксируем исход каждой шутки (positive / neutral / negative) per chat,
агрегируем simple ratio (positive / total) и подмешиваем humor_score обратно
в system prompt, чтобы LLM мог сам решать — шутить ли тут вообще.

### Сигналы (определяет caller, не модуль)
- positive: реакции 😂 / ❤️ / 🔥, явный «lol/хаха/смешно» в reply
- negative: удаление шутки оператором, реакция 👎/💩, reply типа «не смешно»
- neutral: всё остальное (нет реакций, проигнорировали)

### Инварианты
- Per chat изоляция — score одного чата не влияет на другой.
- Идемпотентный persist: после каждого record файл переписывается.
- История ограничена `_MAX_HISTORY_PER_CHAT` (последние 50 шуток на чат) — для
  отладки и будущей детальной аналитики, score считается по агрегатам.
- Lazy expiry не нужен (записи не истекают; чат либо живой, либо удалят руками).

### Не решает
- Не классифицирует «что именно зашло» (тип шутки, тема). Это будущая работа.
- Не определяет автоматически positive/negative из реакций — caller обязан
  передать уже размеченный сигнал. Маппинг emoji→sentiment живёт в reaction
  pipeline, не здесь.
- Не wired в активный pipeline. Включение под флагом `KRAB_JOKE_CALIBRATION_ENABLED`
  (default False) — само по себе hooks нет, это backlog.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from .logger import get_logger

logger = get_logger(__name__)

JokeReaction = Literal["positive", "neutral", "negative"]

_VALID_REACTIONS: frozenset[str] = frozenset({"positive", "neutral", "negative"})

# Сколько последних шуток на чат хранить в истории.
# Score считается по counters, история — для дебага/будущей аналитики.
_MAX_HISTORY_PER_CHAT: int = 50

# Default threshold для should_attempt_humor: если ratio positive/total >= 0.5,
# юмор «работает» в этом чате.
_DEFAULT_HUMOR_THRESHOLD: float = 0.5

# Минимум записей чтобы score считался статистически значимым.
# Меньше — возвращаем нейтральное значение и не препятствуем.
_MIN_SAMPLES_FOR_SCORE: int = 3


class JokeCalibrationStore:
    """Per-chat учёт исходов шуток с persist в JSON.

    Используется как module-level singleton (`joke_calibration_store`). Принимает
    `storage_path` в конструкторе ТОЛЬКО для unit-тестов; в рантайме singleton
    инициализируется через `configure_default_path()` из bootstrap (когда/если
    feature будет wired).
    """

    def __init__(
        self,
        *,
        storage_path: Path | None = None,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self._storage_path: Path | None = storage_path
        # chat_id (str) → {"positive": int, "neutral": int, "negative": int,
        #                  "history": [{"ts": iso, "reaction": str, "joke": str}, ...]}
        self._entries: dict[str, dict[str, Any]] = {}
        self._now_fn: Callable[[], datetime] = now_fn or (lambda: datetime.now(timezone.utc))
        if storage_path is not None:
            self._load_from_disk()

    def _now(self) -> datetime:
        return self._now_fn()

    # ---- Configuration --------------------------------------------------

    def configure_default_path(self, storage_path: Path) -> None:
        """Устанавливает путь к persisted JSON и подгружает то что лежит на диске."""
        with self._lock:
            self._storage_path = storage_path
            self._entries = {}
            self._load_from_disk()

    # ---- Core API -------------------------------------------------------

    def record_joke(
        self,
        chat_id: Any,
        joke_text: str,
        reaction: JokeReaction,
    ) -> None:
        """Фиксирует исход одной шутки в указанном чате.

        `reaction` обязан быть одним из 'positive' / 'neutral' / 'negative' —
        невалидное значение игнорируется с warning'ом, чтобы битый caller не
        ронял hot path.
        """
        target = self._normalize(chat_id)
        if not target:
            return
        if reaction not in _VALID_REACTIONS:
            logger.warning(
                "joke_calibration_invalid_reaction",
                chat_id=target,
                reaction=str(reaction),
            )
            return
        text = (joke_text or "").strip()
        # Урезаем хранимый текст шутки чтобы файл не разрастался от длинных
        # генераций. 280 — telegram-ишь tweet limit, более чем достаточно.
        if len(text) > 280:
            text = text[:277] + "..."
        now_iso = self._now().isoformat()

        with self._lock:
            entry = self._entries.get(target)
            if entry is None:
                entry = {
                    "positive": 0,
                    "neutral": 0,
                    "negative": 0,
                    "history": [],
                    "first_seen_at": now_iso,
                }
                self._entries[target] = entry
            entry[reaction] = int(entry.get(reaction) or 0) + 1
            entry["last_updated_at"] = now_iso
            history = entry.setdefault("history", [])
            history.append({"ts": now_iso, "reaction": reaction, "joke": text})
            # Trim history — храним только последние N штук на чат.
            if len(history) > _MAX_HISTORY_PER_CHAT:
                del history[: len(history) - _MAX_HISTORY_PER_CHAT]
            self._persist_to_disk()
        logger.info(
            "joke_calibration_recorded",
            chat_id=target,
            reaction=reaction,
            joke_length=len(text),
        )

    def chat_humor_score(self, chat_id: Any) -> float:
        """Возвращает score [0..1] = positive / (positive + negative).

        Neutral исключаются из знаменателя — нет смысла наказывать за «нет
        реакции» (могут просто не видеть). Считаем только активный feedback.

        Если активного feedback вообще нет (только neutral или нет записей) —
        возвращаем 0.5 как нейтральный prior, чтобы caller не блокировал юмор
        преждевременно. То же если total < `_MIN_SAMPLES_FOR_SCORE`.
        """
        target = self._normalize(chat_id)
        if not target:
            return 0.5
        with self._lock:
            entry = self._entries.get(target)
            if entry is None:
                return 0.5
            pos = int(entry.get("positive") or 0)
            neg = int(entry.get("negative") or 0)
            active_total = pos + neg
            if active_total < _MIN_SAMPLES_FOR_SCORE:
                return 0.5
            return pos / active_total

    def should_attempt_humor(
        self,
        chat_id: Any,
        threshold: float = _DEFAULT_HUMOR_THRESHOLD,
    ) -> bool:
        """True если в чате юмор «заходит» с вероятностью >= threshold."""
        return self.chat_humor_score(chat_id) >= threshold

    def format_humor_advice_for_prompt(self, chat_id: Any) -> str:
        """Возвращает короткую строку для system prompt suffix.

        Caller сам решает, добавлять ли это к prompt'у. Идея — дать LLM
        дополнительный контекст «в этой группе твой юмор раньше не заходил,
        будь сдержанней» без жёстких правил.
        """
        target = self._normalize(chat_id)
        if not target:
            return ""
        with self._lock:
            entry = self._entries.get(target)
            if entry is None:
                return ""
            pos = int(entry.get("positive") or 0)
            neg = int(entry.get("negative") or 0)
            neu = int(entry.get("neutral") or 0)
            active_total = pos + neg
            if active_total < _MIN_SAMPLES_FOR_SCORE:
                return (
                    f"[humor calibration] недостаточно сигналов для этого чата "
                    f"(positive={pos}, negative={neg}, neutral={neu}); "
                    "веди себя нейтрально."
                )
            score = pos / active_total
        if score >= 0.75:
            tone = "юмор хорошо заходит — можешь шутить смелее"
        elif score >= 0.5:
            tone = "юмор воспринимается умеренно — шути в меру"
        elif score >= 0.25:
            tone = "юмор скорее не заходит — будь сдержанным"
        else:
            tone = "юмор регулярно проваливается — лучше не шутить вовсе"
        return (
            f"[humor calibration] score={score:.2f} "
            f"(positive={pos}/negative={neg}/neutral={neu}); {tone}."
        )

    def get_stats(self, chat_id: Any) -> dict[str, Any] | None:
        """Снимок статистики по чату для owner UI / отладки.

        Возвращает копию счётчиков, не сам entry — чтобы caller не мутировал
        внутреннее состояние.
        """
        target = self._normalize(chat_id)
        if not target:
            return None
        with self._lock:
            entry = self._entries.get(target)
            if entry is None:
                return None
            pos = int(entry.get("positive") or 0)
            neg = int(entry.get("negative") or 0)
            neu = int(entry.get("neutral") or 0)
            return {
                "chat_id": target,
                "positive": pos,
                "neutral": neu,
                "negative": neg,
                "total": pos + neg + neu,
                "score": self.chat_humor_score(target),
                "first_seen_at": entry.get("first_seen_at"),
                "last_updated_at": entry.get("last_updated_at"),
            }

    def list_chats(self) -> list[dict[str, Any]]:
        """Снимок по всем чатам — для dashboard."""
        with self._lock:
            chat_ids = list(self._entries.keys())
        result: list[dict[str, Any]] = []
        for chat_id in chat_ids:
            stats = self.get_stats(chat_id)
            if stats is not None:
                result.append(stats)
        return result

    def clear(self, chat_id: Any) -> bool:
        """Удаляет всю историю по чату. Возвращает True если запись была."""
        target = self._normalize(chat_id)
        if not target:
            return False
        with self._lock:
            if target not in self._entries:
                return False
            del self._entries[target]
            self._persist_to_disk()
        logger.info("joke_calibration_cleared", chat_id=target)
        return True

    # ---- Internal helpers -----------------------------------------------

    @staticmethod
    def _normalize(chat_id: Any) -> str:
        return str(chat_id or "").strip()

    def _load_from_disk(self) -> None:
        path = self._storage_path
        if path is None or not path.exists():
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8") or "{}")
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning(
                "joke_calibration_load_failed",
                path=str(path),
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return
        if not isinstance(raw, dict):
            logger.warning("joke_calibration_load_malformed", path=str(path))
            return
        loaded = 0
        skipped = 0
        for key, value in raw.items():
            if not isinstance(value, dict):
                skipped += 1
                continue
            self._entries[str(key)] = dict(value)
            loaded += 1
        if loaded or skipped:
            logger.info("joke_calibration_loaded", loaded=loaded, skipped=skipped)

    def _persist_to_disk(self) -> None:
        path = self._storage_path
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(self._entries, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except (OSError, TypeError) as exc:
            logger.warning(
                "joke_calibration_persist_failed",
                path=str(path),
                error=str(exc),
                error_type=type(exc).__name__,
            )


# Module-level singleton. Конкретный путь конфигурируется вызовом
# joke_calibration_store.configure_default_path(...) из bootstrap (когда/если
# feature будет включена через KRAB_JOKE_CALIBRATION_ENABLED).
joke_calibration_store = JokeCalibrationStore()
