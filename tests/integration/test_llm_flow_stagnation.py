# -*- coding: utf-8 -*-
"""
Integration тест: stagnation cancel в _finish_ai_request_background.

Проверяет что:
1. Когда _run_llm_request_flow raise CancelledError с reason='llm_stagnation_detected',
   обёртка `_finish_ai_request_background` ловит это и тихо выходит (не retry, не raise).
2. Другие CancelledError (без маркера) пробрасываются наверх как раньше.
3. Stagnation НЕ триггерит auto-retry (это inf-провайдер hung, retry не поможет).
"""

from __future__ import annotations

import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.append(os.getcwd())

from src.userbot.llm_flow import LLM_STAGNATION_CANCEL_REASON  # noqa: E402


@pytest.mark.asyncio
async def test_finish_ai_request_background_swallows_stagnation_cancel() -> None:
    """CancelledError с stagnation маркером — молча выходим, не retry."""
    from src.userbot_bridge import KraabUserbot

    with patch("src.userbot_bridge.Client"):
        bot = KraabUserbot()
        bot.client = AsyncMock()
        bot._safe_edit = AsyncMock()

        # Подменяем _run_llm_request_flow: эмулируем raise из watchdog.
        async def _raise_stagnation(**kwargs):
            raise asyncio.CancelledError(LLM_STAGNATION_CANCEL_REASON)

        bot._run_llm_request_flow = _raise_stagnation  # type: ignore[method-assign]

        temp_msg = MagicMock()
        temp_msg.chat.id = 12345

        # Не должно быть исключения — обёртка должна поймать и молча return.
        await bot._finish_ai_request_background(
            chat_id="12345",
            temp_msg=temp_msg,
        )


@pytest.mark.asyncio
async def test_finish_ai_request_background_reraises_other_cancel() -> None:
    """Обычный CancelledError (без маркера) — пробрасывается выше.

    CancelledError — наследник BaseException (Python 3.8+), поэтому внешний
    `except Exception` его не ловит. Owner/caller должен получить reraise.
    """
    from src.userbot_bridge import KraabUserbot

    with patch("src.userbot_bridge.Client"):
        bot = KraabUserbot()
        bot.client = AsyncMock()
        bot._safe_edit = AsyncMock()

        async def _raise_plain_cancel(**kwargs):
            raise asyncio.CancelledError("user_aborted")

        bot._run_llm_request_flow = _raise_plain_cancel  # type: ignore[method-assign]

        temp_msg = MagicMock()
        temp_msg.chat.id = 12345

        # Без stagnation-маркера — обычный CancelledError должен пробрасываться,
        # чтобы asyncio scheduler корректно отменил cascade task'ов.
        with pytest.raises(asyncio.CancelledError):
            await bot._finish_ai_request_background(
                chat_id="12345",
                temp_msg=temp_msg,
            )


@pytest.mark.asyncio
async def test_stagnation_cancel_skips_retry_loop() -> None:
    """
    Stagnation cancel НЕ должен дергать auto-retry: _run_llm_request_flow
    вызывается ровно один раз (не retries+1).
    """
    from src.userbot_bridge import KraabUserbot

    call_counter = {"count": 0}

    with patch("src.userbot_bridge.Client"):
        bot = KraabUserbot()
        bot.client = AsyncMock()
        bot._safe_edit = AsyncMock()

        async def _raise_stagnation(**kwargs):
            call_counter["count"] += 1
            raise asyncio.CancelledError(LLM_STAGNATION_CANCEL_REASON)

        bot._run_llm_request_flow = _raise_stagnation  # type: ignore[method-assign]

        temp_msg = MagicMock()
        temp_msg.chat.id = 12345

        await bot._finish_ai_request_background(
            chat_id="12345",
            temp_msg=temp_msg,
        )

        assert call_counter["count"] == 1, "Stagnation cancel не должен триггерить retry"


@pytest.mark.asyncio
async def test_detect_stagnation_integration_via_poller() -> None:
    """
    Интеграция: при подстановке stagnant tasks через poll_active_tasks
    detect_stagnation находит их.
    """
    from src.core.openclaw_task_poller import TaskState, detect_stagnation

    fake_tasks = [
        TaskState(
            task_id="t_hung",
            status="running",
            label="codex-cli",
            progress_summary="waiting for subprocess",
            last_event_at_ms=int((asyncio.get_event_loop().time() * 1000) - 200_000),
            is_stale=True,
        )
    ]
    # last_event_at_ms в прошлом относительно time.time() — используем реальное время.
    import time as _t

    fake_tasks = [
        TaskState(
            task_id="t_hung",
            status="running",
            label="codex-cli",
            progress_summary="waiting for subprocess",
            last_event_at_ms=int((_t.time() - 200.0) * 1000),
            is_stale=True,
        )
    ]
    result = detect_stagnation(fake_tasks, threshold_sec=120.0)
    assert len(result) == 1
    assert result[0].task_id == "t_hung"
