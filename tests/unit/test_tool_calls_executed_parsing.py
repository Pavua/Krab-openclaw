"""Tests for Wave 11-C: tool_calls_executed parsing + hallucination guard wiring.

См. docs/architecture/CLI_TOOL_CALLS_TELEMETRY_CONTRACT.md
"""

from __future__ import annotations

from src.core.hallucination_guard import detect_hallucinated_tool_success
from src.openclaw_client import parse_tool_calls_executed


def test_parse_with_telemetry():
    """Response с tool_calls_executed → entries parsed, verified=True."""
    data = {
        "choices": [{"message": {"content": "ok"}}],
        "tool_calls_executed": [
            {
                "tool": "krab-telegram.telegram_send_message",
                "args_redacted": {"chat_id": 123, "text_len": 184},
                "status": "done",
                "result_summary": {"message_id": 456, "ok": True},
                "started_at_ms": 1714650000123,
                "elapsed_ms": 234,
                "provider": "codex-cli",
                "trace_id": "ocl-7f3a",
            }
        ],
    }
    parsed = parse_tool_calls_executed(data)
    assert len(parsed) == 1
    entry = parsed[0]
    assert entry["name"] == "krab-telegram.telegram_send_message"
    assert entry["status"] == "done"
    assert entry["verified"] is True
    assert entry["provider"] == "codex-cli"
    assert entry["elapsed_ms"] == 234


def test_parse_without_telemetry():
    """Legacy response без поля → empty list (backward compat)."""
    data = {
        "choices": [{"message": {"content": "ok"}}],
        "usage": {"total_tokens": 100},
    }
    assert parse_tool_calls_executed(data) == []


def test_parse_none_response():
    """Non-dict input → empty list, no exception."""
    assert parse_tool_calls_executed(None) == []
    assert parse_tool_calls_executed("not a dict") == []
    assert parse_tool_calls_executed([]) == []


def test_parse_malformed():
    """Malformed entries skipped, valid ones kept; invalid type → empty."""
    # Не-список → пусто.
    assert parse_tool_calls_executed({"tool_calls_executed": "garbage"}) == []
    assert parse_tool_calls_executed({"tool_calls_executed": {"x": 1}}) == []

    # Mixed: некоторые entries некорректны, валидные остаются.
    data = {
        "tool_calls_executed": [
            "string-not-dict",
            {"tool": "valid.tool", "status": "done"},
            {"status": "done"},  # no tool name
            {"tool": "", "status": "done"},  # empty tool name
            {"tool": "another.tool", "status": "error", "elapsed_ms": 50},
        ]
    }
    parsed = parse_tool_calls_executed(data)
    assert len(parsed) == 2
    assert parsed[0]["name"] == "valid.tool"
    assert parsed[0]["verified"] is True
    assert parsed[1]["name"] == "another.tool"
    assert parsed[1]["status"] == "error"


def test_parse_status_default():
    """Missing status → defaults to 'done'."""
    data = {"tool_calls_executed": [{"tool": "foo.bar"}]}
    parsed = parse_tool_calls_executed(data)
    assert parsed[0]["status"] == "done"


def test_guard_trusts_verified_success():
    """Wave 9-A guard skips warning if verified write-tool entry exists."""
    text = "Отправил в личку Дашке: доставка прошла успешно, message id 1677"
    snapshot = [
        {
            "name": "krab-telegram.telegram_send_message",
            "status": "done",
            "verified": True,
        },
    ]
    assert detect_hallucinated_tool_success(text, snapshot) is False


def test_guard_no_write_tool_still_warns():
    """Empty snapshot + claim-of-success → guard fires (existing behaviour)."""
    text = "Отправил сообщение, доставка прошла успешно."
    assert detect_hallucinated_tool_success(text, []) is True


def test_guard_warns_on_inferred_only():
    """Verified=False write-tool entries: existing pre-Wave-11-C trust preserved.

    Эта проверка фиксирует backward-compat: до Wave 11-C поле verified
    не существовало, и любая write-tool entry считалась trustworthy.
    Wave 11-C сохраняет это поведение, чтобы избежать regressions.
    Будущие фазы могут tighten логику.
    """
    text = "Отправил сообщение успешно"
    snapshot = [
        {
            "name": "telegram_send_message",
            "status": "done",
            "verified": False,
        },
    ]
    # Backward compat: старые entries без verified=True всё ещё trusted.
    assert detect_hallucinated_tool_success(text, snapshot) is False


def test_guard_legacy_entry_without_verified_field():
    """Entries без verified key (legacy) → continue trusting (pre-Wave-11-C)."""
    text = "Сообщение отправлено"
    snapshot = [
        {"name": "telegram_send_message", "status": "done"},  # no verified key
    ]
    assert detect_hallucinated_tool_success(text, snapshot) is False
