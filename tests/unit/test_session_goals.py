"""Тесты Feature J — Session Goal Tracking."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.core.session_goals import Goal, GoalTracker, parse_goals_response


def _make_tracker(tmp_path: Path, *, analyzer=None, clock_box=None, **kwargs) -> GoalTracker:
    storage = tmp_path / "session_goals.json"
    now_fn = (lambda: clock_box[0]) if clock_box is not None else None
    return GoalTracker(
        storage_path=storage,
        analyzer_fn=analyzer,
        now_fn=now_fn,
        **kwargs,
    )


def test_parse_goals_response_handles_codefence():
    raw = """```json
[{"name": "Krab refactor", "evidence": "обсуждаем модули", "confidence": 0.8}]
```"""
    goals = parse_goals_response(raw)
    assert len(goals) == 1
    assert goals[0].name == "Krab refactor"
    assert 0.79 < goals[0].confidence < 0.81


def test_parse_goals_response_invalid_returns_empty():
    assert parse_goals_response("") == []
    assert parse_goals_response("не json вообще") == []
    assert parse_goals_response("{not a list}") == []


@pytest.mark.asyncio
async def test_refresh_caches_goals_and_persists(tmp_path: Path):
    captured = []

    async def fake_analyzer(chat_id: str, msgs: list[str]) -> list[Goal]:
        captured.append((chat_id, list(msgs)))
        return [Goal(name="Project X", evidence="evidence", confidence=0.9)]

    tracker = _make_tracker(tmp_path, analyzer=fake_analyzer, refresh_every=3)
    # Регистрируем 3 сообщения — должно потребовать refresh.
    for _ in range(3):
        tracker.note_message("c1")
    assert tracker.should_refresh("c1") is True

    goals = await tracker.refresh("c1", ["msg1", "msg2", "msg3"])
    assert len(goals) == 1
    assert goals[0].name == "Project X"
    assert captured[0][0] == "c1"

    # После refresh — не нужно сразу пересчитывать.
    assert tracker.should_refresh("c1") is False
    # Persist: новый tracker подхватывает state.
    tracker2 = _make_tracker(tmp_path, analyzer=fake_analyzer)
    assert tracker2.get_goals("c1")[0].name == "Project X"


def test_should_refresh_triggers_after_ttl(tmp_path: Path):
    clock = [datetime(2026, 4, 28, 10, 0, 0, tzinfo=timezone.utc)]
    tracker = _make_tracker(tmp_path, clock_box=clock, ttl_hours=24)
    # Симулируем: state с goals и refreshed_at = час назад.
    tracker._states["c1"] = (
        tracker._states.get("c1")
        or __import__("src.core.session_goals", fromlist=["_ChatState"])._ChatState()
    )
    state = tracker._states["c1"]
    state.goals = [Goal(name="X", evidence="e", confidence=0.9)]
    state.refreshed_at = (clock[0] - timedelta(hours=1)).replace(tzinfo=None).isoformat(
        timespec="seconds"
    ) + "Z"
    state.message_count_at_refresh = 0
    state.total_messages_seen = 0
    tracker._loaded = True
    # Час прошёл, TTL 24h — рефрешить пока не надо.
    assert tracker.should_refresh("c1") is False
    # Сдвигаем clock на 25 часов вперёд → пора.
    clock[0] = clock[0] + timedelta(hours=25)
    assert tracker.should_refresh("c1") is True


def test_system_prompt_suffix_filters_low_confidence(tmp_path: Path):
    tracker = _make_tracker(tmp_path)
    tracker._states["c1"] = __import__(
        "src.core.session_goals", fromlist=["_ChatState"]
    )._ChatState(
        goals=[
            Goal(name="High", evidence="e", confidence=0.9),
            Goal(name="Low", evidence="e", confidence=0.1),
        ],
    )
    tracker._loaded = True
    suffix = tracker.system_prompt_suffix("c1", min_confidence=0.4)
    assert "High" in suffix
    assert "Low" not in suffix
    # Без goals — пустой суффикс.
    assert tracker.system_prompt_suffix("absent_chat") == ""
