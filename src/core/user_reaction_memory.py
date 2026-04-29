# -*- coding: utf-8 -*-
"""
UserReactionStore — per-user reaction memory для Smart Routing precision.

Дополняет per-chat policy (`chat_response_policy.py`) индивидуальной статистикой
по `user_id`. Некоторые пользователи реагируют позитивно (любят Краба) — для них
снижаем порог. Другие негативно (раздражает) — повышаем.

Storage: JSON, thread-safe atomic writes,
~/.openclaw/krab_runtime_state/user_reaction_memory.json.

Threshold modifier:
  - net negative (negative >= 3, positive == 0) → +0.2 (труднее триггернуть)
  - net positive (positive >= 3, negative <= positive // 2) → -0.15 (легче)
  - иначе → 0.0 (нейтрал)

См. Feature B (Session 28+) — расширение Smart Routing per-user.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

_STORE_PATH = Path.home() / ".openclaw" / "krab_runtime_state" / "user_reaction_memory.json"

# Пороги классификации пользователя
_NEGATIVE_MIN_COUNT = 3
_POSITIVE_MIN_COUNT = 3

# Threshold modifiers
_NEGATIVE_MODIFIER = 0.2
_POSITIVE_MODIFIER = -0.15


@dataclass
class UserReactionRecord:
    """Запись по одному user_id."""

    user_id: str
    positive_count: int = 0
    negative_count: int = 0
    neutral_count: int = 0
    last_updated_at: float = field(default_factory=time.time)
    preferred_response_style: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "UserReactionRecord":
        return cls(
            user_id=str(data["user_id"]),
            positive_count=int(data.get("positive_count", 0)),
            negative_count=int(data.get("negative_count", 0)),
            neutral_count=int(data.get("neutral_count", 0)),
            last_updated_at=float(data.get("last_updated_at") or time.time()),
            preferred_response_style=data.get("preferred_response_style"),
        )

    def classify(self) -> str:
        """Классификация пользователя: 'negative' | 'positive' | 'neutral'."""
        # Чёткий negative: накопил >=3 negative и нет positive
        if self.negative_count >= _NEGATIVE_MIN_COUNT and self.positive_count == 0:
            return "negative"
        # Чёткий positive: >=3 positive и negative не превышает половины positive
        if (
            self.positive_count >= _POSITIVE_MIN_COUNT
            and self.negative_count <= self.positive_count // 2
        ):
            return "positive"
        return "neutral"


class UserReactionStore:
    """Thread-safe JSON-backed store per-user reaction memory."""

    def __init__(self, path: Path = _STORE_PATH):
        self._path = Path(path)
        self._lock = threading.RLock()
        self._cache: dict[str, UserReactionRecord] = {}
        self._load()

    # ── Persistence ───────────────────────────────────────────

    def _load(self) -> None:
        with self._lock:
            self._cache.clear()
            if not self._path.exists():
                return
            try:
                raw = json.loads(self._path.read_text())
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(
                    "user_reaction_memory_load_failed",
                    error=str(e),
                    error_type=type(e).__name__,
                )
                return
            if not isinstance(raw, dict):
                logger.warning("user_reaction_memory_invalid_format")
                return
            users = raw.get("users")
            if not isinstance(users, dict):
                return
            for user_id, payload in users.items():
                if not isinstance(payload, dict):
                    continue
                payload = {**payload, "user_id": str(user_id)}
                try:
                    record = UserReactionRecord.from_dict(payload)
                    self._cache[record.user_id] = record
                except (KeyError, ValueError, TypeError) as e:
                    logger.warning(
                        "user_reaction_memory_decode_failed",
                        user_id=user_id,
                        error=str(e),
                        error_type=type(e).__name__,
                    )

    def _persist(self) -> None:
        """Atomic write через .tmp + replace под self._lock."""
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "users": {uid: rec.to_dict() for uid, rec in self._cache.items()},
            }
            tmp = self._path.with_suffix(self._path.suffix + ".tmp")
            try:
                tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
                tmp.replace(self._path)
            except OSError as e:
                logger.warning(
                    "user_reaction_memory_save_failed",
                    error=str(e),
                    error_type=type(e).__name__,
                )
                try:
                    if tmp.exists():
                        tmp.unlink()
                except OSError:
                    pass

    # ── Public API ────────────────────────────────────────────

    def get_record(self, user_id: str | int) -> UserReactionRecord:
        """Вернуть копию записи (или дефолтную если нет)."""
        uid = str(user_id)
        with self._lock:
            record = self._cache.get(uid)
            if record is None:
                return UserReactionRecord(user_id=uid)
            # Возвращаем копию, чтобы внешний код не мог мутировать кэш
            return UserReactionRecord.from_dict(record.to_dict())

    def record_positive(self, user_id: str | int) -> UserReactionRecord:
        return self._increment(user_id, "positive_count")

    def record_negative(self, user_id: str | int) -> UserReactionRecord:
        return self._increment(user_id, "negative_count")

    def record_neutral(self, user_id: str | int) -> UserReactionRecord:
        return self._increment(user_id, "neutral_count")

    def _increment(self, user_id: str | int, field_name: str) -> UserReactionRecord:
        uid = str(user_id)
        now = time.time()
        with self._lock:
            record = self._cache.get(uid) or UserReactionRecord(user_id=uid)
            current = getattr(record, field_name, 0)
            setattr(record, field_name, current + 1)
            record.last_updated_at = now
            self._cache[uid] = record
            self._persist()
            logger.info(
                "user_reaction_recorded",
                user_id=uid,
                field=field_name,
                positive=record.positive_count,
                negative=record.negative_count,
                neutral=record.neutral_count,
                classification=record.classify(),
            )
            # Возвращаем копию
            return UserReactionRecord.from_dict(record.to_dict())

    def get_threshold_modifier(self, user_id: str | int | None) -> float:
        """Threshold modifier для smart trigger.

        Returns:
            +0.2 если user классифицирован negative (труднее триггернуть)
            -0.15 если positive (легче)
            0.0 для neutral / unknown / None
        """
        if user_id is None:
            return 0.0
        uid = str(user_id)
        with self._lock:
            record = self._cache.get(uid)
            if record is None:
                return 0.0
            cls = record.classify()
        if cls == "negative":
            return _NEGATIVE_MODIFIER
        if cls == "positive":
            return _POSITIVE_MODIFIER
        return 0.0

    def list_all(self) -> list[UserReactionRecord]:
        """Снимок всех записей (копии)."""
        with self._lock:
            return [
                UserReactionRecord.from_dict(rec.to_dict())
                for rec in sorted(self._cache.values(), key=lambda r: r.user_id)
            ]

    def reset_user(self, user_id: str | int) -> bool:
        uid = str(user_id)
        with self._lock:
            existed = uid in self._cache
            self._cache.pop(uid, None)
            if existed:
                self._persist()
                logger.info("user_reaction_memory_reset", user_id=uid)
            return existed


# Singleton (lazy)
_singleton: UserReactionStore | None = None


def get_store() -> UserReactionStore:
    global _singleton
    if _singleton is None:
        _singleton = UserReactionStore()
    return _singleton


def reset_store_for_tests() -> None:
    """Test-only helper для сброса singleton между тестами."""
    global _singleton
    _singleton = None
