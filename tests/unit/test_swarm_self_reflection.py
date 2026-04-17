# -*- coding: utf-8 -*-
"""
Тесты для swarm_self_reflection (Proactivity Level 3).

Покрываем:
- reflect_on_task: парсинг JSON ответа, пустой результат без клиента,
  обработка LLM-ошибок, truncation result preview.
- enqueue_followups: time-based → reminders_queue, event-based → task_board,
  пустой followups, skipped for malformed entries.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.swarm_self_reflection import (
    ReflectionResult,
    _is_time_based,
    _parse_hours_from_trigger,
    enqueue_followups,
    reflect_on_task,
)

# ---------------------------------------------------------------------------
# reflect_on_task
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reflect_on_task_parses_json_response() -> None:
    """LLM вернул валидный JSON → insights/followups распарсены."""
    llm_response = """{
        "insights": ["Insight 1", "Insight 2"],
        "unresolved": ["Question 1"],
        "followups": [
            {"title": "Follow A", "description": "do A", "priority": "high", "suggested_team": "coders", "trigger": "manual"}
        ]
    }"""
    caller = AsyncMock(return_value=llm_response)

    result = await reflect_on_task(
        task_id="t1",
        task_title="test",
        task_description="desc",
        task_result="result text",
        llm_caller=caller,
    )

    assert len(result.insights) == 2
    assert result.insights[0] == "Insight 1"
    assert len(result.unresolved) == 1
    assert len(result.followups) == 1
    assert result.followups[0]["title"] == "Follow A"
    assert result.followups[0]["priority"] == "high"
    assert result.task_id == "t1"
    assert result.completed_at > 0


@pytest.mark.asyncio
async def test_reflect_on_task_no_client_returns_empty() -> None:
    """Без клиента возвращается пустой ReflectionResult — logger.warning."""
    result = await reflect_on_task("t1", "title", "desc", "res")
    assert result.insights == []
    assert result.unresolved == []
    assert result.followups == []
    assert result.raw_response == ""
    assert result.task_id == "t1"


@pytest.mark.asyncio
async def test_reflect_on_task_json_wrapped_in_markdown() -> None:
    """LLM ответ обёрнут в ```json ... ``` — парсер должен извлечь JSON."""
    llm_response = """Вот мой анализ:

```json
{"insights": ["A"], "unresolved": [], "followups": []}
```

Конец.
"""
    caller = AsyncMock(return_value=llm_response)
    result = await reflect_on_task(
        task_id="t2",
        task_title="t",
        task_description="d",
        task_result="r",
        llm_caller=caller,
    )
    assert result.insights == ["A"]


@pytest.mark.asyncio
async def test_reflect_on_task_invalid_json_returns_empty_parsed() -> None:
    """LLM вернул мусор → raw_response сохраняется, insights пустые."""
    caller = AsyncMock(return_value="бла-бла не-JSON ответ")
    result = await reflect_on_task(
        task_id="t3",
        task_title="t",
        task_description="d",
        task_result="r",
        llm_caller=caller,
    )
    assert result.insights == []
    assert result.followups == []
    # raw_response может содержать ответ (но JSON не найден → пустой raw или с обёрткой)


@pytest.mark.asyncio
async def test_reflect_on_task_llm_raises() -> None:
    """LLM-caller бросает исключение — возвращается пустой результат, не падаем."""

    async def raising_caller(prompt: str) -> str:
        raise RuntimeError("LLM unavailable")

    result = await reflect_on_task(
        task_id="t4",
        task_title="t",
        task_description="d",
        task_result="r",
        llm_caller=raising_caller,
    )
    assert result.insights == []
    assert result.followups == []
    assert result.task_id == "t4"


@pytest.mark.asyncio
async def test_reflect_on_task_truncates_long_result() -> None:
    """Result >2000 chars обрезается в промпте (проверяем что caller получил усечённый текст)."""
    long_result = "x" * 5000
    captured_prompts: list[str] = []

    async def capturing_caller(prompt: str) -> str:
        captured_prompts.append(prompt)
        return '{"insights": [], "unresolved": [], "followups": []}'

    await reflect_on_task(
        task_id="t5",
        task_title="t",
        task_description="d",
        task_result=long_result,
        llm_caller=capturing_caller,
    )
    assert len(captured_prompts) == 1
    # В промпте должно быть 2000 x-ов, не 5000
    assert "x" * 2000 in captured_prompts[0]
    assert "x" * 2001 not in captured_prompts[0]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_parse_hours_ru(self) -> None:
        assert _parse_hours_from_trigger("через 3 часа") == 3
        assert _parse_hours_from_trigger("через 12 часов") == 12

    def test_parse_hours_en(self) -> None:
        assert _parse_hours_from_trigger("in 5 hours") == 5
        assert _parse_hours_from_trigger("1 hour later") == 1

    def test_parse_hours_default(self) -> None:
        assert _parse_hours_from_trigger("manual") == 2
        assert _parse_hours_from_trigger("") == 2

    def test_is_time_based_ru(self) -> None:
        assert _is_time_based("через 2 часа") is True
        assert _is_time_based("через час") is True

    def test_is_time_based_en(self) -> None:
        assert _is_time_based("in 3 hours") is True

    def test_is_time_based_false(self) -> None:
        assert _is_time_based("manual") is False
        assert _is_time_based("when X happens") is False
        assert _is_time_based("") is False


# ---------------------------------------------------------------------------
# enqueue_followups
# ---------------------------------------------------------------------------


class TestEnqueueFollowups:
    def test_empty_followups_does_nothing(self) -> None:
        reflection = ReflectionResult(task_id="t", completed_at=0)
        board = MagicMock()
        reminders = MagicMock()
        stats = enqueue_followups(reflection, task_board=board, reminders_queue=reminders)
        assert stats == {"board": 0, "reminders": 0, "skipped": 0}
        board.create_task.assert_not_called()
        reminders.add_time_reminder.assert_not_called()

    def test_time_based_goes_to_reminders(self) -> None:
        reflection = ReflectionResult(
            task_id="t",
            completed_at=0,
            followups=[
                {
                    "title": "Проверить через 3 часа",
                    "description": "повторить check",
                    "priority": "high",
                    "suggested_team": "analysts",
                    "trigger": "через 3 часа",
                }
            ],
        )
        reminders = MagicMock()
        stats = enqueue_followups(reflection, reminders_queue=reminders)
        assert stats["reminders"] == 1
        assert stats["board"] == 0
        reminders.add_time_reminder.assert_called_once()
        kwargs = reminders.add_time_reminder.call_args.kwargs
        assert kwargs["owner_id"] == "self"
        assert kwargs["action_type"] == "notify"
        assert "analysts" in kwargs["action"]

    def test_event_based_goes_to_task_board(self) -> None:
        reflection = ReflectionResult(
            task_id="t",
            completed_at=0,
            followups=[
                {
                    "title": "Follow A",
                    "description": "desc",
                    "priority": "medium",
                    "suggested_team": "coders",
                    "trigger": "manual",
                }
            ],
        )
        board = MagicMock()
        stats = enqueue_followups(reflection, task_board=board)
        assert stats["board"] == 1
        assert stats["reminders"] == 0
        board.create_task.assert_called_once()
        kwargs = board.create_task.call_args.kwargs
        assert kwargs["team"] == "coders"
        assert kwargs["title"] == "Follow A"
        assert kwargs["priority"] == "medium"
        assert kwargs["created_by"] == "self_reflection"

    def test_malformed_followup_is_skipped(self) -> None:
        reflection = ReflectionResult(
            task_id="t",
            completed_at=0,
            followups=[
                "not-a-dict",  # type: ignore[list-item]
                {"title": "", "description": ""},  # no content
                {"title": "OK", "description": "good", "trigger": "manual"},
            ],
        )
        board = MagicMock()
        stats = enqueue_followups(reflection, task_board=board)
        assert stats["board"] == 1
        assert stats["skipped"] == 2

    def test_time_based_without_reminders_falls_back_to_board(self) -> None:
        """Time-based, но reminders_queue не задан → fallback на task_board
        (чтобы задача не потерялась)."""
        reflection = ReflectionResult(
            task_id="t",
            completed_at=0,
            followups=[
                {
                    "title": "через час",
                    "description": "later",
                    "trigger": "через 1 час",
                }
            ],
        )
        board = MagicMock()
        stats = enqueue_followups(reflection, task_board=board, reminders_queue=None)
        assert stats["board"] == 1
        assert stats["reminders"] == 0
        board.create_task.assert_called_once()

    def test_time_based_without_any_queue_is_skipped(self) -> None:
        """Time-based и нет ни reminders_queue, ни board → skipped."""
        reflection = ReflectionResult(
            task_id="t",
            completed_at=0,
            followups=[
                {"title": "T", "description": "D", "trigger": "через 2 часа"}
            ],
        )
        stats = enqueue_followups(reflection, task_board=None, reminders_queue=None)
        assert stats["skipped"] == 1

    def test_board_create_task_raises_counts_as_skipped(self) -> None:
        reflection = ReflectionResult(
            task_id="t",
            completed_at=0,
            followups=[{"title": "T", "description": "D", "trigger": "manual"}],
        )
        board = MagicMock()
        board.create_task.side_effect = RuntimeError("disk full")
        stats = enqueue_followups(reflection, task_board=board)
        assert stats["skipped"] == 1
        assert stats["board"] == 0
