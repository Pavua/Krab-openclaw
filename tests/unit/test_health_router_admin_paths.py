# -*- coding: utf-8 -*-
"""S65 W5: Cover edge cases / failure paths in health_router.

Targets previously-uncovered branches:
- _resolve_log_file_path with KRAB_LOG_FILE override (line 68-72)
- _parse_log_line edge cases: empty/no-ts/no-brackets/no-event (84-104)
- _coerce_int(None) (118)
- _collect_verifier_samples OSError fallback (180-181)
- _verifier_sample_rate parse error (231-232)
- /api/health/lite when telegram_rate_limiter import fails (339-340)
- /api/ecosystem/health/debug router=None and inner exception (437-442, 459-462)
- /api/network/probes split_brain probe object + exception (525, 528-530)
- /api/network/probes dispatcher exception (541-543)
- /api/network/probes pyrogram metrics exception (567-568)
- /api/admin/local-draft-verifier-stats exception fallback (600-602)
"""

from __future__ import annotations

import builtins
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.modules.web_routers._context import RouterContext
from src.modules.web_routers.health_router import (
    _coerce_int,
    _collect_verifier_samples,
    _parse_log_line,
    _resolve_log_file_path,
    _verifier_sample_rate,
    build_health_router,
    collect_local_draft_verifier_stats,
)

# ---------------------------------------------------------------------------
# Pure-function edge cases
# ---------------------------------------------------------------------------


def test_resolve_log_file_path_honors_env_override(monkeypatch, tmp_path) -> None:
    """KRAB_LOG_FILE env var должен переопределить путь (line 68-72)."""
    override = tmp_path / "custom.log"
    monkeypatch.setenv("KRAB_LOG_FILE", str(override))
    assert _resolve_log_file_path() == override


def test_parse_log_line_handles_empty_and_malformed() -> None:
    """_parse_log_line robust к мусорным строкам (lines 84-104)."""
    # Empty after strip
    assert _parse_log_line("") is None
    assert _parse_log_line("   \n\t") is None
    # No timestamp at start
    assert _parse_log_line("not_a_timestamp_line foo=bar") is None
    # Bracket without closing
    assert _parse_log_line("2026-05-18 03:17:39 [info event_name") is None
    # Only timestamp + bracket → no parts after
    assert _parse_log_line("2026-05-18 03:17:39 [info     ]   ") is None


def test_parse_log_line_extracts_kv_pairs() -> None:
    """Sanity: happy path остаётся рабочим."""
    parsed = _parse_log_line(
        "2026-05-18 03:17:39 [info     ] some_event    foo=bar baz='quoted val'"
    )
    assert parsed is not None
    assert parsed["event"] == "some_event"
    assert parsed["foo"] == "bar"
    assert parsed["baz"] == "quoted val"


def test_coerce_int_returns_none_for_none_and_garbage() -> None:
    """line 117-118 + 121-122."""
    assert _coerce_int(None) is None
    assert _coerce_int("not_a_number") is None
    assert _coerce_int("42") == 42


def test_verifier_sample_rate_handles_parse_error(monkeypatch) -> None:
    """line 231-232: невалидное значение → дефолт 0.2."""
    monkeypatch.setenv("KRAB_LOCAL_DRAFT_VERIFY_SAMPLE_RATE", "not_a_float")
    assert _verifier_sample_rate() == 0.2


def test_verifier_sample_rate_clamps_to_unit_interval(monkeypatch) -> None:
    """Sanity: rate clamps [0, 1]."""
    monkeypatch.setenv("KRAB_LOCAL_DRAFT_VERIFY_SAMPLE_RATE", "2.5")
    assert _verifier_sample_rate() == 1.0
    monkeypatch.setenv("KRAB_LOCAL_DRAFT_VERIFY_SAMPLE_RATE", "-1.0")
    assert _verifier_sample_rate() == 0.0


def test_collect_verifier_samples_handles_oserror(tmp_path, monkeypatch) -> None:
    """line 180-181: log open() OSError → warning + empty samples."""
    log_path = tmp_path / "krab_main.log"
    log_path.write_text("dummy content\n")

    def _raise_oserror(*args, **kwargs):
        raise OSError("permission denied")

    monkeypatch.setattr(Path, "open", _raise_oserror)
    samples, warnings = _collect_verifier_samples(log_path, now=1_700_000_000.0)
    assert samples == []
    assert any("log_read_error" in w for w in warnings)


def test_collect_verifier_samples_missing_file(tmp_path) -> None:
    """line 150-152: лог отсутствует → warning."""
    samples, warnings = _collect_verifier_samples(tmp_path / "nonexistent.log", now=1_700_000_000.0)
    assert samples == []
    assert any("log_file_missing" in w for w in warnings)


# ---------------------------------------------------------------------------
# Endpoint failure / fallback paths
# ---------------------------------------------------------------------------


class _FakeRouter:
    pass


def _make_client(deps_overrides: dict[str, Any] | None = None) -> TestClient:
    snapshot = {
        "lmstudio_model_state": "loaded",
        "telegram_session_state": "active",
        "telegram_userbot": {
            "startup_state": "ready",
            "client_connected": True,
            "startup_error_code": None,
        },
        "openclaw_auth_state": "ok",
        "last_runtime_route": {"channel": "cloud"},
        "scheduler_enabled": True,
        "inbox_summary": {"open": 0},
        "voice_gateway_configured": True,
        "status": "up",
    }

    async def _runtime_lite(*, force_refresh: bool = False) -> dict[str, Any]:
        return dict(snapshot)

    deps: dict[str, Any] = {
        "router": _FakeRouter(),
        "openclaw_client": None,
        "voice_gateway_client": None,
        "krab_ear_client": None,
        "health_service": None,
    }
    if deps_overrides:
        deps.update(deps_overrides)

    ctx = RouterContext(
        deps=deps,
        project_root=Path("."),
        web_api_key_fn=lambda: None,
        assert_write_access_fn=lambda *a, **kw: None,
        runtime_lite_provider=_runtime_lite,
    )
    app = FastAPI()
    app.include_router(build_health_router(ctx))
    return TestClient(app, raise_server_exceptions=False)


def test_health_lite_handles_rate_limiter_import_failure() -> None:
    """line 339-340: telegram_rate_limiter import error → result без поля."""
    real_import = builtins.__import__

    def _fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if "telegram_rate_limiter" in name:
            raise ImportError("simulated")
        return real_import(name, globals, locals, fromlist, level)

    client = _make_client()
    with (
        patch(
            "src.modules.web_app._resolve_memory_indexer_state",
            return_value="running",
        ),
        patch(
            "src.modules.web_app._resolve_memory_indexer_queue_size",
            return_value=0,
        ),
        patch("builtins.__import__", side_effect=_fake_import),
    ):
        resp = client.get("/api/health/lite")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    # rate_limiter поля не должно быть, т.к. import упал
    assert "telegram_rate_limiter" not in body


def test_ecosystem_health_debug_router_not_in_deps() -> None:
    """line 437-439: health_svc + router None → error envelope."""
    client = _make_client(deps_overrides={"health_service": None, "router": None})
    resp = client.get("/api/ecosystem/health/debug")
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("error") == "router_not_found_in_deps"


def test_ecosystem_health_debug_inner_exception_returns_trace() -> None:
    """line 459-462: исключение в collect() → error + trace."""

    class _BoomSvc:
        def _collect_session_12_stats(self) -> dict:
            return {}

        async def collect(self):
            raise RuntimeError("kaboom")

    client = _make_client(deps_overrides={"health_service": _BoomSvc()})
    resp = client.get("/api/ecosystem/health/debug")
    assert resp.status_code == 200
    body = resp.json()
    assert "kaboom" in body.get("error", "")
    assert "trace" in body


def test_network_probes_split_brain_from_probe_object() -> None:
    """line 522-525: _last_get_state_probe.split_brain_suspected → True."""
    probe = SimpleNamespace(split_brain_suspected=True)
    userbot = SimpleNamespace(
        _last_telegram_event_ts=0.0,
        _last_get_state_probe=probe,
        _last_dispatcher_tick_ts=0.0,
        _dispatcher_tick_count=0,
    )
    client = _make_client(deps_overrides={"kraab_userbot": userbot})
    resp = client.get("/api/network/probes")
    assert resp.status_code == 200
    body = resp.json()
    assert body["main_app"]["split_brain"] is True


def test_network_probes_split_brain_attribute_access_raises() -> None:
    """line 528-530: исключение при чтении probe атрибутов → split_brain=False."""

    class _BadProbe:
        @property
        def split_brain_suspected(self):
            raise RuntimeError("attr boom")

    class _BadUserbot:
        _last_telegram_event_ts = 0.0
        _last_get_state_probe = _BadProbe()
        _last_dispatcher_tick_ts = 0.0
        _dispatcher_tick_count = 0

        # Делаем доступ к атрибутам ошибочным через __getattr__ для split_brain
        @property
        def _split_brain_suspected(self):
            raise RuntimeError("split brain boom")

    client = _make_client(deps_overrides={"kraab_userbot": _BadUserbot()})
    resp = client.get("/api/network/probes")
    assert resp.status_code == 200
    body = resp.json()
    # fail-open → split_brain False
    assert body["main_app"]["split_brain"] is False


def test_network_probes_dispatcher_check_failure_returns_false() -> None:
    """line 541-543: _check_dispatcher_starved raises → starved=False."""
    userbot = SimpleNamespace(
        _last_telegram_event_ts=0.0,
        _last_dispatcher_tick_ts=0.0,
        _dispatcher_tick_count=0,
    )

    def _raise(*args, **kwargs):
        raise RuntimeError("dispatcher boom")

    client = _make_client(deps_overrides={"kraab_userbot": userbot})
    with patch(
        "src.userbot.network_watchdog._check_dispatcher_starved",
        side_effect=_raise,
    ):
        resp = client.get("/api/network/probes")
    assert resp.status_code == 200
    body = resp.json()
    assert body["dispatcher_tick"]["starved"] is False


def test_network_probes_pyrogram_metrics_failure_returns_defaults() -> None:
    """line 567-568: prometheus_metrics import failure → defaults."""
    userbot = SimpleNamespace(
        _last_telegram_event_ts=0.0,
        _last_dispatcher_tick_ts=0.0,
        _dispatcher_tick_count=0,
    )
    real_import = builtins.__import__

    def _fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if "prometheus_metrics" in name:
            raise ImportError("simulated")
        return real_import(name, globals, locals, fromlist, level)

    client = _make_client(deps_overrides={"kraab_userbot": userbot})
    with patch("builtins.__import__", side_effect=_fake_import):
        resp = client.get("/api/network/probes")
    assert resp.status_code == 200
    body = resp.json()
    assert body["pyrogram"]["disconnects_total"] == 0
    assert body["pyrogram"]["session_label"] == "unknown"


def test_local_draft_verifier_endpoint_exception_returns_fallback() -> None:
    """line 600-602: collect_local_draft_verifier_stats raises → fallback envelope."""
    client = _make_client()
    with patch(
        "src.modules.web_routers.health_router.collect_local_draft_verifier_stats",
        side_effect=RuntimeError("collector boom"),
    ):
        resp = client.get("/api/admin/local-draft-verifier-stats")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert body["stats"]["total_verified_24h"] == 0
    assert body["stats"]["mean_score"] is None
    assert any("endpoint_error" in w for w in body["warnings"])


def test_local_draft_verifier_stats_happy_path_zero_samples(tmp_path, monkeypatch) -> None:
    """Sanity: collect_local_draft_verifier_stats без событий → zeroed."""
    monkeypatch.setenv("KRAB_LOG_FILE", str(tmp_path / "empty.log"))
    # Force re-parse (cache TTL=0)
    result = collect_local_draft_verifier_stats(cache_ttl_sec=0.0)
    assert result["ok"] is True
    assert result["stats"]["total_verified_24h"] == 0
    assert result["stats"]["mean_score"] is None
