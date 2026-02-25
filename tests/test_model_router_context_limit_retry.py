# -*- coding: utf-8 -*-
"""
Регрессия: при context_limit роутер должен сделать retry с укороченным контекстом.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.core.model_manager import ModelRouter


@pytest.mark.asyncio
async def test_call_gemini_retries_with_trimmed_context_on_context_limit(tmp_path: Path) -> None:
    router = ModelRouter(
        config={
            "MODEL_ROUTING_MEMORY_PATH": str(tmp_path / "routing_memory.json"),
            "MODEL_USAGE_REPORT_PATH": str(tmp_path / "usage_report.json"),
            "MODEL_OPS_STATE_PATH": str(tmp_path / "ops_state.json"),
            "MODEL_FEEDBACK_PATH": str(tmp_path / "feedback.json"),
        }
    )

    calls: list[int] = []

    async def fake_chat_completions(messages, model: str = "", timeout_seconds: int = 0, probe_provider_on_error: bool = False):
        calls.append(len(messages))
        if len(calls) == 1:
            return "❌ OpenClaw Error (400): Model context window too small (12000 tokens). Minimum is 16000."
        return "OK_AFTER_TRIM"

    router.openclaw_client.chat_completions = fake_chat_completions  # type: ignore[assignment]

    context = [
        {"role": "user", "text": f"сообщение-{idx}-" + ("x" * 2200)}
        for idx in range(12)
    ]
    result = await router._call_gemini(
        prompt="проверка контекстного окна",
        model_name="google/gemini-2.5-flash",
        context=context,
        chat_type="private",
        is_owner=True,
        max_retries=2,
    )

    assert result == "OK_AFTER_TRIM"
    assert len(calls) == 2
    assert calls[1] < calls[0]
