"""
Feature J — Session Goal Tracking.

Krab вытаскивает текущие проекты/цели owner'а из conversation patterns
и подмешивает их в system prompt при retrieval'е, чтобы приоритезировать
related queries.

Workflow:
  1. Каждые N сообщений в чате (`should_refresh()` решает) собираем последние
     ~50 сообщений и шлём в дешёвую LLM с промптом
     "Какие 1-3 текущих проекта/цели у owner'а в этом чате? JSON: …".
  2. Парсим ответ → list[Goal]; кэшируем per-chat (TTL 24h).
  3. На retrieval-time `system_prompt_suffix(chat_id)` возвращает форматированный
     суффикс: «Активные goals owner'а: X, Y…».

Persist: `~/.openclaw/krab_runtime_state/session_goals.json`.
LLM-вызов абстрагирован через callable `analyzer_fn(prompt, messages)` —
тесты подсовывают fake без походов в сеть.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

# TTL свежести goals (после — пора пересчитать).
_DEFAULT_TTL_HOURS = 24
# Минимум сообщений между refresh'ами в одном чате.
_DEFAULT_REFRESH_EVERY = 50
# Сколько последних сообщений отдаём LLM на анализ.
_DEFAULT_ANALYSIS_WINDOW = 50

_DEFAULT_STORE_PATH = Path("~/.openclaw/krab_runtime_state/session_goals.json").expanduser()


# ---------------------------------------------------------------------------
# Модель данных.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Goal:
    """Одна цель/проект owner'а."""

    name: str
    evidence: str
    confidence: float

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "evidence": self.evidence, "confidence": self.confidence}

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Goal":
        return cls(
            name=str(payload.get("name", "")).strip(),
            evidence=str(payload.get("evidence", "")).strip(),
            confidence=float(payload.get("confidence", 0.0)),
        )


@dataclass
class _ChatState:
    """Состояние per-chat: goals + счётчик сообщений + timestamp."""

    goals: list[Goal] = field(default_factory=list)
    message_count_at_refresh: int = 0
    refreshed_at: str | None = None  # ISO-8601 UTC
    total_messages_seen: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "goals": [g.to_dict() for g in self.goals],
            "message_count_at_refresh": self.message_count_at_refresh,
            "refreshed_at": self.refreshed_at,
            "total_messages_seen": self.total_messages_seen,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "_ChatState":
        return cls(
            goals=[Goal.from_dict(g) for g in payload.get("goals", []) if isinstance(g, dict)],
            message_count_at_refresh=int(payload.get("message_count_at_refresh", 0)),
            refreshed_at=payload.get("refreshed_at"),
            total_messages_seen=int(payload.get("total_messages_seen", 0)),
        )


# ---------------------------------------------------------------------------
# LLM analyzer.
# ---------------------------------------------------------------------------


# Сигнатура анализатора: принимает (chat_id, recent_messages) → list[Goal] (async).
AnalyzerFn = Callable[[str, list[str]], Awaitable[list[Goal]]]


_DEFAULT_PROMPT = (
    "Проанализируй последние сообщения owner'а в этом чате. "
    "Определи 1-3 текущих проекта/цели/задачи, которыми он сейчас занят. "
    "Верни строго JSON-массив объектов вида: "
    '[{"name": "...", "evidence": "...", "confidence": 0.0..1.0}]. '
    "Если явных проектов нет — верни []."
)


def parse_goals_response(raw: str) -> list[Goal]:
    """Парсит JSON-ответ LLM в list[Goal]. Терпим к мусору вокруг JSON."""
    if not raw:
        return []
    text = raw.strip()
    # Иногда модель оборачивает в ```json ... ```
    if text.startswith("```"):
        # уберём всё до первого '[' и всё после последнего ']'
        start = text.find("[")
        end = text.rfind("]")
        if start >= 0 and end > start:
            text = text[start : end + 1]
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        # last-resort: ищем массив руками
        start = text.find("[")
        end = text.rfind("]")
        if start < 0 or end <= start:
            return []
        try:
            payload = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return []
    if not isinstance(payload, list):
        return []
    out: list[Goal] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        if not name:
            continue
        try:
            conf = float(item.get("confidence", 0.0))
        except (TypeError, ValueError):
            conf = 0.0
        if conf < 0.0:
            conf = 0.0
        elif conf > 1.0:
            conf = 1.0
        out.append(Goal(name=name, evidence=str(item.get("evidence", "")).strip(), confidence=conf))
    return out


# ---------------------------------------------------------------------------
# Tracker.
# ---------------------------------------------------------------------------


class GoalTracker:
    """Per-chat session goals: refresh, persist, lookup.

    Не делает походов в LLM сам — `analyzer_fn` инъектируется, так удобнее
    для тестирования и для подмены модели. Persist в JSON-store.
    """

    def __init__(
        self,
        *,
        storage_path: Path | None = None,
        refresh_every: int = _DEFAULT_REFRESH_EVERY,
        ttl_hours: int = _DEFAULT_TTL_HOURS,
        analysis_window: int = _DEFAULT_ANALYSIS_WINDOW,
        analyzer_fn: AnalyzerFn | None = None,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self._storage_path = storage_path or _DEFAULT_STORE_PATH
        self._refresh_every = max(1, int(refresh_every))
        self._ttl_hours = max(1, int(ttl_hours))
        self._analysis_window = max(5, int(analysis_window))
        self._analyzer_fn = analyzer_fn
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))
        self._states: dict[str, _ChatState] = {}
        self._loaded = False
        self._lock = asyncio.Lock()

    # ---------- persistence --------------------------------------------

    def _load_from_disk(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if not self._storage_path.exists():
            return
        try:
            raw = self._storage_path.read_text(encoding="utf-8")
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                return
            for chat_id, state_dict in payload.items():
                if isinstance(state_dict, dict):
                    self._states[str(chat_id)] = _ChatState.from_dict(state_dict)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning(
                "session_goals_load_failed error=%s error_type=%s",
                exc,
                type(exc).__name__,
            )

    def _persist_to_disk(self) -> None:
        try:
            self._storage_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {chat_id: state.to_dict() for chat_id, state in self._states.items()}
            self._storage_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            logger.warning(
                "session_goals_persist_failed error=%s error_type=%s",
                exc,
                type(exc).__name__,
            )

    # ---------- core API ------------------------------------------------

    def configure_analyzer(self, analyzer_fn: AnalyzerFn) -> None:
        """Поздняя инжекция analyzer'а (после bootstrap'а, когда LLM готова)."""
        self._analyzer_fn = analyzer_fn

    def note_message(self, chat_id: str) -> bool:
        """Регистрирует новое сообщение в чате. Возвращает True если пора refresh.

        Сам не вызывает LLM — caller решает, делать ли await refresh().
        """
        self._load_from_disk()
        cid = str(chat_id)
        state = self._states.setdefault(cid, _ChatState())
        state.total_messages_seen += 1
        return self.should_refresh(cid)

    def should_refresh(self, chat_id: str) -> bool:
        """True если надо пересчитать goals (по счётчику или TTL)."""
        self._load_from_disk()
        cid = str(chat_id)
        state = self._states.get(cid)
        if state is None or not state.goals:
            return True
        # Проверка TTL.
        if state.refreshed_at:
            try:
                refreshed = datetime.fromisoformat(state.refreshed_at.replace("Z", "+00:00"))
                if self._now_fn() - refreshed > timedelta(hours=self._ttl_hours):
                    return True
            except ValueError:
                return True
        # Проверка счётчика.
        delta = state.total_messages_seen - state.message_count_at_refresh
        return delta >= self._refresh_every

    async def refresh(
        self,
        chat_id: str,
        recent_messages: list[str],
    ) -> list[Goal]:
        """Заставляет analyzer пересчитать goals и обновляет кэш."""
        if self._analyzer_fn is None:
            logger.debug("session_goals_refresh_skipped reason=no_analyzer")
            return self.get_goals(chat_id)
        cid = str(chat_id)
        window = recent_messages[-self._analysis_window :] if recent_messages else []
        async with self._lock:
            try:
                goals = await self._analyzer_fn(cid, window)
            except Exception as exc:  # noqa: BLE001 — отлавливаем всё, чтобы не валить chat-flow
                logger.warning(
                    "session_goals_analyzer_failed chat_id=%s error=%s error_type=%s",
                    cid,
                    exc,
                    type(exc).__name__,
                )
                return self.get_goals(cid)
            self._load_from_disk()
            state = self._states.setdefault(cid, _ChatState())
            state.goals = list(goals)
            state.refreshed_at = (
                self._now_fn().replace(tzinfo=None).isoformat(timespec="seconds") + "Z"
            )
            state.message_count_at_refresh = state.total_messages_seen
            self._persist_to_disk()
            logger.info(
                "session_goals_refreshed chat_id=%s goals=%d",
                cid,
                len(state.goals),
            )
            return list(state.goals)

    def get_goals(self, chat_id: str) -> list[Goal]:
        """Возвращает текущий снимок goals (копия)."""
        self._load_from_disk()
        state = self._states.get(str(chat_id))
        if state is None:
            return []
        return list(state.goals)

    def system_prompt_suffix(self, chat_id: str, *, min_confidence: float = 0.4) -> str:
        """Готовый кусок текста для system prompt: «Активные goals owner'а: …».

        Возвращает пустую строку если goals нет либо ниже порога confidence.
        """
        goals = [g for g in self.get_goals(chat_id) if g.confidence >= min_confidence]
        if not goals:
            return ""
        names = [g.name for g in goals[:3]]
        joined = ", ".join(names)
        return f"\n\nАктивные цели/проекты owner'а в этом чате: {joined}."

    def reset(self, chat_id: str | None = None) -> None:
        """Очищает кэш для chat'а (или весь, если chat_id is None)."""
        self._load_from_disk()
        if chat_id is None:
            self._states.clear()
        else:
            self._states.pop(str(chat_id), None)
        self._persist_to_disk()

    def to_dict(self) -> dict[str, Any]:
        """Снимок всего состояния (для диагностики/CLI)."""
        self._load_from_disk()
        return {
            "chats": {cid: asdict(state) for cid, state in self._states.items()},
            "config": {
                "refresh_every": self._refresh_every,
                "ttl_hours": self._ttl_hours,
                "analysis_window": self._analysis_window,
            },
        }


# Singleton.
goal_tracker = GoalTracker()


def configure_default_path(storage_path: Path) -> None:
    """Bootstrap-hook: переинициализирует singleton под другой storage path."""
    global goal_tracker
    # Сохраняем уже инжектированный analyzer (если был).
    analyzer = goal_tracker._analyzer_fn  # noqa: SLF001 — точечная переустановка
    goal_tracker = GoalTracker(storage_path=storage_path, analyzer_fn=analyzer)
