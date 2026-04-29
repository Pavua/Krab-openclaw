# -*- coding: utf-8 -*-
"""
Voice fingerprinting registry — Idea 36.

Хранит "отпечатки" известных голосов, чтобы в будущем по embedding из voice
message определять, кто говорил в групповом чате. Сам ML-инференс (resemblyzer
/ pyannote.audio / speechbrain ECAPA-TDNN) выносится в отдельный backlog —
он тащит heavy-зависимости (torch ~800 МБ), поэтому здесь только pure-Python
реестр + cosine similarity.

### Дизайн

- Embedding — список float'ов произвольной длины (типичный размер 192/256/512
  у resemblyzer/ECAPA). Длина проверяется только при сравнении: при `identify`
  разной размерности вернётся `(None, 0.0)`, без исключений.
- Embedding *опционален*. Можно зарегистрировать спикера только по `name` —
  тогда `identify()` для него никогда не сматчит, но он попадёт в
  `list_known_speakers()` как "known but no fingerprint".
- Persist в `~/.openclaw/krab_runtime_state/voice_fingerprints.json` — после
  каждого `register_speaker` / `forget_speaker` файл переписывается.
- Threshold по умолчанию 0.75 — типичный порог cosine для resemblyzer; при
  более точных моделях (ECAPA-TDNN) разумно поднимать до 0.80+.

### Что НЕ делает

- Не извлекает embedding из аудио. Это работа caller'а / отдельного pipeline.
- Не делает diarization (разделение голосов в одной записи).
- Не интегрирован в audio_summarizer / perceptor — это отдельный шаг
  backlog'а (нельзя трогать active scope в этой задаче).
"""

from __future__ import annotations

import json
import math
import threading
import time
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .logger import get_logger

logger = get_logger(__name__)

# Порог по умолчанию для cosine similarity. Подобран под resemblyzer (256-D
# embeddings); для ECAPA-TDNN можно поднимать до 0.80-0.82.
DEFAULT_MATCH_THRESHOLD: float = 0.75

# Минимальная норма embedding, чтобы считать его валидным. Embeddings из
# нормальных моделей всегда имеют норму >> 1e-6; всё что меньше — артефакт
# (нулевой вектор, ошибка инференса).
_MIN_EMBEDDING_NORM: float = 1e-6


def _cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity между двумя векторами равной длины.

    Возвращает 0.0 если длины не совпадают или один из векторов нулевой —
    это безопасный default для downstream `identify()` логики.
    """
    if len(a) != len(b) or not a:
        return 0.0
    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for x, y in zip(a, b, strict=False):
        fx = float(x)
        fy = float(y)
        dot += fx * fy
        norm_a += fx * fx
        norm_b += fy * fy
    if norm_a < _MIN_EMBEDDING_NORM or norm_b < _MIN_EMBEDDING_NORM:
        return 0.0
    return dot / (math.sqrt(norm_a) * math.sqrt(norm_b))


class VoiceFingerprintRegistry:
    """Реестр известных голосов с persist на диск.

    Используется как module-level singleton (`voice_fingerprint_registry`).
    Конструктор принимает `storage_path` и `now_fn` — оба только для тестов.
    В рантайме singleton конфигурируется через `configure_default_path()`
    из bootstrap (этот шаг вне scope текущей задачи — он идёт в backlog).
    """

    def __init__(
        self,
        *,
        storage_path: Path | None = None,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self._storage_path: Path | None = storage_path
        # user_id (str) → {"name": str, "embedding": list[float] | None,
        # "registered_at": iso, "updated_at": iso, "embedding_dim": int | None}
        self._speakers: dict[str, dict[str, Any]] = {}
        self._now_fn: Callable[[], datetime] = now_fn or (lambda: datetime.now(timezone.utc))
        if storage_path is not None:
            self._load_from_disk()

    # ---- Configuration --------------------------------------------------

    def configure_default_path(self, storage_path: Path) -> None:
        """Устанавливает путь к persisted JSON и подгружает данные с диска.

        Переинициализирует singleton — все in-memory записи затираются и
        заменяются содержимым файла. Вызывать один раз при bootstrap.
        """
        with self._lock:
            self._storage_path = storage_path
            self._speakers = {}
            self._load_from_disk()

    # ---- Public API -----------------------------------------------------

    def register_speaker(
        self,
        user_id: Any,
        name: str,
        *,
        voice_embedding: Sequence[float] | None = None,
    ) -> dict[str, Any]:
        """Регистрирует или обновляет известного спикера.

        Если запись уже есть — `name` обновляется, embedding пере-записывается
        только если передан явно (`None` оставляет старый отпечаток на месте).
        Это нужно чтобы редактировать имя без потери уже снятого embedding'а.

        Возвращает копию итоговой записи.
        """
        target = self._normalize_user_id(user_id)
        if not target:
            raise ValueError("user_id must be non-empty")
        clean_name = (name or "").strip()
        if not clean_name:
            raise ValueError("name must be non-empty")

        embedding_list: list[float] | None = None
        embedding_dim: int | None = None
        if voice_embedding is not None:
            embedding_list = [float(v) for v in voice_embedding]
            if not embedding_list:
                raise ValueError("voice_embedding must not be empty")
            embedding_dim = len(embedding_list)

        now_iso = self._now().isoformat()
        with self._lock:
            existing = self._speakers.get(target)
            if existing is None:
                entry: dict[str, Any] = {
                    "name": clean_name,
                    "embedding": embedding_list,
                    "embedding_dim": embedding_dim,
                    "registered_at": now_iso,
                    "updated_at": now_iso,
                }
                self._speakers[target] = entry
            else:
                existing["name"] = clean_name
                existing["updated_at"] = now_iso
                if embedding_list is not None:
                    existing["embedding"] = embedding_list
                    existing["embedding_dim"] = embedding_dim
                entry = existing
            self._persist_to_disk()
            snapshot = dict(entry)
        snapshot["user_id"] = target
        logger.info(
            "voice_fingerprint_registered",
            user_id=target,
            name=clean_name,
            has_embedding=embedding_list is not None,
            embedding_dim=embedding_dim,
        )
        return snapshot

    def identify(
        self,
        voice_embedding: Sequence[float],
        *,
        threshold: float = DEFAULT_MATCH_THRESHOLD,
    ) -> tuple[str | None, float]:
        """Сопоставляет embedding с известными спикерами.

        Возвращает `(user_id, confidence)` лучшего матча или `(None, 0.0)`
        если ни одного спикера выше порога не нашлось / эмбеддинг пуст /
        ни у одного зарегистрированного спикера embedding не записан.
        Confidence — это cosine similarity (диапазон [-1, 1], для адекватных
        embeddings почти всегда [0, 1]).
        """
        if not voice_embedding:
            return None, 0.0
        try:
            probe = [float(v) for v in voice_embedding]
        except (TypeError, ValueError):
            logger.warning("voice_fingerprint_identify_invalid_embedding")
            return None, 0.0

        best_user: str | None = None
        best_score: float = 0.0
        with self._lock:
            for user_id, entry in self._speakers.items():
                stored = entry.get("embedding")
                if not stored:
                    continue
                score = _cosine_similarity(probe, stored)
                if score > best_score:
                    best_score = score
                    best_user = user_id
        if best_user is not None and best_score >= threshold:
            logger.debug(
                "voice_fingerprint_match",
                user_id=best_user,
                confidence=round(best_score, 4),
                threshold=threshold,
            )
            return best_user, best_score
        return None, best_score

    def list_known_speakers(self) -> list[dict[str, Any]]:
        """Снимок зарегистрированных спикеров (без сырого embedding).

        Embedding не возвращаем целиком — он шумный и большой; вместо этого
        отдаём `embedding_dim` и `has_embedding`, чего достаточно для UI.
        """
        with self._lock:
            result: list[dict[str, Any]] = []
            for user_id, entry in self._speakers.items():
                result.append(
                    {
                        "user_id": user_id,
                        "name": entry.get("name", ""),
                        "has_embedding": bool(entry.get("embedding")),
                        "embedding_dim": entry.get("embedding_dim"),
                        "registered_at": entry.get("registered_at"),
                        "updated_at": entry.get("updated_at"),
                    }
                )
        return result

    def forget_speaker(self, user_id: Any) -> bool:
        """Удаляет спикера. True — если запись была."""
        target = self._normalize_user_id(user_id)
        if not target:
            return False
        with self._lock:
            if target not in self._speakers:
                return False
            del self._speakers[target]
            self._persist_to_disk()
        logger.info("voice_fingerprint_forgotten", user_id=target)
        return True

    # ---- Internal helpers -----------------------------------------------

    def _now(self) -> datetime:
        return self._now_fn()

    @staticmethod
    def _normalize_user_id(user_id: Any) -> str:
        return str(user_id or "").strip()

    def _load_from_disk(self) -> None:
        path = self._storage_path
        if path is None or not path.exists():
            return
        t0 = time.monotonic()
        try:
            raw = json.loads(path.read_text(encoding="utf-8") or "{}")
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning(
                "voice_fingerprint_load_failed",
                path=str(path),
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return
        if not isinstance(raw, dict):
            logger.warning("voice_fingerprint_load_malformed", path=str(path))
            return
        loaded = 0
        skipped = 0
        for key, value in raw.items():
            if not isinstance(value, dict):
                skipped += 1
                continue
            embedding = value.get("embedding")
            if embedding is not None and not isinstance(embedding, list):
                skipped += 1
                continue
            self._speakers[str(key)] = dict(value)
            loaded += 1
        elapsed_ms = round((time.monotonic() - t0) * 1000, 1)
        if loaded or skipped:
            logger.info(
                "voice_fingerprint_loaded",
                loaded=loaded,
                skipped=skipped,
                elapsed_ms=elapsed_ms,
            )

    def _persist_to_disk(self) -> None:
        path = self._storage_path
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(self._speakers, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except (OSError, TypeError) as exc:
            logger.warning(
                "voice_fingerprint_persist_failed",
                path=str(path),
                error=str(exc),
                error_type=type(exc).__name__,
            )


# Module-level singleton — конфигурируется через configure_default_path()
# из bootstrap userbot (вне scope текущей задачи).
voice_fingerprint_registry = VoiceFingerprintRegistry()
