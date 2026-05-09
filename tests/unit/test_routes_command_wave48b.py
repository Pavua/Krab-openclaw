# -*- coding: utf-8 -*-
"""Wave 48-B: тесты для !routes command + route_switch_log + ETA formatter.

Покрытие:
- _format_recovery_time: 4 кейса (s/m/h/d)
- _format_chain_state: ✅/⏸ marks + recovery ETA
- handle_routes: integration через mocked Message
- route_switch_log: ring buffer append/read FIFO
"""

from __future__ import annotations

import datetime
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.handlers.commands.observability_commands import (
    _format_chain_state,
    _format_recent_switches,
    _format_recovery_time,
    handle_routes,
)
from src.integrations import route_switch_log

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_message() -> SimpleNamespace:
    return SimpleNamespace(
        text="!routes",
        chat=SimpleNamespace(id=-100123),
        reply=AsyncMock(),
    )


def _make_bot() -> MagicMock:
    return MagicMock()


# ---------------------------------------------------------------------------
# _format_recovery_time
# ---------------------------------------------------------------------------


def test_recovery_time_format_short_seconds() -> None:
    assert _format_recovery_time(23) == "23s"
    assert _format_recovery_time(0) == "0s"
    # negative clamped to 0
    assert _format_recovery_time(-5) == "0s"


def test_recovery_time_format_minutes() -> None:
    assert _format_recovery_time(330) == "5m 30s"
    assert _format_recovery_time(300) == "5m"  # без секунд если 0


def test_recovery_time_format_hours() -> None:
    assert _format_recovery_time(2 * 3600 + 15 * 60) == "2h 15m"
    assert _format_recovery_time(3600) == "1h"


def test_recovery_time_format_days() -> None:
    # 4d 12h
    assert _format_recovery_time(4 * 24 * 3600 + 12 * 3600) == "4d 12h"
    # ровно 7 дней — "7d"
    assert _format_recovery_time(7 * 24 * 3600) == "7d"


# ---------------------------------------------------------------------------
# _format_chain_state
# ---------------------------------------------------------------------------


def test_chain_state_all_available_when_no_quota_state() -> None:
    chain = _format_chain_state(
        primary="codex-cli/gpt-5.5",
        fallbacks=["gemini-3-pro-preview", "google-vertex/gemini-flash-latest"],
        quota_state={},
    )
    assert len(chain) == 3
    assert all(line.startswith("✅") for line in chain)
    assert "codex-cli/gpt-5.5" in chain[0]


def test_chain_state_codex_disabled_shows_recovery_eta() -> None:
    # disabled_at = now - 2d 12h → remaining = 4d 12h из weekly cooldown
    now = datetime.datetime(2026, 5, 10, 12, 0, 0, tzinfo=datetime.timezone.utc)
    disabled_at = (now - datetime.timedelta(days=2, hours=12)).isoformat()
    chain = _format_chain_state(
        primary="codex-cli/gpt-5.5",
        fallbacks=["gemini-3-pro-preview"],
        quota_state={
            "disabled": True,
            "disabled_at": disabled_at,
            "kind": "weekly",
            "last_fallback_model": "gemini-3-pro-preview",
        },
        now_utc=now,
    )
    # codex-cli line должна быть ⏸
    codex_line = next(line for line in chain if "codex-cli" in line)
    assert codex_line.startswith("⏸")
    assert "quota exhausted" in codex_line
    assert "4d 12h" in codex_line
    # gemini-3-pro-preview всё ещё ✅
    gemini_line = next(line for line in chain if "gemini-3-pro" in line)
    assert gemini_line.startswith("✅")


def test_chain_state_dedupes_duplicate_models() -> None:
    chain = _format_chain_state(
        primary="model-a",
        fallbacks=["model-a", "model-b"],
        quota_state={},
    )
    # Не должно быть двух model-a
    assert sum(1 for line in chain if "model-a" in line) == 1
    assert any("model-b" in line for line in chain)


# ---------------------------------------------------------------------------
# _format_recent_switches
# ---------------------------------------------------------------------------


def test_recent_switches_format_ok() -> None:
    entries = [
        {
            "ts": "2026-05-10T00:12:00+00:00",
            "from": "codex-cli/gpt-5.5",
            "to": "gemini-3-pro-preview",
            "reason": "quota",
        }
    ]
    lines = _format_recent_switches(entries)
    assert len(lines) == 1
    assert "00:12" in lines[0]
    assert "codex-cli/gpt-5.5" in lines[0]
    assert "gemini-3-pro-preview" in lines[0]
    assert "(quota)" in lines[0]


def test_recent_switches_handles_malformed() -> None:
    entries = [{"ts": "garbage", "from": None, "to": None, "reason": None}]
    lines = _format_recent_switches(entries)
    assert len(lines) == 1
    assert "?" in lines[0]


# ---------------------------------------------------------------------------
# route_switch_log persistence
# ---------------------------------------------------------------------------


def test_route_switch_log_append_and_read(tmp_path, monkeypatch) -> None:
    log_file = tmp_path / "route_switches.jsonl"
    monkeypatch.setattr(route_switch_log, "LOG_FILE", log_file)

    route_switch_log.append_switch(
        from_model="codex-cli/gpt-5.5",
        to_model="gemini-3-pro-preview",
        reason="quota",
        kind="weekly",
    )
    route_switch_log.append_switch(
        from_model="gemini-3-pro-preview",
        to_model="google-vertex/gemini-flash-latest",
        reason="provider_500",
    )
    items = route_switch_log.read_recent(limit=5)
    assert len(items) == 2
    assert items[0]["from"] == "codex-cli/gpt-5.5"
    assert items[1]["reason"] == "provider_500"


def test_route_switch_log_ring_buffer_caps(tmp_path, monkeypatch) -> None:
    log_file = tmp_path / "route_switches.jsonl"
    monkeypatch.setattr(route_switch_log, "LOG_FILE", log_file)
    monkeypatch.setattr(route_switch_log, "MAX_ENTRIES", 3)

    for i in range(5):
        route_switch_log.append_switch(from_model=f"m{i}", to_model=f"m{i + 1}", reason="quota")
    items = route_switch_log.read_recent(limit=10)
    # Только последние 3 должны остаться
    assert len(items) == 3
    assert items[0]["from"] == "m2"
    assert items[-1]["from"] == "m4"


def test_route_switch_log_read_missing_file(tmp_path, monkeypatch) -> None:
    log_file = tmp_path / "nonexistent.jsonl"
    monkeypatch.setattr(route_switch_log, "LOG_FILE", log_file)
    assert route_switch_log.read_recent() == []


def test_route_switch_log_read_skips_invalid_lines(tmp_path, monkeypatch) -> None:
    log_file = tmp_path / "route_switches.jsonl"
    log_file.write_text(
        '{"ts":"2026-05-10T00:12:00+00:00","from":"a","to":"b","reason":"q"}\n'
        "garbage line\n"
        '{"ts":"2026-05-10T00:13:00+00:00","from":"b","to":"c","reason":"q"}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(route_switch_log, "LOG_FILE", log_file)
    items = route_switch_log.read_recent(limit=10)
    assert len(items) == 2
    assert items[0]["from"] == "a"
    assert items[1]["from"] == "b"


# ---------------------------------------------------------------------------
# handle_routes integration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_routes_shows_primary_and_active_models(monkeypatch, tmp_path) -> None:
    # Mock quota state файл — отсутствует => все ✅
    state_file = tmp_path / "codex_quota_state.json"

    import src.handlers.commands.observability_commands as obs_mod

    def _mock_read_state() -> dict:
        return {}

    monkeypatch.setattr(obs_mod, "_read_codex_quota_state", _mock_read_state)
    monkeypatch.setattr(
        "src.core.openclaw_runtime_models.get_runtime_primary_model",
        lambda: "codex-cli/gpt-5.5",
    )
    monkeypatch.setattr(
        "src.core.openclaw_runtime_models.get_runtime_fallback_models",
        lambda: ["gemini-3-pro-preview", "google-vertex/gemini-flash-latest"],
    )
    # mock route_switch_log.read_recent
    monkeypatch.setattr(route_switch_log, "LOG_FILE", state_file)  # пустой = no switches

    msg = _make_message()
    await handle_routes(_make_bot(), msg)
    assert msg.reply.await_count == 1
    text = msg.reply.await_args.args[0]
    assert "Routing State" in text
    assert "codex-cli/gpt-5.5" in text
    assert "Primary" in text
    assert "Active" in text


@pytest.mark.asyncio
async def test_handle_routes_quota_recovery_eta_format(monkeypatch, tmp_path) -> None:
    """Если codex disabled — должен показать ⏸ + recovery ETA."""
    import src.handlers.commands.observability_commands as obs_mod

    now = datetime.datetime.now(datetime.timezone.utc)
    disabled_at = (now - datetime.timedelta(days=2, hours=12)).isoformat()

    def _mock_read_state() -> dict:
        return {
            "disabled": True,
            "disabled_at": disabled_at,
            "kind": "weekly",
            "last_fallback_model": "gemini-3-pro-preview",
        }

    monkeypatch.setattr(obs_mod, "_read_codex_quota_state", _mock_read_state)
    monkeypatch.setattr(
        "src.core.openclaw_runtime_models.get_runtime_primary_model",
        lambda: "codex-cli/gpt-5.5",
    )
    monkeypatch.setattr(
        "src.core.openclaw_runtime_models.get_runtime_fallback_models",
        lambda: ["gemini-3-pro-preview"],
    )
    monkeypatch.setattr(route_switch_log, "LOG_FILE", tmp_path / "missing.jsonl")

    msg = _make_message()
    await handle_routes(_make_bot(), msg)
    text = msg.reply.await_args.args[0]
    assert "⏸" in text
    assert "quota exhausted" in text
    # Active = last_fallback_model
    assert "gemini-3-pro-preview" in text
    assert "fallback after quota" in text


@pytest.mark.asyncio
async def test_handle_routes_no_recent_switches_shows_empty_state(monkeypatch, tmp_path) -> None:
    import src.handlers.commands.observability_commands as obs_mod

    monkeypatch.setattr(obs_mod, "_read_codex_quota_state", lambda: {})
    monkeypatch.setattr(
        "src.core.openclaw_runtime_models.get_runtime_primary_model",
        lambda: "primary",
    )
    monkeypatch.setattr(
        "src.core.openclaw_runtime_models.get_runtime_fallback_models",
        lambda: ["fb1"],
    )
    monkeypatch.setattr(route_switch_log, "LOG_FILE", tmp_path / "missing.jsonl")

    msg = _make_message()
    await handle_routes(_make_bot(), msg)
    text = msg.reply.await_args.args[0]
    assert "no recent switches" in text


@pytest.mark.asyncio
async def test_handle_routes_reads_recent_switches_from_log(monkeypatch, tmp_path) -> None:
    log_file = tmp_path / "route_switches.jsonl"
    entry = json.dumps(
        {
            "ts": "2026-05-10T00:12:00+00:00",
            "from": "codex-cli/gpt-5.5",
            "to": "gemini-3-pro-preview",
            "reason": "quota",
        }
    )
    log_file.write_text(entry + "\n", encoding="utf-8")

    import src.handlers.commands.observability_commands as obs_mod

    monkeypatch.setattr(obs_mod, "_read_codex_quota_state", lambda: {})
    monkeypatch.setattr(
        "src.core.openclaw_runtime_models.get_runtime_primary_model",
        lambda: "codex-cli/gpt-5.5",
    )
    monkeypatch.setattr(
        "src.core.openclaw_runtime_models.get_runtime_fallback_models",
        lambda: ["gemini-3-pro-preview"],
    )
    monkeypatch.setattr(route_switch_log, "LOG_FILE", log_file)

    msg = _make_message()
    await handle_routes(_make_bot(), msg)
    text = msg.reply.await_args.args[0]
    assert "00:12" in text
    assert "(quota)" in text


@pytest.mark.asyncio
async def test_handle_routes_handles_missing_state_files_gracefully(monkeypatch, tmp_path) -> None:
    """Все файлы отсутствуют — handle_routes не падает."""
    import src.handlers.commands.observability_commands as obs_mod

    monkeypatch.setattr(obs_mod, "_read_codex_quota_state", lambda: {})
    monkeypatch.setattr("src.core.openclaw_runtime_models.get_runtime_primary_model", lambda: "")
    monkeypatch.setattr("src.core.openclaw_runtime_models.get_runtime_fallback_models", lambda: [])
    monkeypatch.setattr(route_switch_log, "LOG_FILE", tmp_path / "nonexistent.jsonl")

    msg = _make_message()
    await handle_routes(_make_bot(), msg)
    # Должен ответить, не raise
    assert msg.reply.await_count == 1
