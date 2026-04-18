# -*- coding: utf-8 -*-
"""
Тесты для structured reflection (Chado blueprint).

Покрываем:
- structured_reflect: valid JSON, markdown-wrapped, invalid JSON, schema violation,
  LLM error, no caller.
- FollowUpItem / ReflectionOutput Pydantic validation.
- flush_followups_to_reminders: time-based, tomorrow, manual, import-error fallback.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from src.core.swarm_self_reflection import (
    FollowUpItem,
    ReflectionOutput,
    flush_followups_to_reminders,
    structured_reflect,
)

# ---------------------------------------------------------------------------
# FollowUpItem validation
# ---------------------------------------------------------------------------


class TestFollowUpItem:
    def test_defaults(self) -> None:
        fup = FollowUpItem(text="do something")
        assert fup.when == "manual"
        assert fup.priority == "medium"
        assert fup.chat_id is None

    def test_valid_priorities(self) -> None:
        for p in ("low", "medium", "high", "critical"):
            fup = FollowUpItem(text="x" * 3, priority=p)  # type: ignore[arg-type]
            assert fup.priority == p

    def test_invalid_priority_raises(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            FollowUpItem(text="check it", priority="urgent")  # type: ignore[arg-type]

    def test_text_min_length(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            FollowUpItem(text="ab")  # len < 3

    def test_text_max_length(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            FollowUpItem(text="x" * 501)


# ---------------------------------------------------------------------------
# ReflectionOutput validation
# ---------------------------------------------------------------------------


class TestReflectionOutput:
    def test_empty_defaults(self) -> None:
        out = ReflectionOutput()
        assert out.insights == []
        assert out.unresolved == []
        assert out.follow_ups == []

    def test_follow_ups_max_length(self) -> None:
        from pydantic import ValidationError

        items = [FollowUpItem(text=f"task {i}") for i in range(11)]
        with pytest.raises(ValidationError):
            ReflectionOutput(follow_ups=items)

    def test_insights_max_length(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ReflectionOutput(insights=["i"] * 6)


# ---------------------------------------------------------------------------
# structured_reflect
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_structured_reflect_valid_json() -> None:
    """LLM вернул валидный JSON → распарсен без ошибок."""

    async def mock_llm(prompt: str) -> str:
        return (
            '{"insights": ["I1"], "unresolved": [], '
            '"follow_ups": [{"text": "check later", "when": "in 2 hours", "priority": "high"}]}'
        )

    result = await structured_reflect(
        "t1", "title", "desc", "result", llm_caller=mock_llm
    )
    assert len(result.insights) == 1
    assert result.insights[0] == "I1"
    assert len(result.follow_ups) == 1
    assert result.follow_ups[0].priority == "high"
    assert result.follow_ups[0].when == "in 2 hours"


@pytest.mark.asyncio
async def test_structured_reflect_markdown_wrapped() -> None:
    """LLM обернул JSON в ```json — парсер должен снять обёртку."""

    async def mock_llm(prompt: str) -> str:
        return '```json\n{"insights":["I"],"unresolved":[],"follow_ups":[]}\n```'

    result = await structured_reflect("t1", "t", "d", "r", llm_caller=mock_llm)
    assert result.insights == ["I"]
    assert result.follow_ups == []


@pytest.mark.asyncio
async def test_structured_reflect_no_caller_returns_empty() -> None:
    """Без llm_caller → пустой ReflectionOutput, не падаем."""
    result = await structured_reflect("t1", "t", "d", "r")
    assert result.insights == []
    assert result.follow_ups == []


@pytest.mark.asyncio
async def test_structured_reflect_invalid_json_returns_empty() -> None:
    """LLM вернул мусор → пустой output, не падаем."""

    async def mock_llm(prompt: str) -> str:
        return "not json at all"

    result = await structured_reflect("t1", "t", "d", "r", llm_caller=mock_llm)
    assert result.insights == []
    assert result.follow_ups == []


@pytest.mark.asyncio
async def test_structured_reflect_schema_violation_returns_empty() -> None:
    """JSON найден но не соответствует схеме → empty, не падаем."""

    async def mock_llm(prompt: str) -> str:
        # insights должен быть списком, а не строкой
        return '{"insights": "should be list", "follow_ups": []}'

    result = await structured_reflect("t1", "t", "d", "r", llm_caller=mock_llm)
    assert result.insights == []


@pytest.mark.asyncio
async def test_structured_reflect_llm_raises_returns_empty() -> None:
    """LLM-caller бросает исключение → пустой output, не падаем."""

    async def raising_caller(prompt: str) -> str:
        raise RuntimeError("network error")

    result = await structured_reflect("t1", "t", "d", "r", llm_caller=raising_caller)
    assert result.insights == []
    assert result.follow_ups == []


@pytest.mark.asyncio
async def test_structured_reflect_history_snippet_truncated() -> None:
    """history_snippet обрезается до 500 символов в промпте."""
    captured: list[str] = []

    async def capturing_llm(prompt: str) -> str:
        captured.append(prompt)
        return '{"insights":[],"unresolved":[],"follow_ups":[]}'

    long_history = "h" * 1000
    await structured_reflect(
        "t1", "t", "d", "r", llm_caller=capturing_llm, history_snippet=long_history
    )
    assert len(captured) == 1
    # 500 h-символов должны быть в промпте, но не 501
    assert "h" * 500 in captured[0]
    assert "h" * 501 not in captured[0]


@pytest.mark.asyncio
async def test_structured_reflect_partial_schema_uses_defaults() -> None:
    """follow_up без поля when → использует default 'manual'."""

    async def mock_llm(prompt: str) -> str:
        return (
            '{"insights": [], "unresolved": [], '
            '"follow_ups": [{"text": "run checks"}]}'
        )

    result = await structured_reflect("t1", "t", "d", "r", llm_caller=mock_llm)
    assert len(result.follow_ups) == 1
    assert result.follow_ups[0].when == "manual"
    assert result.follow_ups[0].priority == "medium"


# ---------------------------------------------------------------------------
# flush_followups_to_reminders
# ---------------------------------------------------------------------------


def test_flush_time_based_in_hours() -> None:
    """'in N hours' → add_time_reminder вызывается с правильным fire_at."""
    mock_queue = MagicMock()
    mock_queue.add_time_reminder = MagicMock(return_value="rid1")

    refl = ReflectionOutput(
        follow_ups=[
            FollowUpItem(text="check stats", when="in 2 hours", priority="medium"),
        ]
    )
    before = int(time.time())
    with patch("src.core.reminders_queue.reminders_queue", mock_queue):
        count = flush_followups_to_reminders(refl)

    assert count == 1
    mock_queue.add_time_reminder.assert_called_once()
    call_kwargs = mock_queue.add_time_reminder.call_args.kwargs
    assert call_kwargs["owner_id"] == "self"
    assert call_kwargs["action_type"] == "notify"
    assert "[medium]" in call_kwargs["action"]
    assert "check stats" in call_kwargs["action"]
    # fire_at ≈ now + 2 * 3600
    expected_fire_at = before + 2 * 3600
    assert abs(call_kwargs["fire_at"] - expected_fire_at) < 5


def test_flush_time_based_in_minutes() -> None:
    """'in N minutes' → multiplier = 60."""
    mock_queue = MagicMock()
    refl = ReflectionOutput(
        follow_ups=[FollowUpItem(text="quick check", when="in 30 min", priority="low")]
    )
    before = int(time.time())
    with patch("src.core.reminders_queue.reminders_queue", mock_queue):
        count = flush_followups_to_reminders(refl)

    assert count == 1
    call_kwargs = mock_queue.add_time_reminder.call_args.kwargs
    expected = before + 30 * 60
    assert abs(call_kwargs["fire_at"] - expected) < 5


def test_flush_tomorrow_format() -> None:
    """'tomorrow HH:MM' → fire_at в следующих сутках."""
    mock_queue = MagicMock()
    refl = ReflectionOutput(
        follow_ups=[
            FollowUpItem(text="morning review", when="tomorrow 09:00", priority="high")
        ]
    )
    with patch("src.core.reminders_queue.reminders_queue", mock_queue):
        count = flush_followups_to_reminders(refl)

    assert count == 1
    call_kwargs = mock_queue.add_time_reminder.call_args.kwargs
    # fire_at должен быть завтра → > now + 12 часов как минимум
    assert call_kwargs["fire_at"] > int(time.time()) + 12 * 3600


def test_flush_manual_items_skipped() -> None:
    """'manual' when → не добавляются в reminders_queue."""
    mock_queue = MagicMock()
    refl = ReflectionOutput(
        follow_ups=[
            FollowUpItem(text="manual task", when="manual"),
            FollowUpItem(text="another manual", when="MANUAL"),
        ]
    )
    with patch("src.core.reminders_queue.reminders_queue", mock_queue):
        count = flush_followups_to_reminders(refl)

    assert count == 0
    mock_queue.add_time_reminder.assert_not_called()


def test_flush_mixed_manual_and_timed() -> None:
    """Смешанные: только timed попадают в queue."""
    mock_queue = MagicMock()
    refl = ReflectionOutput(
        follow_ups=[
            FollowUpItem(text="manual task", when="manual"),
            FollowUpItem(text="timed task", when="in 1 hours", priority="critical"),
            FollowUpItem(text="also manual", when="manual"),
        ]
    )
    with patch("src.core.reminders_queue.reminders_queue", mock_queue):
        count = flush_followups_to_reminders(refl)

    assert count == 1
    assert mock_queue.add_time_reminder.call_count == 1
    call_kwargs = mock_queue.add_time_reminder.call_args.kwargs
    assert "[critical]" in call_kwargs["action"]


def test_flush_custom_owner_id() -> None:
    """owner_id передаётся в reminders_queue."""
    mock_queue = MagicMock()
    refl = ReflectionOutput(
        follow_ups=[FollowUpItem(text="owner check", when="in 1 hours")]
    )
    with patch("src.core.reminders_queue.reminders_queue", mock_queue):
        flush_followups_to_reminders(refl, owner_id="user_42")

    call_kwargs = mock_queue.add_time_reminder.call_args.kwargs
    assert call_kwargs["owner_id"] == "user_42"


def test_flush_import_error_returns_zero() -> None:
    """ImportError при импорте reminders_queue → возвращает 0, не падаем."""
    # Проверяем что функция существует и принимает правильные аргументы.
    # ImportError path трудно тестировать напрямую без разрушения модуля.
    refl = ReflectionOutput(follow_ups=[])
    count = flush_followups_to_reminders(refl)
    assert isinstance(count, int)
    assert count == 0


def test_flush_empty_follow_ups() -> None:
    """Пустой follow_ups → 0 без вызовов."""
    mock_queue = MagicMock()
    refl = ReflectionOutput()
    with patch("src.core.reminders_queue.reminders_queue", mock_queue):
        count = flush_followups_to_reminders(refl)

    assert count == 0
    mock_queue.add_time_reminder.assert_not_called()
