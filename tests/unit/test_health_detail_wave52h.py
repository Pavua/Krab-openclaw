# -*- coding: utf-8 -*-
"""Wave 52-H: tests for !health detail comprehensive runtime state report.

Покрытие:
  - core/gateway/mcps/snapshots/routes/catchup collectors
  - format_health_detail markdown output
  - handle_health backward compat (short !health unchanged)
  - !health detail owner-only gate
  - graceful degradation при отсутствующих state-файлах
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.health_detail_collector import (
    _collect_health_catchup,
    _collect_health_core,
    _collect_health_gateway,
    _collect_health_mcps,
    _collect_health_memory,
    _collect_health_routes_24h,
    _collect_health_snapshots,
    collect_health_detail,
    format_health_detail,
)
from src.handlers.command_handlers import handle_health

# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_bot(*, me_id: int = 777, owner_level: bool = True) -> SimpleNamespace:
    """Stub KraabUserbot: minimal API for handle_health."""
    pw_task = MagicMock()
    pw_task.done.return_value = False

    from src.core.access_control import AccessLevel

    profile = SimpleNamespace(level=AccessLevel.OWNER if owner_level else AccessLevel.GUEST)

    bot = SimpleNamespace(
        me=SimpleNamespace(id=me_id),
        _proactive_watch_task=pw_task,
        _session_start_time=time.time() - 3600,
        get_voice_runtime_profile=lambda: {"enabled": True, "voice": "ru-RU-Test"},
        _get_access_profile=lambda _user: profile,
        _get_command_args=lambda _msg: "",
    )
    return bot


def _make_message(text: str = "!health", user_id: int = 42) -> SimpleNamespace:
    msg = SimpleNamespace(
        text=text,
        from_user=SimpleNamespace(id=user_id),
        reply=AsyncMock(),
        edit=AsyncMock(),
    )
    return msg


def _setup_runtime_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Override KRAB_RUNTIME_STATE_DIR to tmp_path."""
    monkeypatch.setenv("KRAB_RUNTIME_STATE_DIR", str(tmp_path))
    return tmp_path


# ── Collectors ───────────────────────────────────────────────────────────────


def test_collect_core_returns_pid_uptime_rss() -> None:
    started = time.time() - 120
    info = _collect_health_core(session_start_time=started)
    assert info["pid"] > 0
    assert info["uptime_sec"] >= 100
    # rss_mb available если psutil installed
    assert info["rss_mb"] is None or info["rss_mb"] > 0


def test_collect_core_handles_missing_session_start() -> None:
    info = _collect_health_core(session_start_time=None)
    assert info["uptime_sec"] == 0
    assert info["pid"] > 0


@pytest.mark.asyncio
async def test_collect_gateway_latency_measured() -> None:
    """Gateway probe возвращает latency_ms даже на failure."""

    class _MockResp:
        status_code = 200

    class _MockClient:
        def __init__(self, *_a, **_kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, _url):
            return _MockResp()

    with patch("httpx.AsyncClient", _MockClient):
        result = await _collect_health_gateway(url="http://127.0.0.1:18789/health")
    assert result["healthy"] is True
    assert result["status_code"] == 200
    assert isinstance(result["latency_ms"], int)
    assert result["latency_ms"] >= 0


@pytest.mark.asyncio
async def test_collect_gateway_handles_connection_error() -> None:
    class _BoomClient:
        def __init__(self, *_a, **_kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, _url):
            raise ConnectionError("refused")

    with patch("httpx.AsyncClient", _BoomClient):
        result = await _collect_health_gateway()
    assert result["healthy"] is False
    assert "error" in result
    assert "latency_ms" in result


def test_collect_mcps_via_subprocess_parsed() -> None:
    """openclaw mcp list --json → list of names."""

    fake_completed = SimpleNamespace(
        returncode=0,
        stdout=json.dumps(
            [
                {"name": "context7", "status": "ok"},
                {"name": "github", "status": "ok"},
                {"name": "krab-yung-nagato", "status": "ok"},
            ]
        ),
        stderr="",
    )
    with patch("subprocess.run", return_value=fake_completed):
        result = _collect_health_mcps()
    assert result["count"] == 3
    assert "context7" in result["names"]
    assert "krab-yung-nagato" in result["names"]


def test_collect_mcps_handles_missing_cli() -> None:
    with patch("subprocess.run", side_effect=FileNotFoundError):
        result = _collect_health_mcps()
    assert result["count"] == 0
    assert "openclaw CLI not found" in result.get("error", "")


def test_collect_snapshots_total_size_calculated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = _setup_runtime_state(tmp_path, monkeypatch)
    snap_root = base / "snapshots"
    for ts in ["20260509T220000Z", "20260509T230000Z", "20260510T000000Z"]:
        d = snap_root / ts
        d.mkdir(parents=True)
        (d / "route_switches.jsonl.bak").write_bytes(b"x" * 1024)
        (d / "swarm_memory.json.bak").write_bytes(b"y" * 2048)

    result = _collect_health_snapshots()
    assert result["count"] == 3
    assert result["total_bytes"] == 3 * (1024 + 2048)
    assert result["total_mb"] > 0
    assert result["last_ts"] == "20260510T000000Z"


def test_collect_snapshots_missing_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _setup_runtime_state(tmp_path, monkeypatch)
    result = _collect_health_snapshots()
    assert result["count"] == 0
    assert result["last_ts"] is None


def test_collect_routes_24h_count_filters_old(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = _setup_runtime_state(tmp_path, monkeypatch)
    log = base / "route_switches.jsonl"
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    entries = [
        # 3 свежих, 1 старая (48h)
        {"ts": (now - timedelta(hours=2)).isoformat(), "from": "a", "to": "b", "reason": "quota"},
        {"ts": (now - timedelta(hours=5)).isoformat(), "from": "a", "to": "c", "reason": "quota"},
        {
            "ts": (now - timedelta(hours=10)).isoformat(),
            "from": "a",
            "to": "d",
            "reason": "timeout",
        },
        {"ts": (now - timedelta(hours=48)).isoformat(), "from": "a", "to": "e", "reason": "old"},
    ]
    log.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")

    result = _collect_health_routes_24h()
    assert result["count_24h"] == 3
    assert result["top_reason"] == "quota"


def test_collect_routes_24h_no_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _setup_runtime_state(tmp_path, monkeypatch)
    result = _collect_health_routes_24h()
    assert result["count_24h"] == 0
    assert result["top_reason"] is None


def test_collect_catchup_reads_jsonl(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    base = _setup_runtime_state(tmp_path, monkeypatch)
    history = base / "catchup_history.jsonl"
    entries = [
        {
            "ts": "2026-05-10T01:20:00Z",
            "target_count": 2,
            "total_caught_up": 0,
            "total_skipped_self": 1,
        },
        {
            "ts": "2026-05-10T02:20:00Z",
            "target_count": 2,
            "total_caught_up": 5,
            "total_skipped_self": 2,
        },
    ]
    history.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")

    result = _collect_health_catchup()
    assert result["target_count"] == 2
    assert result["total_caught_up"] == 5
    assert result["total_skipped_self"] == 2


def test_collect_catchup_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _setup_runtime_state(tmp_path, monkeypatch)
    result = _collect_health_catchup()
    assert "error" in result


def test_collect_memory_swarm_entries_counted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = _setup_runtime_state(tmp_path, monkeypatch)
    swarm = base / "swarm_memory.json"
    swarm.write_text(
        json.dumps({"traders": [1, 2, 3], "coders": [{"x": 1}, {"y": 2}]}),
        encoding="utf-8",
    )
    result = _collect_health_memory()
    assert result["swarm_entries"] == 5


# ── format_health_detail ─────────────────────────────────────────────────────


def test_format_health_detail_includes_all_sections() -> None:
    data = {
        "core": {"pid": 12345, "uptime_sec": 8040, "rss_mb": 287.5},
        "gateway": {"healthy": True, "latency_ms": 8, "status_code": 200},
        "mcps": {"count": 3, "names": ["context7", "github", "sentry"]},
        "catchup": {
            "ts": "2026-05-10T01:20:00Z",
            "target_count": 2,
            "total_caught_up": 0,
            "total_skipped_self": 1,
        },
        "snapshots": {"count": 3, "total_mb": 2.2, "last_ts": "20260510T012000Z"},
        "routes": {"count_24h": 4, "top_reason": "quota"},
        "memory": {"rss_mb": 287.5, "swarm_entries": 137},
        "alerts": {"available": True, "active": 0},
    }
    out = format_health_detail(data)
    assert "Krab Health Detail" in out
    assert "PID 12345" in out
    assert "RSS 287.5MB" in out
    assert "Gateway" in out and "8ms" in out
    assert "MCPs" in out and "context7" in out
    assert "Catchup" in out
    assert "Snapshots" in out and "3 backups" in out
    assert "Route switches (24h)" in out and "4" in out
    assert "top reason: quota" in out
    assert "swarm_memory 137 entries" in out
    assert "Alerts" in out


def test_format_health_detail_handles_missing_state_files() -> None:
    """Gateway down + missing files должны рендериться gracefully."""
    data = {
        "core": {"pid": 1, "uptime_sec": 0, "rss_mb": None},
        "gateway": {"healthy": False, "error": "refused", "latency_ms": 2000},
        "mcps": {"count": 0, "names": [], "error": "openclaw CLI not found"},
        "catchup": {"error": "no catchup_history.jsonl"},
        "snapshots": {"count": 0, "total_mb": 0, "last_ts": None},
        "routes": {"count_24h": 0, "top_reason": None},
        "memory": {"rss_mb": None, "swarm_entries": 0},
        "alerts": {"available": False},
    }
    out = format_health_detail(data)
    assert "❌" in out  # error markers present
    assert "Gateway" in out and "refused" in out
    # No crash — that's the contract.
    assert len(out) < 4000


# ── handle_health backward compat + detail gate ─────────────────────────────


def _short_health_patches(monkeypatch: pytest.MonkeyPatch) -> None:
    """Standard mocks used by short-form !health (parity with existing tests)."""
    import src.handlers.command_handlers as mod

    monkeypatch.setattr(mod.openclaw_client, "health_check", AsyncMock(return_value=True))
    monkeypatch.setattr(
        mod.openclaw_client, "get_last_runtime_route", lambda: {"model": "gemini-test"}
    )
    monkeypatch.setattr(mod, "is_lm_studio_available", AsyncMock(return_value=True))
    monkeypatch.setattr(
        mod.inbox_service, "get_summary", lambda: {"attention_items": 0, "open_items": 0}
    )
    monkeypatch.setattr(mod.config, "SCHEDULER_ENABLED", True, raising=False)
    monkeypatch.setattr(mod.config, "LM_STUDIO_URL", "http://localhost:1234", raising=False)
    monkeypatch.setattr(mod.config, "VOICE_REPLY_VOICE", "ru-RU-Test", raising=False)


@pytest.mark.asyncio
async def test_health_default_keeps_short_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`!health` без аргументов — старый short формат, никаких detail-секций."""
    _short_health_patches(monkeypatch)
    bot = _make_bot()
    bot._get_command_args = lambda _msg: ""
    msg = _make_message("!health")

    mock_sched = MagicMock()
    mock_sched.list_jobs.return_value = [1, 2]
    mock_rl = MagicMock()
    mock_rl.stats.return_value = {"current_in_window": 1, "max_per_sec": 20}

    with (
        patch("src.core.swarm_bus.TEAM_REGISTRY", {"traders": []}),
        patch("src.core.swarm_scheduler.swarm_scheduler", mock_sched),
        patch("src.core.telegram_rate_limiter.telegram_rate_limiter", mock_rl),
    ):
        await handle_health(bot, msg)

    msg.reply.assert_awaited_once()
    report = msg.reply.call_args[0][0]
    # Short output marker (старый header)
    assert "Health Check" in report
    # Detail-only marker НЕ должен присутствовать
    assert "Krab Health Detail" not in report


@pytest.mark.asyncio
async def test_health_detail_owner_only_blocks_non_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-owner получает UserInputError при попытке !health detail."""
    from src.core.exceptions import UserInputError

    bot = _make_bot(owner_level=False)
    bot._get_command_args = lambda _msg: "detail"
    msg = _make_message("!health detail")

    with pytest.raises(UserInputError):
        await handle_health(bot, msg)


@pytest.mark.asyncio
async def test_health_detail_includes_all_sections(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Owner !health detail вызывает collect+format и шлёт в reply."""
    _setup_runtime_state(tmp_path, monkeypatch)

    bot = _make_bot(owner_level=True)
    bot._get_command_args = lambda _msg: "detail"
    msg = _make_message("!health detail")

    fake_data = {
        "core": {"pid": 999, "uptime_sec": 60, "rss_mb": 100.0},
        "gateway": {"healthy": True, "latency_ms": 10, "status_code": 200},
        "mcps": {"count": 1, "names": ["context7"]},
        "catchup": {
            "ts": "2026-05-10T01:00:00Z",
            "target_count": 1,
            "total_caught_up": 0,
            "total_skipped_self": 0,
        },
        "snapshots": {"count": 0, "total_mb": 0, "last_ts": None},
        "routes": {"count_24h": 0, "top_reason": None},
        "memory": {"rss_mb": 100.0, "swarm_entries": 0},
        "alerts": {"available": False},
    }

    with patch(
        "src.core.health_detail_collector.collect_health_detail",
        AsyncMock(return_value=fake_data),
    ):
        await handle_health(bot, msg)

    msg.reply.assert_awaited_once()
    report = msg.reply.call_args[0][0]
    assert "Krab Health Detail" in report
    assert "PID 999" in report


@pytest.mark.asyncio
async def test_health_full_alias_works(monkeypatch: pytest.MonkeyPatch) -> None:
    """`!health full` — синоним detail, поведение идентично."""
    bot = _make_bot(owner_level=True)
    bot._get_command_args = lambda _msg: "full"
    msg = _make_message("!health full")

    fake_data = {
        "core": {"pid": 1, "uptime_sec": 0, "rss_mb": None},
        "gateway": {"healthy": False, "error": "x", "latency_ms": 0},
        "mcps": {"count": 0, "names": []},
        "catchup": {"error": "missing"},
        "snapshots": {"count": 0, "total_mb": 0, "last_ts": None},
        "routes": {"count_24h": 0, "top_reason": None},
        "memory": {"rss_mb": None, "swarm_entries": 0},
        "alerts": {"available": False},
    }
    with patch(
        "src.core.health_detail_collector.collect_health_detail",
        AsyncMock(return_value=fake_data),
    ):
        await handle_health(bot, msg)

    msg.reply.assert_awaited_once()
    assert "Krab Health Detail" in msg.reply.call_args[0][0]


@pytest.mark.asyncio
async def test_collect_health_detail_aggregator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end: aggregator собирает все секции с реальными tmp state."""
    base = _setup_runtime_state(tmp_path, monkeypatch)
    # Минимальный snapshots/routes/swarm
    snap = base / "snapshots" / "20260510T000000Z"
    snap.mkdir(parents=True)
    (snap / "x.bak").write_bytes(b"a" * 100)
    (base / "swarm_memory.json").write_text('{"t": [1, 2]}', encoding="utf-8")

    # Mock-аем сетевые вызовы (gateway + alerts) на failure
    class _BoomClient:
        def __init__(self, *_a, **_kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, _url):
            raise ConnectionError("no")

    with (
        patch("httpx.AsyncClient", _BoomClient),
        patch("subprocess.run", side_effect=FileNotFoundError),
    ):
        data = await collect_health_detail(session_start_time=time.time() - 10)

    assert "core" in data
    assert "gateway" in data
    assert data["gateway"]["healthy"] is False
    assert data["snapshots"]["count"] == 1
    assert data["memory"]["swarm_entries"] == 2
