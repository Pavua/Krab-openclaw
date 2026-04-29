# -*- coding: utf-8 -*-
"""Proactive Suggestions — детектор повторяющихся паттернов поведения owner'а.

Идея (Idea 32): Krab наблюдает за повторяющимися действиями (time-queries в
одну TZ, news-форварды, OCR-загрузки скриншотов, повторяющиеся calc/convert)
и proactively предлагает автоматизацию.

Этот модуль — pure detection. Wire-up (создание Inbox item / DM-промпт) —
backlog. Module экспортирует:

- `Suggestion` — dataclass: trigger_pattern, suggestion_text, action_type,
  confidence (0.0..1.0), evidence (chat_id, count, examples).
- `PatternDetector` — singleton с persisted sliding-window store.
  - `record_action(action_type, chat_id, owner_id, metadata)` — журналирует
    действие в JSON store.
  - `detect_patterns(min_count=3, window_hours=24)` — возвращает Suggestion
    для активных паттернов.

Storage: `~/.openclaw/krab_runtime_state/proactive_actions.json`.
Sliding window: при каждом record/detect отсекаем записи старше
`window_hours` (default 24h) — это и есть «expiry» без отдельного sweep.

### Built-in patterns

- **timezone_check**: 3+ time-запросов с одинаковой TZ из metadata['tz'].
- **news_forwards**: 5+ news-форвардов в DM (chat_id положительный, для
  upstream wire-up можно фильтровать на стороне caller'а).
- **screenshot_uploads**: 3+ screenshot-загрузок.
- **same_calc_query**: 3+ повтора одного и того же calc/convert
  (metadata['expression']).

### Не решает
- Не отправляет suggestions в чаты — только detection.
- Не дедуплицирует suggestions между запусками: если owner проигнорил,
  detector продолжит выдавать тот же Suggestion. Дедупликация — дело
  caller'а (Inbox / proactive_watch).

### Конфиг
- `KRAB_PROACTIVE_SUGGESTIONS_ENABLED` (default False) — флаг для caller'ов.
  Сам модуль работает всегда; флаг читать в wire-up точке.
"""

from __future__ import annotations

import json
import threading
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .logger import get_logger

logger = get_logger(__name__)

# Лимит записей в JSON store: при превышении — обрезаем старые. 10к достаточно
# для 24-72h окна даже у активного owner'а (~300-1000 событий/сутки).
_MAX_ACTIONS = 10_000

# Известные action_type для built-in detector'ов. Остальные тоже принимаются
# (для будущих паттернов), но built-in detect_patterns их не обработает.
ACTION_TIMEZONE_QUERY = "timezone_query"
ACTION_NEWS_FORWARD = "news_forward"
ACTION_SCREENSHOT_UPLOAD = "screenshot_upload"
ACTION_CALC_QUERY = "calc_query"


@dataclass(frozen=True)
class Suggestion:
    """Предложение автоматизации, рождённое из паттерна."""

    trigger_pattern: str
    suggestion_text: str
    action_type: str
    confidence: float
    evidence: dict[str, Any] = field(default_factory=dict)


class PatternDetector:
    """Sliding-window детектор повторяющихся действий owner'а.

    Используется как module-level singleton (`pattern_detector` ниже). Принимает
    `storage_path` в конструкторе — для тестов; в рантайме singleton конфигу-
    рируется через `configure_default_path()` из bootstrap.
    """

    def __init__(
        self,
        *,
        storage_path: Path | None = None,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self._storage_path: Path | None = storage_path
        # Список dict'ов: {action_type, chat_id, owner_id, ts, metadata}.
        self._actions: list[dict[str, Any]] = []
        self._now_fn: Callable[[], datetime] = now_fn or (lambda: datetime.now(timezone.utc))
        if storage_path is not None:
            self._load_from_disk()

    def _now(self) -> datetime:
        return self._now_fn()

    # ---- Configuration --------------------------------------------------

    def configure_default_path(self, storage_path: Path) -> None:
        """Устанавливает путь к JSON store и подгружает то что лежит на диске."""
        with self._lock:
            self._storage_path = storage_path
            self._actions = []
            self._load_from_disk()

    # ---- Core API -------------------------------------------------------

    def record_action(
        self,
        action_type: str,
        chat_id: Any,
        owner_id: Any,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Журналирует одно действие. Persist after each write — writes редкие."""
        normalized_action = str(action_type or "").strip()
        if not normalized_action:
            return
        entry = {
            "action_type": normalized_action,
            "chat_id": str(chat_id) if chat_id is not None else "",
            "owner_id": str(owner_id) if owner_id is not None else "",
            "ts": self._now().isoformat(),
            "metadata": dict(metadata or {}),
        }
        with self._lock:
            self._actions.append(entry)
            if len(self._actions) > _MAX_ACTIONS:
                # Cap: оставляем самые свежие. Sliding window и так подрезает,
                # но это страховка от бомбардировки record'ами.
                self._actions = self._actions[-_MAX_ACTIONS:]
            self._persist_to_disk()

    def detect_patterns(
        self,
        *,
        min_count: int = 3,
        window_hours: float = 24.0,
    ) -> list[Suggestion]:
        """Возвращает Suggestion'ы для активных паттернов.

        Sliding window: записи старше `window_hours` не учитываются и
        одновременно вычищаются из in-memory списка.
        """
        cutoff = self._now() - timedelta(hours=float(window_hours))
        with self._lock:
            self._actions = [a for a in self._actions if self._parse_ts(a.get("ts")) >= cutoff]
            self._persist_to_disk()
            actions_snapshot = [dict(a) for a in self._actions]

        suggestions: list[Suggestion] = []
        suggestions.extend(self._detect_timezone(actions_snapshot, min_count))
        suggestions.extend(self._detect_news_forwards(actions_snapshot, min_count))
        suggestions.extend(self._detect_screenshots(actions_snapshot, min_count))
        suggestions.extend(self._detect_calc(actions_snapshot, min_count))
        return suggestions

    def list_actions(self) -> list[dict[str, Any]]:
        """Снимок текущего списка действий — для UI / диагностики.

        Возвращает копии dict'ов, чтобы caller не мутировал внутреннее состояние.
        """
        with self._lock:
            return [dict(a) for a in self._actions]

    def clear(self) -> None:
        """Полностью очищает store (для тестов / owner reset)."""
        with self._lock:
            self._actions = []
            self._persist_to_disk()

    # ---- Built-in detectors ---------------------------------------------

    def _detect_timezone(self, actions: list[dict[str, Any]], min_count: int) -> list[Suggestion]:
        # Группируем по (owner_id, tz) — повторные запросы одной TZ
        # одним owner'ом → намёк на постоянную потребность в widget'е.
        counter: Counter[tuple[str, str]] = Counter()
        per_owner: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for a in actions:
            if a.get("action_type") != ACTION_TIMEZONE_QUERY:
                continue
            tz = str((a.get("metadata") or {}).get("tz") or "").strip()
            if not tz:
                continue
            key = (a.get("owner_id") or "", tz)
            counter[key] += 1
            per_owner[key].append(a)
        suggestions: list[Suggestion] = []
        for (owner_id, tz), count in counter.items():
            if count < min_count:
                continue
            suggestions.append(
                Suggestion(
                    trigger_pattern=f"timezone_query:{tz}",
                    suggestion_text=(
                        f"Замечаю что ты {count} раз спрашивал время в {tz}. "
                        f"Хочу настроить timezone widget в твой dashboard?"
                    ),
                    action_type="setup_timezone_widget",
                    confidence=min(1.0, 0.5 + 0.1 * (count - min_count + 1)),
                    evidence={
                        "owner_id": owner_id,
                        "tz": tz,
                        "count": count,
                        "examples": [a.get("ts") for a in per_owner[(owner_id, tz)][:5]],
                    },
                )
            )
        return suggestions

    def _detect_news_forwards(
        self, actions: list[dict[str, Any]], min_count: int
    ) -> list[Suggestion]:
        # news_forwards default threshold выше (5), потому что owner может
        # один раз форварднуть 3 ссылки подряд без намерения автоматизации.
        threshold = max(min_count, 5)
        per_owner: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for a in actions:
            if a.get("action_type") != ACTION_NEWS_FORWARD:
                continue
            per_owner[a.get("owner_id") or ""].append(a)
        suggestions: list[Suggestion] = []
        for owner_id, items in per_owner.items():
            if len(items) < threshold:
                continue
            suggestions.append(
                Suggestion(
                    trigger_pattern="news_forward",
                    suggestion_text=(
                        f"Ты пересылал новости {len(items)} раз за окно. "
                        f"Давай я буду саммаризировать новости автоматически?"
                    ),
                    action_type="enable_news_autosummary",
                    confidence=min(1.0, 0.5 + 0.05 * (len(items) - threshold + 1)),
                    evidence={
                        "owner_id": owner_id,
                        "count": len(items),
                        "chat_ids": sorted({i.get("chat_id") or "" for i in items}),
                    },
                )
            )
        return suggestions

    def _detect_screenshots(
        self, actions: list[dict[str, Any]], min_count: int
    ) -> list[Suggestion]:
        per_owner: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for a in actions:
            if a.get("action_type") != ACTION_SCREENSHOT_UPLOAD:
                continue
            per_owner[a.get("owner_id") or ""].append(a)
        suggestions: list[Suggestion] = []
        for owner_id, items in per_owner.items():
            if len(items) < min_count:
                continue
            suggestions.append(
                Suggestion(
                    trigger_pattern="screenshot_upload",
                    suggestion_text=(
                        f"Ты загрузил {len(items)} скриншотов. "
                        f"Хочу автоматически OCR'ить скриншоты?"
                    ),
                    action_type="enable_screenshot_ocr",
                    confidence=min(1.0, 0.5 + 0.1 * (len(items) - min_count + 1)),
                    evidence={
                        "owner_id": owner_id,
                        "count": len(items),
                    },
                )
            )
        return suggestions

    def _detect_calc(self, actions: list[dict[str, Any]], min_count: int) -> list[Suggestion]:
        # Группируем по (owner_id, expression) — буквальное повторение
        # одного и того же запроса означает, что owner запрашивает один и
        # тот же вычисленный результат и его стоит сохранить.
        counter: Counter[tuple[str, str]] = Counter()
        per_key: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for a in actions:
            if a.get("action_type") != ACTION_CALC_QUERY:
                continue
            expr = str((a.get("metadata") or {}).get("expression") or "").strip()
            if not expr:
                continue
            key = (a.get("owner_id") or "", expr)
            counter[key] += 1
            per_key[key].append(a)
        suggestions: list[Suggestion] = []
        for (owner_id, expr), count in counter.items():
            if count < min_count:
                continue
            suggestions.append(
                Suggestion(
                    trigger_pattern=f"calc_query:{expr}",
                    suggestion_text=(
                        f"Ты повторял запрос '{expr}' {count} раз. "
                        f"Хочу сохранить результат как pinned shortcut?"
                    ),
                    action_type="pin_calc_shortcut",
                    confidence=min(1.0, 0.5 + 0.1 * (count - min_count + 1)),
                    evidence={
                        "owner_id": owner_id,
                        "expression": expr,
                        "count": count,
                    },
                )
            )
        return suggestions

    # ---- Internal helpers -----------------------------------------------

    def _parse_ts(self, raw: Any) -> datetime:
        if not isinstance(raw, str):
            return datetime.min.replace(tzinfo=timezone.utc)
        try:
            return datetime.fromisoformat(raw)
        except (TypeError, ValueError):
            return datetime.min.replace(tzinfo=timezone.utc)

    def _load_from_disk(self) -> None:
        path = self._storage_path
        if path is None or not path.exists():
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8") or "[]")
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning(
                "proactive_suggestions_load_failed",
                path=str(path),
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return
        if not isinstance(raw, list):
            logger.warning("proactive_suggestions_load_malformed", path=str(path))
            return
        loaded = 0
        skipped = 0
        for item in raw:
            if not isinstance(item, dict):
                skipped += 1
                continue
            if not item.get("action_type") or not item.get("ts"):
                skipped += 1
                continue
            self._actions.append(dict(item))
            loaded += 1
        if loaded or skipped:
            logger.info(
                "proactive_suggestions_loaded",
                loaded=loaded,
                skipped=skipped,
            )

    def _persist_to_disk(self) -> None:
        path = self._storage_path
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(self._actions, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except (OSError, TypeError) as exc:
            logger.warning(
                "proactive_suggestions_persist_failed",
                path=str(path),
                error=str(exc),
                error_type=type(exc).__name__,
            )


# Module-level singleton — pattern совпадает с chat_ban_cache, silence_manager,
# inbox_service. Конкретный путь конфигурируется вызовом
# `pattern_detector.configure_default_path(...)` из bootstrap.
pattern_detector = PatternDetector()
