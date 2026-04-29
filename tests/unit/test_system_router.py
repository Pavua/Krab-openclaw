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
    with (
        patch("src.core.chat_ban_cache.chat_ban_cache.list_entries", return_value=[]),
        patch(
            "src.core.chat_capability_cache.chat_capability_cache.list_entries",
            return_value=[],
        ),
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
    with (
        patch(
            "src.core.chat_ban_cache.chat_ban_cache.list_entries",
            return_value=[{"chat_id": "1"}, {"chat_id": "2"}],
        ),
        patch(
            "src.core.chat_capability_cache.chat_capability_cache.list_entries",
            return_value=[
                {"voice_allowed": False, "slow_mode_seconds": 30},
                {"voice_allowed": True, "slow_mode_seconds": 0},
                {"voice_allowed": False},
            ],
        ),
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


# ---------------------------------------------------------------------------
# Wave AA: POST /api/runtime/chat-session/clear
# ---------------------------------------------------------------------------


class _FakeOpenclaw:
    def __init__(self) -> None:
        self.cleared: list[str] = []

    def clear_session(self, chat_id: str) -> None:
        self.cleared.append(chat_id)


def test_runtime_chat_session_clear_ok(monkeypatch) -> None:
    monkeypatch.delenv("WEB_API_KEY", raising=False)
    fake_oc = _FakeOpenclaw()
    client = _make_client(deps_overrides={"openclaw_client": fake_oc})
    resp = client.post("/api/runtime/chat-session/clear", json={"chat_id": "777", "note": "ops"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["action"] == "clear_chat_session"
    assert body["chat_id"] == "777"
    assert body["note"] == "ops"
    assert "runtime_after" in body
    assert fake_oc.cleared == ["777"]


def test_runtime_chat_session_clear_missing_chat_id(monkeypatch) -> None:
    monkeypatch.delenv("WEB_API_KEY", raising=False)
    client = _make_client(deps_overrides={"openclaw_client": _FakeOpenclaw()})
    resp = client.post("/api/runtime/chat-session/clear", json={})
    assert resp.status_code == 400
    assert resp.json()["detail"] == "chat_id_required"


def test_runtime_chat_session_clear_unsupported(monkeypatch) -> None:
    monkeypatch.delenv("WEB_API_KEY", raising=False)
    client = _make_client(deps_overrides={"openclaw_client": None})
    resp = client.post("/api/runtime/chat-session/clear", json={"chat_id": "1"})
    assert resp.status_code == 503
    assert resp.json()["detail"] == "chat_session_clear_not_supported"


def test_runtime_chat_session_clear_invalid_auth(monkeypatch) -> None:
    monkeypatch.setenv("WEB_API_KEY", "secret-key")
    client = _make_client(deps_overrides={"openclaw_client": _FakeOpenclaw()})
    resp = client.post("/api/runtime/chat-session/clear", json={"chat_id": "1"})
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Wave QQ: POST /api/runtime/repair-active-shared-permissions
# ---------------------------------------------------------------------------


def test_runtime_repair_active_shared_permissions_ok(monkeypatch) -> None:
    monkeypatch.delenv("WEB_API_KEY", raising=False)
    calls: dict[str, Any] = {}

    def _fake_normalize(root: Any) -> dict:
        calls["root"] = root
        return {"ok": True, "fixed": 5}

    def _fake_health() -> dict:
        return {"non_writable_count": 0, "status": "ready"}

    client = _make_client(
        deps_overrides={
            "active_shared_root_helper": lambda: Path("/tmp/Краб-active"),
            "normalize_shared_worktree_permissions_helper": _fake_normalize,
            "active_shared_permission_health_helper": _fake_health,
        }
    )
    resp = client.post("/api/runtime/repair-active-shared-permissions")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["repair"] == {"ok": True, "fixed": 5}
    assert body["active_shared_permission_health"]["status"] == "ready"
    assert calls["root"] == Path("/tmp/Краб-active")


def test_runtime_repair_active_shared_permissions_failure(monkeypatch) -> None:
    monkeypatch.delenv("WEB_API_KEY", raising=False)
    client = _make_client(
        deps_overrides={
            "active_shared_root_helper": lambda: Path("/tmp/krab-active"),
            "normalize_shared_worktree_permissions_helper": lambda root: {
                "ok": False,
                "error": "permission_denied",
            },
            "active_shared_permission_health_helper": lambda: {
                "non_writable_count": 3,
                "status": "attention",
            },
        }
    )
    resp = client.post("/api/runtime/repair-active-shared-permissions")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert body["repair"]["error"] == "permission_denied"
    assert body["active_shared_permission_health"]["non_writable_count"] == 3


def test_runtime_repair_active_shared_permissions_invalid_auth(monkeypatch) -> None:
    monkeypatch.setenv("WEB_API_KEY", "secret-key")
    client = _make_client()
    resp = client.post("/api/runtime/repair-active-shared-permissions")
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Wave QQ: POST /api/runtime/recover
# ---------------------------------------------------------------------------


class _FakeOpenclawCloud:
    def __init__(
        self,
        *,
        switch_result: dict | None = None,
        probe_result: dict | None = None,
        switch_raises: Exception | None = None,
        probe_raises: Exception | None = None,
    ) -> None:
        self.switch_result = switch_result or {"ok": True, "tier": "paid"}
        self.probe_result = probe_result or {"reachable": True}
        self.switch_raises = switch_raises
        self.probe_raises = probe_raises
        self.switch_calls: list[str] = []

    async def switch_cloud_tier(self, tier: str) -> dict:
        self.switch_calls.append(tier)
        if self.switch_raises:
            raise self.switch_raises
        return dict(self.switch_result)

    async def get_cloud_runtime_check(self) -> dict:
        if self.probe_raises:
            raise self.probe_raises
        return dict(self.probe_result)


def test_runtime_recover_runs_default_steps_and_skips_optional(monkeypatch) -> None:
    monkeypatch.delenv("WEB_API_KEY", raising=False)
    invocations: list[Any] = []

    def _fake_run(script_path: Any, *, timeout_seconds: int) -> dict:
        invocations.append((script_path.name, timeout_seconds))
        return {"ok": True, "exit_code": 0, "stdout_tail": "done", "error": ""}

    client = _make_client(
        deps_overrides={
            "run_project_python_script_helper": _fake_run,
            "bool_env_helper": lambda value, default=False: (
                str(value).lower() in {"1", "true", "yes", "on"}
            ),
        }
    )
    resp = client.post("/api/runtime/recover", json={})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    steps_by_name = {s["step"]: s for s in body["steps"]}
    assert steps_by_name["openclaw_runtime_repair"]["ok"] is True
    assert steps_by_name["sync_openclaw_models"]["ok"] is True
    assert "switch_cloud_tier" not in steps_by_name
    assert body["cloud_runtime"] is None
    # Helper called with proper script names.
    names = [item[0] for item in invocations]
    assert "openclaw_runtime_repair.py" in names
    assert "sync_openclaw_models.py" in names


def test_runtime_recover_can_skip_default_steps(monkeypatch) -> None:
    monkeypatch.delenv("WEB_API_KEY", raising=False)

    def _fake_run(script_path: Any, *, timeout_seconds: int) -> dict:
        raise AssertionError("run_script must not be called when skipped")

    client = _make_client(deps_overrides={"run_project_python_script_helper": _fake_run})
    resp = client.post(
        "/api/runtime/recover",
        json={
            "run_openclaw_runtime_repair": False,
            "run_sync_openclaw_models": False,
        },
    )
    assert resp.status_code == 200
    steps = resp.json()["steps"]
    assert all(s.get("skipped") for s in steps)
    assert all(s["ok"] for s in steps)


def test_runtime_recover_force_tier_switches_via_openclaw(monkeypatch) -> None:
    monkeypatch.delenv("WEB_API_KEY", raising=False)
    fake_oc = _FakeOpenclawCloud(switch_result={"ok": True, "tier": "free"})

    client = _make_client(
        deps_overrides={
            "openclaw_client": fake_oc,
            "run_project_python_script_helper": lambda *a, **kw: {
                "ok": True,
                "exit_code": 0,
                "stdout_tail": "",
                "error": "",
            },
        }
    )
    resp = client.post(
        "/api/runtime/recover",
        json={
            "run_openclaw_runtime_repair": False,
            "run_sync_openclaw_models": False,
            "force_tier": "free",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    tier_step = next(s for s in body["steps"] if s["step"] == "switch_cloud_tier")
    assert tier_step["ok"] is True
    assert tier_step["requested_tier"] == "free"
    assert fake_oc.switch_calls == ["free"]


def test_runtime_recover_probe_cloud_runtime_returns_report(monkeypatch) -> None:
    monkeypatch.delenv("WEB_API_KEY", raising=False)
    fake_oc = _FakeOpenclawCloud(probe_result={"tier": "paid", "reachable": True})

    client = _make_client(
        deps_overrides={
            "openclaw_client": fake_oc,
            "run_project_python_script_helper": lambda *a, **kw: {
                "ok": True,
                "exit_code": 0,
                "stdout_tail": "",
                "error": "",
            },
        }
    )
    resp = client.post(
        "/api/runtime/recover",
        json={
            "run_openclaw_runtime_repair": False,
            "run_sync_openclaw_models": False,
            "probe_cloud_runtime": True,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["cloud_runtime"]["available"] is True
    assert body["cloud_runtime"]["report"]["tier"] == "paid"


def test_runtime_recover_invalid_auth(monkeypatch) -> None:
    monkeypatch.setenv("WEB_API_KEY", "secret-key")
    client = _make_client()
    resp = client.post("/api/runtime/recover", json={})
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# /api/runtime/handoff (Wave UU)
# ---------------------------------------------------------------------------


def _handoff_handoff_overrides(*, latest: Any = None) -> dict[str, Any]:
    """Базовый набор helpers для /api/runtime/handoff endpoint."""

    async def _safe_health(client: Any, *, source: str, timeout_sec: float = 3.0) -> dict:
        return {"ok": client is not None, "status": "ok", "source": source, "detail": {}}

    async def _translator_readiness(*, runtime_lite: Any = None) -> dict:
        return {"ready": True, "language_pair": "es-ru"}

    async def _capability_registry(*, runtime_lite: Any = None) -> dict:
        return {
            "summary": {"contours_total": 3},
            "policy_matrix": {"summary": {"rules": 5}},
            "contours": {"channels": {"summary": {"telegram": "ok"}}},
        }

    return {
        "runtime_handoff_safe_client_health_helper": _safe_health,
        "translator_readiness_snapshot": _translator_readiness,
        "capability_registry_snapshot_helper": _capability_registry,
        "runtime_handoff_latest_path_by_glob_helper": lambda pattern: latest,
        "runtime_handoff_git_snapshot_helper": lambda: {
            "branch": "feature-x",
            "head": "abc123",
            "status_short": "## feature-x",
        },
        "runtime_handoff_mask_secret_helper": lambda v: ("***" + v[-3:]) if v else "",
        "bool_env_helper": lambda value, default=False: (
            str(value or "").strip().lower() in {"1", "true", "yes", "on"}
        ),
    }


def test_runtime_handoff_returns_machine_readable_snapshot() -> None:
    """Handoff endpoint должен отдать единый JSON с health_lite/runtime/git."""
    overrides = _handoff_handoff_overrides()
    client = _make_client(deps_overrides=overrides)
    resp = client.get("/api/runtime/handoff", params={"probe_cloud_runtime": "0"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["git"]["branch"] == "feature-x"
    assert body["health_lite"]["status"] == "up"
    assert body["health_lite"]["telegram_session_state"] == "active"
    assert body["translator_readiness"]["ready"] is True
    assert body["capability_registry_summary"] == {"contours_total": 3}
    assert body["policy_matrix_summary"] == {"rules": 5}
    assert body["channel_capabilities_summary"] == {"telegram": "ok"}


def test_runtime_handoff_skips_cloud_probe_when_disabled() -> None:
    """probe_cloud_runtime=0 должен вернуть ok=True без cloud-probe."""
    client = _make_client(deps_overrides=_handoff_handoff_overrides())
    resp = client.get("/api/runtime/handoff", params={"probe_cloud_runtime": "0"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["cloud_runtime"]["available"] is False
    assert body["cloud_runtime"]["skipped"] is True
    assert body["cloud_runtime"]["reason"] == "probe_disabled"


def test_runtime_handoff_cloud_probe_unsupported_when_no_openclaw() -> None:
    """Без openclaw_client должен вернуть not_supported (не падать в 500)."""
    overrides = _handoff_handoff_overrides()
    client = _make_client(deps_overrides=overrides)
    resp = client.get("/api/runtime/handoff", params={"probe_cloud_runtime": "1"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["cloud_runtime"]["available"] is False
    assert body["cloud_runtime"]["error"] == "not_supported"


def test_runtime_handoff_masks_secrets(monkeypatch) -> None:
    """masked_secrets должны проходить через mask_helper, а не светить env."""
    monkeypatch.setenv("OPENCLAW_GATEWAY_TOKEN", "supersecret_token_12345")
    monkeypatch.setenv("WEB_API_KEY", "")
    monkeypatch.delenv("GEMINI_API_KEY_FREE", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY_PAID", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    overrides = _handoff_handoff_overrides()
    client = _make_client(deps_overrides=overrides)
    resp = client.get("/api/runtime/handoff", params={"probe_cloud_runtime": "0"})
    assert resp.status_code == 200
    masked = resp.json()["masked_secrets"]
    assert masked["openclaw_token"].endswith("345")
    assert masked["openclaw_token"].startswith("***")
    assert masked["web_api_key"] == ""


def test_runtime_handoff_includes_artifacts_paths(tmp_path) -> None:
    """artifacts должны включать project_root-based абсолютные пути для docs."""
    overrides = _handoff_handoff_overrides(latest=tmp_path / "handoff_2026")
    client = _make_client(deps_overrides=overrides)
    resp = client.get("/api/runtime/handoff", params={"probe_cloud_runtime": "0"})
    assert resp.status_code == 200
    artifacts = resp.json()["artifacts"]
    # latest_handoff_bundle_dir resolved через helper
    assert artifacts["latest_handoff_bundle_dir"].endswith("handoff_2026")
    # docs paths relative to project_root (".") в _make_client
    assert artifacts["master_plan_doc"].endswith("docs/MASTER_PLAN_VNEXT_RU.md")
    assert artifacts["translator_audit_doc"].endswith("docs/CALL_TRANSLATOR_AUDIT_RU.md")
    # latest_transfer_prompt — None если pack_dir/TRANSFER_PROMPT_RU.md не существует
    assert artifacts["latest_transfer_prompt"] is None


def test_runtime_handoff_safe_when_helpers_missing() -> None:
    """Endpoint не должен падать, если helpers отсутствуют (graceful degradation)."""
    overrides = {
        "runtime_handoff_safe_client_health_helper": None,
        "translator_readiness_snapshot": None,
        "capability_registry_snapshot_helper": None,
        "runtime_handoff_latest_path_by_glob_helper": None,
        "runtime_handoff_git_snapshot_helper": None,
        "runtime_handoff_mask_secret_helper": None,
        "bool_env_helper": None,
    }
    client = _make_client(deps_overrides=overrides)
    resp = client.get("/api/runtime/handoff", params={"probe_cloud_runtime": "0"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["translator_readiness"] == {}
    assert body["capability_registry_summary"] == {}
    assert body["git"] == {}
