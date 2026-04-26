# -*- coding: utf-8 -*-
"""
Тесты ``src.modules.web_routers.system_router`` — Phase 2 Wave Y (Session 25).

Покрывают factory-pattern: ``build_system_router(ctx)`` работает stand-alone
с mocked RouterContext. Контракт endpoint'ов сохранён 1:1 с inline
definitions из web_app.py.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.modules.web_routers._context import RouterContext
from src.modules.web_routers.system_router import build_system_router

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeRouter:
    def __init__(self, *, active_tier: str = "default") -> None:
        self.active_tier = active_tier
        self.local_engine = "lm_studio"
        self.rag = None


class _FakeHealthSvc:
    async def collect(self) -> dict:
        return {
            "resources": {"cpu_pct": 12.3, "ram_mb": 4096},
            "budget": {"used_eur": 1.5, "limit_eur": 10.0},
        }


class _FakeKraab:
    def get_translator_runtime_profile(self) -> dict:
        return {"language_pair": "es-ru", "mode": "bilingual"}

    def get_translator_session_state(self) -> dict:
        return {"active": False, "duration_sec": 0}

    def get_voice_blocked_chats(self) -> list:
        return ["chat-1", "chat-2"]


def _make_client(
    *,
    runtime_lite: dict[str, Any] | None = None,
    deps_overrides: dict[str, Any] | None = None,
    operator_profile: dict | None = None,
    stats_router_payload: dict | None = None,
    local_runtime_truth: dict | None = None,
) -> TestClient:
    snapshot = runtime_lite or {
        "telegram_session_state": "active",
        "lmstudio_model_state": "loaded",
        "openclaw_auth_state": "ok",
        "status": "up",
    }

    async def _runtime_lite(*, force_refresh: bool = False) -> dict[str, Any]:
        return dict(snapshot)

    profile = operator_profile or {"operator": "owner", "tier": "paid"}
    stats_payload = stats_router_payload or {"current_model": "gemini-3-pro", "tier": "cloud"}
    local_truth = local_runtime_truth or {
        "engine": "lm_studio",
        "active_model": "qwen2.5-coder",
        "runtime_reachable": True,
        "loaded_models": ["qwen2.5-coder"],
    }

    async def _build_stats_payload(_router: Any) -> dict:
        return dict(stats_payload)

    async def _resolve_local(_router: Any) -> dict:
        return dict(local_truth)

    deps: dict[str, Any] = {
        "router": _FakeRouter(),
        "health_service": _FakeHealthSvc(),
        "kraab_userbot": _FakeKraab(),
        "black_box": None,
        "watchdog": None,
        "openclaw_client": None,
        "voice_gateway_client": None,
        "krab_ear_client": None,
        "runtime_operator_profile_helper": lambda: dict(profile),
        "build_stats_router_payload_helper": _build_stats_payload,
        "resolve_local_runtime_truth_helper": _resolve_local,
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
    app.include_router(build_system_router(ctx))
    return TestClient(app)


# ---------------------------------------------------------------------------
# /api/runtime/operator-profile
# ---------------------------------------------------------------------------


def test_runtime_operator_profile_returns_helper_payload() -> None:
    client = _make_client(operator_profile={"operator": "kraab", "tier": "free"})
    resp = client.get("/api/runtime/operator-profile")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["profile"] == {"operator": "kraab", "tier": "free"}


def test_runtime_operator_profile_returns_empty_when_helper_missing() -> None:
    client = _make_client(deps_overrides={"runtime_operator_profile_helper": None})
    resp = client.get("/api/runtime/operator-profile")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "profile": {}}


# ---------------------------------------------------------------------------
# /api/runtime/summary
# ---------------------------------------------------------------------------


def test_runtime_summary_aggregates_runtime_lite_and_translator() -> None:
    client = _make_client()
    resp = client.get("/api/runtime/summary")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["health"]["telegram_session_state"] == "active"
    assert body["translator"]["profile"]["language_pair"] == "es-ru"
    assert body["translator"]["session"]["active"] is False
    assert "swarm" in body
    assert "silence" in body
    assert "notify_enabled" in body


def test_runtime_summary_handles_missing_kraab_gracefully() -> None:
    client = _make_client(deps_overrides={"kraab_userbot": None})
    resp = client.get("/api/runtime/summary")
    assert resp.status_code == 200
    body = resp.json()
    assert body["translator"] == {"profile": {}, "session": {}}


# ---------------------------------------------------------------------------
# /api/dashboard/summary
# ---------------------------------------------------------------------------


def test_dashboard_summary_calls_collector_with_boot_ts() -> None:
    client = _make_client()

    async def _fake_collector(*, boot_ts: float, router: Any) -> dict:
        return {"boot_ts": boot_ts, "router_present": router is not None}

    with patch(
        "src.core.dashboard_summary.collect_dashboard_summary_async",
        new=_fake_collector,
    ):
        resp = client.get("/api/dashboard/summary")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body["boot_ts"], (int, float))
    assert body["router_present"] is True


# ---------------------------------------------------------------------------
# /api/stats
# ---------------------------------------------------------------------------


def test_get_stats_returns_router_payload_and_disabled_blackbox() -> None:
    client = _make_client(stats_router_payload={"current_model": "test-model"})
    resp = client.get("/api/stats")
    assert resp.status_code == 200
    body = resp.json()
    assert body["router"] == {"current_model": "test-model"}
    assert body["black_box"] == {"enabled": False}
    assert body["rag"] == {"enabled": False, "count": 0}


def test_get_stats_uses_router_rag_when_present() -> None:
    class _RouterWithRag(_FakeRouter):
        def __init__(self) -> None:
            super().__init__()

            class _Rag:
                def get_stats(self) -> dict:
                    return {"enabled": True, "count": 7}

            self.rag = _Rag()

    client = _make_client(deps_overrides={"router": _RouterWithRag()})
    resp = client.get("/api/stats")
    assert resp.status_code == 200
    assert resp.json()["rag"] == {"enabled": True, "count": 7}


# ---------------------------------------------------------------------------
# /api/stats/caches
# ---------------------------------------------------------------------------


def test_stats_caches_returns_zero_counts_when_caches_empty() -> None:
    client = _make_client()
    # Внутренние модули могут быть импортированы; чтобы не зависеть от
    # глобального состояния, патчим оба cache module.
    with patch(
        "src.core.chat_ban_cache.chat_ban_cache.list_entries", return_value=[]
    ), patch(
        "src.core.chat_capability_cache.chat_capability_cache.list_entries",
        return_value=[],
    ):
        resp = client.get("/api/stats/caches")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ban_cache_count"] == 0
    assert body["capability_cache_count"] == 0
    # voice_blocked_count приходит из _FakeKraab
    assert body["voice_blocked_count"] == 2


def test_stats_caches_aggregates_capability_flags() -> None:
    client = _make_client()
    with patch(
        "src.core.chat_ban_cache.chat_ban_cache.list_entries",
        return_value=[{"chat_id": "1"}, {"chat_id": "2"}],
    ), patch(
        "src.core.chat_capability_cache.chat_capability_cache.list_entries",
        return_value=[
            {"voice_allowed": False, "slow_mode_seconds": 30},
            {"voice_allowed": True, "slow_mode_seconds": 0},
            {"voice_allowed": False},
        ],
    ):
        resp = client.get("/api/stats/caches")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ban_cache_count"] == 2
    assert body["capability_cache_count"] == 3
    assert body["capability_voice_disallowed"] == 2
    assert body["capability_slow_mode"] == 1


# ---------------------------------------------------------------------------
# /api/system/diagnostics
# ---------------------------------------------------------------------------


def test_system_diagnostics_ok_when_local_reachable() -> None:
    client = _make_client()
    resp = client.get("/api/system/diagnostics")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["status"] == "ok"
    assert body["resources"]["cpu_pct"] == 12.3
    assert body["budget"]["limit_eur"] == 10.0
    assert body["local_ai"]["available"] is True
    assert body["local_ai"]["model"] == "qwen2.5-coder"


def test_system_diagnostics_failed_when_local_unreachable_and_default_tier() -> None:
    client = _make_client(
        local_runtime_truth={"runtime_reachable": False, "active_model": ""},
    )
    resp = client.get("/api/system/diagnostics")
    assert resp.status_code == 200
    assert resp.json()["status"] == "failed"


def test_system_diagnostics_degraded_when_paid_tier() -> None:
    client = _make_client(
        deps_overrides={"router": _FakeRouter(active_tier="paid")},
    )
    resp = client.get("/api/system/diagnostics")
    assert resp.status_code == 200
    assert resp.json()["status"] == "degraded"


def test_system_diagnostics_returns_error_when_no_router() -> None:
    client = _make_client(deps_overrides={"router": None})
    resp = client.get("/api/system/diagnostics")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert body["error"] == "router_not_found"
