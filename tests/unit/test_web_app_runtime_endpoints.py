# -*- coding: utf-8 -*-
"""
Тесты runtime endpoint'ов web-панели.

Покрываем:
1) расширенный `/api/health/lite`;
2) `GET /api/runtime/handoff`;
3) `POST /api/runtime/recover` (guard + успешный dry-like запуск).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from src.config import config
from src.modules.web_app import WebApp


class _DummyRouter:
    """Минимальный роутер-заглушка для инициализации WebApp."""

    def get_model_info(self):
        return {}


class _PhotoModel:
    """Минимальная модель для photo-smoke тестов."""

    def __init__(self, *, supports_vision: bool, model_type: str):
        self.supports_vision = supports_vision
        self.type = model_type


class _PhotoModelManager:
    """Минимальный model manager для проверки `/api/openclaw/photo-smoke`."""

    async def discover_models(self):
        return [
            _PhotoModel(supports_vision=True, model_type="local_mlx"),
            _PhotoModel(supports_vision=False, model_type="local_gguf"),
        ]

    async def get_best_model(self, *, has_photo: bool = False):
        assert has_photo is True
        return "lmstudio/local-vision-model"

    def is_local_model(self, model_id: str) -> bool:
        return model_id.startswith("lmstudio/")


class _PhotoRouter(_DummyRouter):
    """Роутер-заглушка с подключенным model manager."""

    def __init__(self) -> None:
        self._mm = _PhotoModelManager()
        self.is_local_available = True


class _FakeOpenClaw:
    """Фейковый OpenClaw клиент для детерминированных тестов runtime endpoint'ов."""

    async def health_check(self) -> bool:
        return True

    def get_last_runtime_route(self):
        return {
            "channel": "local_direct",
            "provider": "nvidia",
            "model": "nvidia/nemotron-3-nano",
            "status": "ok",
            "error_code": None,
        }

    def get_tier_state_export(self):
        return {
            "active_tier": "free",
            "last_error_code": None,
            "last_provider_status": "ok",
            "last_recovery_action": "none",
        }

    async def get_cloud_runtime_check(self):
        return {"ok": True, "provider": "google", "active_tier": "free"}

    async def switch_cloud_tier(self, tier: str):
        return {"ok": True, "new_tier": tier}


class _FakeHealthClient:
    """Фейковый клиент сервиса с `health_check`."""

    def __init__(self, ok: bool = True):
        self._ok = ok

    async def health_check(self) -> bool:
        return self._ok


def _make_client(*, openclaw_client=None) -> TestClient:
    deps = {
        "router": _DummyRouter(),
        "openclaw_client": openclaw_client or _FakeOpenClaw(),
        "black_box": None,
        "health_service": None,
        "provisioning_service": None,
        "ai_runtime": None,
        "reaction_engine": None,
        "voice_gateway_client": _FakeHealthClient(ok=True),
        "krab_ear_client": _FakeHealthClient(ok=True),
        "perceptor": None,
        "watchdog": None,
        "queue": None,
    }
    app = WebApp(deps, port=18080, host="127.0.0.1")
    return TestClient(app.app)


def _make_client_with_router(router, *, openclaw_client=None) -> TestClient:
    deps = {
        "router": router,
        "openclaw_client": openclaw_client or _FakeOpenClaw(),
        "black_box": None,
        "health_service": None,
        "provisioning_service": None,
        "ai_runtime": None,
        "reaction_engine": None,
        "voice_gateway_client": _FakeHealthClient(ok=True),
        "krab_ear_client": _FakeHealthClient(ok=True),
        "perceptor": None,
        "watchdog": None,
        "queue": None,
    }
    app = WebApp(deps, port=18080, host="127.0.0.1")
    return TestClient(app.app)


def test_health_lite_contains_runtime_fields(monkeypatch):
    """
    `/api/health/lite` должен содержать новые runtime-поля,
    даже если внешний контур (LM Studio) в тесте недоступен.
    """
    monkeypatch.setenv("LM_STUDIO_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("OPENCLAW_TOKEN", "test-token")
    client = _make_client()

    resp = client.get("/api/health/lite")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["status"] == "up"
    assert "telegram_session_state" in data
    assert "lmstudio_model_state" in data
    assert "openclaw_auth_state" in data
    assert "last_runtime_route" in data
    assert "scheduler_enabled" in data
    assert "voice_gateway_configured" in data


def test_health_reuses_lite_local_truth_without_router_health_probe(monkeypatch):
    """`/api/health` не должен снова дёргать router.health_check, если local truth уже известен из lite-snapshot."""

    class _RouterWithFailingHealth(_DummyRouter):
        async def health_check(self):
            raise AssertionError("router.health_check не должен вызываться из /api/health")

    async def _fake_lite_snapshot(self):
        return {
            "telegram_session_state": "ready",
            "telegram_session": {"state": "ready"},
            "lmstudio_model_state": "loaded",
            "lmstudio": {"state": "loaded", "loaded_models": ["nvidia/nemotron-3-nano"]},
            "openclaw_auth_state": "configured",
            "last_runtime_route": {},
            "openclaw_tier_state": {},
            "telegram_userbot": {},
            "scheduler_enabled": True,
            "voice_gateway_configured": True,
        }

    monkeypatch.setattr(WebApp, "_collect_runtime_lite_snapshot", _fake_lite_snapshot)
    client = _make_client_with_router(_RouterWithFailingHealth())

    resp = client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["checks"]["local_lm"] is True


def test_lmstudio_snapshot_short_cache_reuses_probe_and_invalidates(monkeypatch):
    """Короткий cache LM Studio snapshot должен схлопывать burst-чтения и сбрасываться вручную."""

    class _Resp:
        def __init__(self, status_code: int, payload: dict):
            self.status_code = status_code
            self._payload = payload

        def json(self):
            return self._payload

    call_counter = {"get": 0}

    class _AsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url: str):
            call_counter["get"] += 1
            return _Resp(
                200,
                {
                    "data": [
                        {
                            "id": "nvidia/nemotron-3-nano",
                            "loaded_instances": [{"id": "nvidia/nemotron-3-nano"}],
                        }
                    ]
                },
            )

    monkeypatch.setenv("LM_STUDIO_URL", "http://127.0.0.1:1234")
    monkeypatch.setenv("WEB_LMSTUDIO_SNAPSHOT_TTL_SEC", "5")
    monkeypatch.setattr("src.modules.web_app.httpx.AsyncClient", _AsyncClient)

    app = WebApp({"router": _DummyRouter()}, port=18080, host="127.0.0.1")

    first = asyncio.run(app._lmstudio_model_snapshot())
    second = asyncio.run(app._lmstudio_model_snapshot())

    assert first["state"] == "loaded"
    assert second["state"] == "loaded"
    assert call_counter["get"] == 1

    app._invalidate_lmstudio_snapshot_cache()
    third = asyncio.run(app._lmstudio_model_snapshot())

    assert third["state"] == "loaded"
    assert call_counter["get"] == 2


def test_lmstudio_snapshot_loaded_state_uses_extended_ttl(monkeypatch):
    """Для loaded-state snapshot должен жить дольше базового TTL и не долбить LM Studio без пользы."""

    class _Resp:
        def __init__(self, status_code: int, payload: dict):
            self.status_code = status_code
            self._payload = payload

        def json(self):
            return self._payload

    call_counter = {"get": 0}

    class _AsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url: str):
            call_counter["get"] += 1
            return _Resp(
                200,
                {
                    "data": [
                        {
                            "id": "nvidia/nemotron-3-nano",
                            "loaded_instances": [{"id": "nvidia/nemotron-3-nano"}],
                        }
                    ]
                },
            )

    time_marks = iter([100.0, 100.0, 100.1, 114.0, 121.0, 121.0, 121.1])

    def _fake_time() -> float:
        try:
            return next(time_marks)
        except StopIteration:
            return 121.1

    monkeypatch.setenv("LM_STUDIO_URL", "http://127.0.0.1:1234")
    monkeypatch.setenv("WEB_LMSTUDIO_SNAPSHOT_TTL_SEC", "2")
    monkeypatch.setenv("WEB_LMSTUDIO_SNAPSHOT_TTL_LOADED_SEC", "20")
    monkeypatch.setattr("src.modules.web_app.httpx.AsyncClient", _AsyncClient)
    monkeypatch.setattr("src.modules.web_app.time.time", _fake_time)

    app = WebApp({"router": _DummyRouter()}, port=18080, host="127.0.0.1")

    first = asyncio.run(app._lmstudio_model_snapshot())
    second = asyncio.run(app._lmstudio_model_snapshot())
    third = asyncio.run(app._lmstudio_model_snapshot())

    assert first["state"] == "loaded"
    assert second["state"] == "loaded"
    assert third["state"] == "loaded"
    assert call_counter["get"] == 2


def test_runtime_lite_snapshot_loaded_state_uses_extended_ttl(monkeypatch):
    """Агрегированный runtime-lite snapshot тоже должен схлопывать частые health/lite тики."""

    build_counter = {"calls": 0}
    time_marks = iter([100.0, 100.0, 100.1, 114.0, 121.0, 121.0, 121.1])

    async def _fake_build_runtime_lite(self):
        build_counter["calls"] += 1
        return {
            "telegram_session_state": "ready",
            "telegram_session": {"state": "ready"},
            "lmstudio_model_state": "loaded",
            "lmstudio": {"state": "loaded", "loaded_models": ["nvidia/nemotron-3-nano"]},
            "openclaw_auth_state": "configured",
            "last_runtime_route": {},
            "openclaw_tier_state": {},
            "telegram_userbot": {"startup_state": "running"},
            "scheduler_enabled": True,
            "voice_gateway_configured": True,
        }

    def _fake_time() -> float:
        try:
            return next(time_marks)
        except StopIteration:
            return 121.1

    monkeypatch.setenv("WEB_RUNTIME_LITE_TTL_SEC", "2")
    monkeypatch.setenv("WEB_RUNTIME_LITE_TTL_LOADED_SEC", "20")
    monkeypatch.setattr(WebApp, "_build_runtime_lite_snapshot_uncached", _fake_build_runtime_lite)
    monkeypatch.setattr("src.modules.web_app.time.time", _fake_time)

    app = WebApp({"router": _DummyRouter()}, port=18080, host="127.0.0.1")

    first = asyncio.run(app._collect_runtime_lite_snapshot())
    second = asyncio.run(app._collect_runtime_lite_snapshot())
    third = asyncio.run(app._collect_runtime_lite_snapshot())

    assert first["lmstudio_model_state"] == "loaded"
    assert second["lmstudio_model_state"] == "loaded"
    assert third["lmstudio_model_state"] == "loaded"
    assert build_counter["calls"] == 2


def test_loaded_state_default_ttl_is_relaxed_for_live_dashboard(monkeypatch):
    """Loaded-state по умолчанию должен жить дольше, чтобы не шуметь LM Studio каждые 10-20 секунд."""

    for name in (
        "WEB_LMSTUDIO_SNAPSHOT_TTL_SEC",
        "WEB_LMSTUDIO_SNAPSHOT_TTL_LOADED_SEC",
        "WEB_LMSTUDIO_SNAPSHOT_TTL_IDLE_SEC",
        "WEB_RUNTIME_LITE_TTL_SEC",
        "WEB_RUNTIME_LITE_TTL_LOADED_SEC",
        "WEB_RUNTIME_LITE_TTL_IDLE_SEC",
    ):
        monkeypatch.delenv(name, raising=False)

    assert WebApp._lmstudio_snapshot_ttl_sec_for_state("loaded") == 60.0
    assert WebApp._lmstudio_snapshot_ttl_sec_for_state("idle") == 20.0
    assert WebApp._runtime_lite_ttl_sec_for_state("loaded") == 60.0
    assert WebApp._runtime_lite_ttl_sec_for_state("idle") == 20.0


def test_runtime_handoff_returns_machine_readable_snapshot(monkeypatch):
    """`/api/runtime/handoff` должен отдавать единый JSON-снимок для anti-413 handoff."""
    monkeypatch.setenv("LM_STUDIO_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("OPENCLAW_TOKEN", "test-token")
    client = _make_client()

    resp = client.get("/api/runtime/handoff")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "generated_at_utc" in data
    assert "git" in data
    assert "runtime" in data
    assert "services" in data
    assert "artifacts" in data
    assert data["health_lite"]["last_runtime_route"]["model"] == "nvidia/nemotron-3-nano"


def test_runtime_recover_requires_web_api_key(monkeypatch):
    """Write endpoint `/api/runtime/recover` должен быть закрыт WEB_API_KEY при включенной защите."""
    monkeypatch.setenv("WEB_API_KEY", "secret")
    client = _make_client()

    resp = client.post("/api/runtime/recover", json={})
    assert resp.status_code == 403


def test_runtime_recover_minimal_flow(monkeypatch):
    """
    Минимальный recovery flow без запуска скриптов:
    endpoint должен отработать и вернуть post-check runtime.
    """
    monkeypatch.setenv("WEB_API_KEY", "secret")
    monkeypatch.setenv("LM_STUDIO_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("OPENCLAW_TOKEN", "test-token")
    client = _make_client()

    resp = client.post(
        "/api/runtime/recover",
        json={
            "run_openclaw_runtime_repair": False,
            "run_sync_openclaw_models": False,
            "force_tier": "free",
            "probe_cloud_runtime": True,
        },
        headers={"X-Krab-Web-Key": "secret"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert isinstance(data["steps"], list)
    assert data["runtime_after"]["last_runtime_route"]["model"] == "nvidia/nemotron-3-nano"
    assert data["cloud_runtime"]["available"] is True


def test_model_local_status_uses_runtime_truth_when_router_state_stale(monkeypatch):
    """`/api/model/local/status` должен брать факт загрузки из runtime, а не из stale router-поля."""

    class _TruthModelManager:
        def get_current_model(self):
            return ""

        async def get_loaded_models(self):
            return ["nvidia/nemotron-3-nano"]

    class _TruthRouter(_DummyRouter):
        def __init__(self) -> None:
            self._mm = _TruthModelManager()
            self.is_local_available = False
            self.active_local_model = ""
            self.local_engine = "lm_studio"
            self.lm_studio_url = "http://127.0.0.1:1234"

    async def _fake_lm_snapshot(self, *args, **kwargs):
        return {
            "state": "loaded",
            "base_url": "http://127.0.0.1:1234",
            "loaded_count": 1,
            "loaded_models": ["nvidia/nemotron-3-nano"],
            "error": "",
        }

    monkeypatch.setattr(WebApp, "_lmstudio_model_snapshot", _fake_lm_snapshot)
    client = _make_client_with_router(_TruthRouter())

    resp = client.get("/api/model/local/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["status"] == "loaded"
    assert data["model_name"] == "nvidia/nemotron-3-nano"
    assert data["details"]["available"] is True
    assert data["details"]["is_loaded"] is True


def test_model_local_status_does_not_double_probe_model_manager_when_snapshot_is_loaded(monkeypatch):
    """Runtime truth не должен делать второй `/models` probe через model_manager, если snapshot уже loaded."""

    class _CountingModelManager:
        def __init__(self) -> None:
            self.loaded_calls = 0

        def get_current_model(self):
            return ""

        async def get_loaded_models(self, *args, **kwargs):
            self.loaded_calls += 1
            return ["nvidia/nemotron-3-nano"]

    class _TruthRouter(_DummyRouter):
        def __init__(self, mm) -> None:
            self._mm = mm
            self.is_local_available = False
            self.active_local_model = ""
            self.local_engine = "lm_studio"
            self.lm_studio_url = "http://127.0.0.1:1234"

    async def _fake_lm_snapshot(self, *args, **kwargs):
        return {
            "state": "loaded",
            "base_url": "http://127.0.0.1:1234",
            "loaded_count": 1,
            "loaded_models": ["nvidia/nemotron-3-nano"],
            "error": "",
        }

    mm = _CountingModelManager()
    monkeypatch.setattr(WebApp, "_lmstudio_model_snapshot", _fake_lm_snapshot)
    client = _make_client_with_router(_TruthRouter(mm))

    resp = client.get("/api/model/local/status")

    assert resp.status_code == 200
    assert mm.loaded_calls == 0


def test_model_catalog_uses_runtime_truth_for_loaded_flag(monkeypatch):
    """`/api/model/catalog` должен помечать loaded-модель по runtime truth, даже если router stale."""

    class _TruthModelManager:
        def get_current_model(self):
            return ""

        async def get_loaded_models(self):
            return ["nvidia/nemotron-3-nano"]

    class _TruthRouter(_DummyRouter):
        def __init__(self) -> None:
            self._mm = _TruthModelManager()
            self.is_local_available = False
            self.active_local_model = ""
            self.local_engine = "lm_studio"
            self.lm_studio_url = "http://127.0.0.1:1234"
            self.models = {"chat": "google/gemini-2.5-flash"}
            self.force_mode = None

        async def list_local_models_verbose(self):
            return [
                {
                    "id": "nvidia/nemotron-3-nano",
                    "loaded": False,
                    "type": "local_mlx",
                    "size_human": "16.6 GB",
                }
            ]

    async def _fake_lm_snapshot(self, *args, **kwargs):
        return {
            "state": "loaded",
            "base_url": "http://127.0.0.1:1234",
            "loaded_count": 1,
            "loaded_models": ["nvidia/nemotron-3-nano"],
            "error": "",
        }

    monkeypatch.setattr(WebApp, "_lmstudio_model_snapshot", _fake_lm_snapshot)
    client = _make_client_with_router(_TruthRouter())

    resp = client.get("/api/model/catalog")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    catalog = data["catalog"]
    assert catalog["local_active_model"] == "nvidia/nemotron-3-nano"
    assert catalog["local_available"] is True
    assert catalog["local_models"][0]["id"] == "nvidia/nemotron-3-nano"
    assert catalog["local_models"][0]["loaded"] is True


def test_model_catalog_cloud_presets_come_from_openclaw_runtime_registry(monkeypatch):
    """Cloud catalog должен строиться из runtime models.json, а не из старого hardcoded списка."""

    class _TruthRouter(_DummyRouter):
        def __init__(self) -> None:
            self.is_local_available = False
            self.active_local_model = ""
            self.local_engine = "lm_studio"
            self.lm_studio_url = "http://127.0.0.1:1234"
            self.models = {"chat": "openai-codex/gpt-4.5-preview"}
            self.force_mode = None

        async def list_local_models_verbose(self):
            return []

    async def _fake_lm_snapshot(self, *args, **kwargs):
        return {
            "state": "idle",
            "base_url": "http://127.0.0.1:1234",
            "loaded_count": 0,
            "loaded_models": [],
            "error": "",
        }

    runtime_payload = {
        "providers": {
            "openai-codex": {
                "models": [
                    {"id": "gpt-4.5-preview", "name": "ChatGPT 4.5 Preview", "reasoning": False, "contextWindow": 128000, "maxTokens": 16384}
                ]
            },
            "google-antigravity": {
                "models": [
                    {"id": "gemini-3.1-pro-preview", "name": "Gemini 3.1 Pro Preview", "reasoning": False, "contextWindow": 128000, "maxTokens": 16384}
                ]
            },
        }
    }

    monkeypatch.setattr(WebApp, "_lmstudio_model_snapshot", _fake_lm_snapshot)
    monkeypatch.setattr(
        WebApp,
        "_load_openclaw_runtime_models",
        classmethod(lambda cls: runtime_payload),
    )
    client = _make_client_with_router(_TruthRouter())

    resp = client.get("/api/model/catalog")

    assert resp.status_code == 200
    data = resp.json()["catalog"]
    cloud_ids = {item["id"] for item in data["cloud_presets"]}
    assert "openai-codex/gpt-4.5-preview" in cloud_ids
    assert "google-antigravity/gemini-3.1-pro-preview" in cloud_ids
    assert "openai/gpt-5-codex" not in cloud_ids
    assert data["runtime_registry_source"] == "openclaw_models_json"


def test_openclaw_model_routing_status_reports_broken_primary_and_disabled_fallback(monkeypatch):
    """Routing status должен честно отражать сломанный primary и disabled OAuth fallback."""

    runtime_config = {
        "agents": {
            "defaults": {
                "model": {
                    "primary": "openai-codex/gpt-4.5-preview",
                    "fallbacks": [
                        "google-antigravity/gemini-3.1-pro-preview",
                        "google/gemini-2.5-flash",
                    ],
                },
                "workspace": "/Users/pablito/.openclaw/workspace-main-messaging",
            }
        }
    }
    runtime_models = {
        "providers": {
            "openai-codex": {
                "models": [{"id": "gpt-4.5-preview"}]
            },
            "google-antigravity": {
                "models": [{"id": "gemini-3.1-pro-preview"}]
            },
        }
    }
    auth_profiles = {
        "profiles": {
            "openai-codex:default": {"provider": "openai-codex"},
            "google-antigravity:vscode-free": {"provider": "google-antigravity"},
        },
        "usageStats": {
            "openai-codex:default": {"failureCounts": {"model_not_found": 2}},
            "google-antigravity:vscode-free": {"disabledReason": "auth_permanent"},
        },
    }

    monkeypatch.setattr(WebApp, "_load_openclaw_runtime_config", classmethod(lambda cls: runtime_config))
    monkeypatch.setattr(WebApp, "_load_openclaw_runtime_models", classmethod(lambda cls: runtime_models))
    monkeypatch.setattr(WebApp, "_load_openclaw_auth_profiles", classmethod(lambda cls: auth_profiles))
    monkeypatch.setenv("OPENCLAW_TARGET_PRIMARY_MODEL", "openai-codex/gpt-5.4")

    client = _make_client_with_router(_DummyRouter())
    resp = client.get("/api/openclaw/model-routing/status")

    assert resp.status_code == 200
    data = resp.json()["routing"]
    assert data["current_primary"] == "openai-codex/gpt-4.5-preview"
    assert data["current_primary_broken"] is True
    assert data["target_primary_candidate"] == "openai-codex/gpt-5.4"
    assert data["target_primary_in_runtime"] is False
    assert data["temporary_primary_recommendation"] == "google/gemini-2.5-flash"
    assert any("model_not_found" in item for item in data["warnings"])
    assert any("disabled" in item.lower() for item in data["warnings"])


def test_model_local_load_default_falls_back_to_config_preferred_model(monkeypatch):
    """`/api/model/local/load-default` не должен ломаться, если compat-router не пробросил поле."""

    class _LoadDefaultRouter(_DummyRouter):
        def __init__(self) -> None:
            self.loaded_model = None
            self.load_reason = None

        async def _smart_load(self, model_id: str, reason: str = "") -> bool:
            self.loaded_model = model_id
            self.load_reason = reason
            return True

    previous = config.LOCAL_PREFERRED_MODEL
    monkeypatch.setattr(config, "LOCAL_PREFERRED_MODEL", "nvidia/nemotron-3-nano")
    router = _LoadDefaultRouter()
    client = _make_client_with_router(router)

    try:
        resp = client.post("/api/model/local/load-default")
    finally:
        monkeypatch.setattr(config, "LOCAL_PREFERRED_MODEL", previous)

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["model"] == "nvidia/nemotron-3-nano"
    assert router.loaded_model == "nvidia/nemotron-3-nano"
    assert router.load_reason == "web_forced"


def test_stats_router_payload_uses_runtime_truth_and_openclaw_fallbacks(monkeypatch):
    """`/api/stats` должен отдавать совместимый router payload из runtime truth и OpenClaw fallback'ов."""

    class _TruthModelManager:
        def get_current_model(self):
            return ""

        async def get_loaded_models(self):
            return ["nvidia/nemotron-3-nano"]

    class _StatsRouter(_DummyRouter):
        def __init__(self) -> None:
            self._mm = _TruthModelManager()
            self.rag = None
            self.is_local_available = False
            self.active_local_model = ""
            self.local_engine = "lm_studio"
            self.lm_studio_url = "http://127.0.0.1:1234"
            self.models = {"chat": "google/gemini-2.5-flash"}

        def get_model_info(self):
            return {
                "current_model": "google/gemini-2.5-flash",
                "models": {"chat": "google/gemini-2.5-flash"},
            }

        def get_last_route(self):
            return {}

    class _StatsOpenClaw(_FakeOpenClaw):
        def get_token_info(self):
            return {
                "active_tier": "free",
                "tiers": {
                    "free": {
                        "is_configured": True,
                        "masked_key": "AIza...123",
                        "is_aistudio_key": True,
                    }
                },
                "current_google_key_masked": "AIza...123",
                "last_error_code": None,
            }

        def get_tier_state_export(self):
            return {
                "active_tier": "free",
                "last_error_code": None,
                "last_error_message": "",
                "last_provider_status": "ok",
                "last_recovery_action": "none",
                "last_probe_at": 0,
                "tiers_configured": {"free": True, "paid": False},
            }

    async def _fake_lm_snapshot(self, *args, **kwargs):
        return {
            "state": "loaded",
            "base_url": "http://127.0.0.1:1234",
            "loaded_count": 1,
            "loaded_models": ["nvidia/nemotron-3-nano"],
            "error": "",
        }

    monkeypatch.setenv("OPENCLAW_TOKEN", "token-from-runtime")
    monkeypatch.setattr(
        WebApp,
        "_openclaw_gateway_token_from_config",
        staticmethod(lambda: ""),
    )
    monkeypatch.setattr(WebApp, "_lmstudio_model_snapshot", _fake_lm_snapshot)
    client = _make_client_with_router(_StatsRouter(), openclaw_client=_StatsOpenClaw())

    resp = client.get("/api/stats")
    assert resp.status_code == 200
    router = resp.json()["router"]
    assert router["local_model"] == "nvidia/nemotron-3-nano"
    assert router["active_local_model"] == "nvidia/nemotron-3-nano"
    assert router["is_local_available"] is True
    assert router["last_route"]["model"] == "nvidia/nemotron-3-nano"
    assert router["cloud_keys"]["openclaw"]["is_configured"] is True
    assert router["cloud_keys"]["gemini"]["is_configured"] is True
    assert router["cloud_keys"]["gemini"]["has_error"] is False
    assert router["cloud_tier"]["active_display"] == "FREE"
    assert router["cloud_tier"]["configured_labels"] == ["Gemini", "OpenClaw"]
    assert router["cloud_tier"]["last_error_summary"] == ""


def test_stats_router_payload_uses_model_manager_cloud_error_fallback(monkeypatch):
    """`/api/stats` должен брать свежий cloud auth-error из ModelManager, если OpenClaw tier-state ещё stale."""

    class _TruthModelManager:
        def get_current_model(self):
            return "nvidia/nemotron-3-nano"

        async def get_loaded_models(self):
            return ["nvidia/nemotron-3-nano"]

        def get_cloud_runtime_state_export(self):
            return {
                "active_tier": "free",
                "last_provider_status": "auth",
                "last_error_code": "auth_invalid",
                "last_error_message": "401 unauthorized",
                "last_probe_at": 9_999_999_999,
            }

    class _StatsRouter(_DummyRouter):
        def __init__(self) -> None:
            self._mm = _TruthModelManager()
            self.rag = None
            self.is_local_available = True
            self.active_local_model = "nvidia/nemotron-3-nano"
            self.local_engine = "lm_studio"
            self.lm_studio_url = "http://127.0.0.1:1234"
            self.models = {"chat": "google/gemini-2.5-flash"}

        def get_model_info(self):
            return {
                "current_model": "google/gemini-2.5-flash",
                "models": {"chat": "google/gemini-2.5-flash"},
            }

        def get_last_route(self):
            return {}

    class _StatsOpenClaw(_FakeOpenClaw):
        def get_token_info(self):
            return {
                "active_tier": "free",
                "tiers": {
                    "free": {
                        "is_configured": True,
                        "masked_key": "AIza...123",
                        "is_aistudio_key": True,
                    }
                },
                "current_google_key_masked": "AIza...123",
                "last_error_code": "",
            }

        def get_tier_state_export(self):
            return {
                "active_tier": "free",
                "last_error_code": "",
                "last_error_message": "",
                "last_provider_status": "unknown",
                "last_recovery_action": "none",
                "last_probe_at": 0,
                "tiers_configured": {"free": True, "paid": False},
            }

    async def _fake_lm_snapshot(self, *args, **kwargs):
        return {
            "state": "loaded",
            "base_url": "http://127.0.0.1:1234",
            "loaded_count": 1,
            "loaded_models": ["nvidia/nemotron-3-nano"],
            "error": "",
        }

    monkeypatch.setenv("OPENCLAW_TOKEN", "token-from-runtime")
    monkeypatch.setattr(
        WebApp,
        "_openclaw_gateway_token_from_config",
        staticmethod(lambda: ""),
    )
    monkeypatch.setattr(WebApp, "_lmstudio_model_snapshot", _fake_lm_snapshot)
    client = _make_client_with_router(_StatsRouter(), openclaw_client=_StatsOpenClaw())

    resp = client.get("/api/stats")
    assert resp.status_code == 200
    router = resp.json()["router"]
    assert router["cloud_keys"]["gemini"]["has_error"] is True
    assert router["cloud_keys"]["last_error"]["code"] == "auth_invalid"
    assert router["cloud_keys"]["last_error"]["summary"] == "401 unauthorized"
    assert router["cloud_tier"]["has_error"] is True
    assert router["cloud_tier"]["last_error_code"] == "auth_invalid"


def test_routing_effective_uses_runtime_truth_for_local_availability(monkeypatch):
    """`/api/openclaw/routing/effective` не должен считать local недоступным по stale router-полю."""

    class _TruthModelManager:
        def get_current_model(self):
            return ""

        async def get_loaded_models(self):
            return ["nvidia/nemotron-3-nano"]

    class _TruthRouter(_DummyRouter):
        def __init__(self) -> None:
            self._mm = _TruthModelManager()
            self.is_local_available = False
            self.active_local_model = ""
            self.local_engine = "lm_studio"
            self.lm_studio_url = "http://127.0.0.1:1234"
            self.models = {"chat": "google/gemini-2.5-flash"}
            self.force_mode = None
            self.routing_policy = "free_first_hybrid"
            self.cloud_soft_cap_reached = False

    async def _fake_lm_snapshot(self, *args, **kwargs):
        return {
            "state": "loaded",
            "base_url": "http://127.0.0.1:1234",
            "loaded_count": 1,
            "loaded_models": ["nvidia/nemotron-3-nano"],
            "error": "",
        }

    monkeypatch.setattr(WebApp, "_lmstudio_model_snapshot", _fake_lm_snapshot)
    client = _make_client_with_router(_TruthRouter())

    resp = client.get("/api/openclaw/routing/effective")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["requested_mode"] == "auto"
    assert data["effective_mode"] == "auto"
    assert data["active_slot_or_model"] == "nvidia/nemotron-3-nano"
    assert data["cloud_fallback"] is True
    assert data["cloud_fallback_state"] == "standby"
    assert data["cloud_fallback_active"] is False
    joined_notes = " ".join(data["decision_notes"])
    assert "Локальный движок 'lm_studio' доступен." in joined_notes
    assert "nvidia/nemotron-3-nano" in joined_notes
    assert "недоступен" not in joined_notes.lower()
    assert "резерв" in joined_notes.lower()


def test_routing_effective_marks_cloud_fallback_active_in_force_cloud(monkeypatch):
    """`/api/openclaw/routing/effective` должен явно помечать активный cloud fallback при force_cloud."""

    class _TruthModelManager:
        def get_current_model(self):
            return ""

        async def get_loaded_models(self):
            return ["nvidia/nemotron-3-nano"]

    class _TruthRouter(_DummyRouter):
        def __init__(self) -> None:
            self._mm = _TruthModelManager()
            self.is_local_available = True
            self.active_local_model = "nvidia/nemotron-3-nano"
            self.local_engine = "lm_studio"
            self.lm_studio_url = "http://127.0.0.1:1234"
            self.models = {"chat": "google/gemini-2.5-flash"}
            self.force_mode = "force_cloud"
            self.routing_policy = "free_first_hybrid"
            self.cloud_soft_cap_reached = False

        def get_last_route(self):
            return {}

    async def _fake_lm_snapshot(self, *args, **kwargs):
        return {
            "state": "loaded",
            "base_url": "http://127.0.0.1:1234",
            "loaded_count": 1,
            "loaded_models": ["nvidia/nemotron-3-nano"],
            "error": "",
        }

    monkeypatch.setattr(WebApp, "_lmstudio_model_snapshot", _fake_lm_snapshot)
    client = _make_client_with_router(_TruthRouter())

    resp = client.get("/api/openclaw/routing/effective")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["requested_mode"] == "force_cloud"
    assert data["effective_mode"] == "cloud"
    assert data["cloud_fallback"] is True
    assert data["cloud_fallback_state"] == "active"
    assert data["cloud_fallback_active"] is True


def test_parse_openclaw_channels_probe_returns_normalized_channels():
    """Парсер channels probe должен отдавать нормализованный список каналов для UI."""
    sample = """
Checking channel status (probe)…
Gateway reachable.
- Telegram default: enabled, configured, running, works
- BlueBubbles default: enabled, not configured, stopped, disconnected, error:not configured

Warnings:
- bluebubbles default: Not configured
""".strip()

    parsed = WebApp._parse_openclaw_channels_probe(sample)
    assert parsed["gateway_reachable"] is True
    assert len(parsed["channels"]) == 2
    assert parsed["channels"][0]["name"] == "Telegram default"
    assert parsed["channels"][0]["status"] == "OK"
    assert parsed["channels"][1]["name"] == "BlueBubbles default"
    assert parsed["channels"][1]["status"] == "FAIL"
    assert parsed["warnings"] == ["bluebubbles default: Not configured"]


def test_browser_smoke_marks_auth_required_as_reachable_but_not_attached(monkeypatch):
    """`/api/openclaw/browser-smoke` должен отличать живой relay с auth-required от реально attached tab."""

    class _Proc:
        def __init__(self, stdout_text: str, *, returncode: int = 0):
            self.returncode = returncode
            self._stdout = stdout_text.encode("utf-8")

        async def communicate(self):
            return self._stdout, b""

        def terminate(self):
            return None

    class _Resp:
        def __init__(self, status_code: int):
            self.status_code = status_code

    class _AsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url: str):
            assert url == "http://127.0.0.1:18791/"
            return _Resp(401)

    async def _fake_create_subprocess_exec(*cmd, **kwargs):
        assert cmd[:3] == ("openclaw", "gateway", "probe")
        stdout_text = """
Gateway Status
Reachable: yes
Probe budget: 3000ms

Targets
Local loopback ws://127.0.0.1:18789
  Connect: ok (15ms) · RPC: ok
""".strip()
        return _Proc(stdout_text, returncode=0)

    monkeypatch.setattr("src.modules.web_app.asyncio.create_subprocess_exec", _fake_create_subprocess_exec)
    monkeypatch.setattr("src.modules.web_app.httpx.AsyncClient", _AsyncClient)
    client = _make_client()

    resp = client.get("/api/openclaw/browser-smoke")
    assert resp.status_code == 200
    smoke = resp.json()["report"]["browser_smoke"]
    assert smoke["relay_reachable"] is True
    assert smoke["browser_http_state"] == "auth_required"
    assert smoke["tab_attached"] is False
    assert smoke["browser_auth_required"] is True


def test_control_compat_status_returns_legacy_aliases(monkeypatch):
    """`/api/openclaw/control-compat/status` должен отдавать legacy-алиасы для текущего UI."""

    class _Proc:
        def __init__(self, stdout_text: str, *, returncode: int = 0):
            self.returncode = returncode
            self._stdout = stdout_text.encode("utf-8")

        async def communicate(self):
            return self._stdout, b""

        def terminate(self):
            return None

    async def _fake_create_subprocess_exec(*cmd, **kwargs):
        if cmd[:4] == ("openclaw", "channels", "status", "--probe"):
            return _Proc("channels ok", returncode=0)
        if cmd[:3] == ("openclaw", "logs", "--tail"):
            return _Proc("Unsupported schema node in Control UI", returncode=0)
        raise AssertionError(f"Неожиданный вызов subprocess: {cmd}")

    monkeypatch.setattr("src.modules.web_app.asyncio.create_subprocess_exec", _fake_create_subprocess_exec)
    client = _make_client()

    resp = client.get("/api/openclaw/control-compat/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["runtime_channels_ok"] is True
    assert data["runtime_status"] == "OK"
    assert data["has_schema_warning"] is True
    assert data["impact_level"] == "ui_only"


def test_health_lite_marks_auth_unauthorized_when_provider_reports_auth(monkeypatch):
    """`health/lite` должен показывать unauthorized при provider_status=auth."""

    class _OpenClawAuthState(_FakeOpenClaw):
        def get_tier_state_export(self):
            return {
                "active_tier": "free",
                "last_error_code": None,
                "last_provider_status": "auth",
                "last_recovery_action": "switch_provider_or_key",
            }

    monkeypatch.setenv("LM_STUDIO_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("OPENCLAW_TOKEN", "test-token")
    client = _make_client(openclaw_client=_OpenClawAuthState())

    resp = client.get("/api/health/lite")
    assert resp.status_code == 200
    assert resp.json()["openclaw_auth_state"] == "unauthorized"


def test_health_lite_marks_auth_unauthorized_from_runtime_route_401_detail(monkeypatch):
    """`health/lite` должен помечать unauthorized по route_detail c 401, даже без error_code."""

    class _OpenClawRoute401(_FakeOpenClaw):
        def get_last_runtime_route(self):
            return {
                "channel": "error",
                "provider": "google",
                "model": "google/gemini-2.5-flash",
                "status": "error",
                "error_code": None,
                "route_detail": "Provider returned HTTP 401 Unauthorized for current key",
            }

        def get_tier_state_export(self):
            return {
                "active_tier": "free",
                "last_error_code": None,
                "last_provider_status": "unknown",
                "last_recovery_action": "none",
            }

    monkeypatch.setenv("LM_STUDIO_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("OPENCLAW_TOKEN", "test-token")
    client = _make_client(openclaw_client=_OpenClawRoute401())

    resp = client.get("/api/health/lite")
    assert resp.status_code == 200
    assert resp.json()["openclaw_auth_state"] == "unauthorized"


def test_openclaw_cli_env_propagates_runtime_token(monkeypatch):
    """`openclaw` CLI env должен получать gateway token без подмены OPENCLAW_TOKEN."""
    monkeypatch.setenv("OPENCLAW_TOKEN", "token-from-runtime")
    monkeypatch.setenv("OPENCLAW_GATEWAY_TOKEN", "")
    monkeypatch.setattr(
        WebApp,
        "_openclaw_gateway_token_from_config",
        staticmethod(lambda: "gateway-token-from-config"),
    )

    env = WebApp._openclaw_cli_env()
    assert env["OPENCLAW_GATEWAY_TOKEN"] == "gateway-token-from-config"
    assert env["OPENCLAW_TOKEN"] == "token-from-runtime"


def test_parse_openclaw_gateway_probe_extracts_reachability():
    """Парсер gateway probe должен извлекать reachable/detail/local target."""
    sample = """
Gateway Status
Reachable: yes
Probe budget: 3000ms

Targets
Local loopback ws://127.0.0.1:18789
  Connect: ok
""".strip()
    parsed = WebApp._parse_openclaw_gateway_probe(sample)
    assert parsed["gateway_reachable"] is True
    assert parsed["local_target"] == "ws://127.0.0.1:18789"
    assert "Connect: ok" in parsed["detail"]


def test_classify_browser_http_probe_auth_required_state():
    """401/403 в browser probe должны маркироваться как auth_required, но reachable."""
    parsed = WebApp._classify_browser_http_probe(401, "")
    assert parsed["state"] == "auth_required"
    assert parsed["reachable"] is True
    assert parsed["auth_required"] is True


def test_photo_smoke_endpoint_reports_ready_with_local_vision():
    """`/api/openclaw/photo-smoke` должен подтверждать готовность vision-маршрута."""
    deps = {
        "router": _PhotoRouter(),
        "openclaw_client": _FakeOpenClaw(),
        "black_box": None,
        "health_service": None,
        "provisioning_service": None,
        "ai_runtime": None,
        "reaction_engine": None,
        "voice_gateway_client": _FakeHealthClient(ok=True),
        "krab_ear_client": _FakeHealthClient(ok=True),
        "perceptor": None,
        "watchdog": None,
        "queue": None,
    }
    app = WebApp(deps, port=18080, host="127.0.0.1")
    client = TestClient(app.app)

    resp = client.get("/api/openclaw/photo-smoke")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["available"] is True
    smoke = payload["report"]["photo_smoke"]
    assert smoke["ok"] is True
    assert smoke["selected_local"] is True
    assert smoke["local_vision_count"] == 1


def test_openclaw_cli_env_fallback_to_env_gateway_token(monkeypatch):
    """Если в конфиге нет токена, используем OPENCLAW_GATEWAY_TOKEN из env."""
    monkeypatch.setenv("OPENCLAW_GATEWAY_TOKEN", "gateway-token-from-env")
    monkeypatch.setattr(
        WebApp,
        "_openclaw_gateway_token_from_config",
        staticmethod(lambda: ""),
    )
    env = WebApp._openclaw_cli_env()
    assert env["OPENCLAW_GATEWAY_TOKEN"] == "gateway-token-from-env"


def test_model_autoswitch_status_passes_current_profile(monkeypatch):
    """`/api/openclaw/model-autoswitch/status` должен запускать скрипт с `--profile current`."""
    monkeypatch.setenv("LM_STUDIO_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("OPENCLAW_TOKEN", "test-token")

    calls = []

    class _Proc:
        def __init__(self):
            self.returncode = 0
            self.stdout = json.dumps({"ok": True, "status": "OK", "reason": "unit_test"})
            self.stderr = ""

    def _fake_run(cmd, **kwargs):
        calls.append(cmd)
        return _Proc()

    monkeypatch.setattr("src.modules.web_app.subprocess.run", _fake_run)
    client = _make_client()

    resp = client.get("/api/openclaw/model-autoswitch/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["autoswitch"]["status"] == "OK"
    assert calls, "subprocess.run не был вызван"
    cmd = calls[-1]
    assert "--dry-run" in cmd
    assert "--profile" in cmd
    assert "current" in cmd


def test_model_autoswitch_apply_honors_toggle_payload(monkeypatch):
    """`/api/openclaw/model-autoswitch/apply` должен передавать `--profile toggle` при body.toggle=true."""
    monkeypatch.setenv("WEB_API_KEY", "secret")

    calls = []

    class _Proc:
        def __init__(self):
            self.returncode = 0
            self.stdout = json.dumps({"ok": True, "status": "OK", "reason": "unit_test"})
            self.stderr = ""

    def _fake_run(cmd, **kwargs):
        calls.append(cmd)
        return _Proc()

    monkeypatch.setattr("src.modules.web_app.subprocess.run", _fake_run)
    client = _make_client()

    resp = client.post(
        "/api/openclaw/model-autoswitch/apply",
        json={"toggle": True},
        headers={"X-Krab-Web-Key": "secret"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert calls, "subprocess.run не был вызван"
    cmd = calls[-1]
    assert "--dry-run" not in cmd
    assert "--profile" in cmd
    assert "toggle" in cmd


def test_model_autoswitch_apply_passes_explicit_profile(monkeypatch):
    """`/api/openclaw/model-autoswitch/apply` должен прокидывать явный профиль из body."""
    monkeypatch.setenv("WEB_API_KEY", "secret")

    calls = []

    class _Proc:
        def __init__(self):
            self.returncode = 0
            self.stdout = json.dumps({"ok": True, "status": "OK", "reason": "unit_test"})
            self.stderr = ""

    def _fake_run(cmd, **kwargs):
        calls.append(cmd)
        return _Proc()

    monkeypatch.setattr("src.modules.web_app.subprocess.run", _fake_run)
    client = _make_client()

    resp = client.post(
        "/api/openclaw/model-autoswitch/apply",
        json={"profile": "production-safe"},
        headers={"X-Krab-Web-Key": "secret"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert calls, "subprocess.run не был вызван"
    cmd = calls[-1]
    assert "--dry-run" not in cmd
    assert "--profile" in cmd
    assert "production-safe" in cmd


def test_model_compat_probe_passes_model_and_reasoning(monkeypatch):
    """`/api/openclaw/model-compat/probe` должен прокидывать model/reasoning/skip_reasoning в probe-скрипт."""
    calls = []

    class _Proc:
        def __init__(self):
            self.returncode = 0
            self.stdout = json.dumps({"ok": False, "status": "BLOCKED", "reason": "unit_test"})
            self.stderr = ""

    def _fake_run(cmd, **kwargs):
        calls.append(cmd)
        return _Proc()

    monkeypatch.setattr("src.modules.web_app.subprocess.run", _fake_run)
    client = _make_client()

    resp = client.get(
        "/api/openclaw/model-compat/probe",
        params={
            "model": "openai-codex/gpt-5.4",
            "reasoning": "high",
            "skip_reasoning": "true",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["probe"]["status"] == "BLOCKED"
    assert calls, "subprocess.run не был вызван"
    cmd = calls[-1]
    assert "--model" in cmd
    assert "openai-codex/gpt-5.4" in cmd
    assert "--reasoning" in cmd
    assert "high" in cmd
    assert "--skip-reasoning" in cmd


def test_userbot_acl_status_returns_runtime_acl_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """`/api/userbot/acl/status` должен отдавать owner, runtime state и partial-команды."""
    acl_path = tmp_path / "krab_userbot_acl.json"
    monkeypatch.setattr(config, "USERBOT_ACL_FILE", acl_path, raising=False)
    monkeypatch.setattr(config, "OWNER_USERNAME", "@pablito", raising=False)
    monkeypatch.setattr(
        "src.modules.web_app.load_acl_runtime_state",
        lambda: {"owner": ["pablito"], "full": ["trusted"], "partial": ["reader"]},
    )

    client = _make_client()
    resp = client.get("/api/userbot/acl/status")

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["acl"]["path"] == str(acl_path)
    assert data["acl"]["owner_username"] == "@pablito"
    assert data["acl"]["state"]["full"] == ["trusted"]
    assert data["acl"]["state"]["partial"] == ["reader"]
    assert data["acl"]["partial_commands"] == ["help", "search", "status"]


def test_userbot_acl_update_requires_web_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """`/api/userbot/acl/update` должен требовать WEB_API_KEY, если он включён."""
    monkeypatch.setenv("WEB_API_KEY", "secret")
    client = _make_client()

    resp = client.post(
        "/api/userbot/acl/update",
        json={"action": "grant", "level": "full", "subject": "@trusted"},
    )

    assert resp.status_code == 403
    assert resp.json()["detail"] == "forbidden: invalid WEB_API_KEY"


def test_userbot_acl_update_grants_subject(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """`/api/userbot/acl/update` должен применять grant/revoke через общий ACL helper."""
    acl_path = tmp_path / "krab_userbot_acl.json"
    monkeypatch.setattr(config, "USERBOT_ACL_FILE", acl_path, raising=False)
    monkeypatch.setenv("WEB_API_KEY", "secret")

    def _fake_update(level: str, subject: str, *, add: bool):
        assert level == "partial"
        assert subject == "@reader"
        assert add is True
        return {
            "changed": True,
            "level": "partial",
            "subject": "reader",
            "path": acl_path,
            "state": {"owner": [], "full": [], "partial": ["reader"]},
        }

    monkeypatch.setattr("src.modules.web_app.update_acl_subject", _fake_update)
    client = _make_client()

    resp = client.post(
        "/api/userbot/acl/update",
        json={"action": "grant", "level": "partial", "subject": "@reader"},
        headers={"X-Krab-Web-Key": "secret"},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["acl"]["action"] == "grant"
    assert data["acl"]["level"] == "partial"
    assert data["acl"]["subject"] == "reader"
    assert data["acl"]["changed"] is True
    assert data["acl"]["path"] == str(acl_path)
    assert data["acl"]["state"]["partial"] == ["reader"]


def test_userbot_acl_update_rejects_invalid_action(monkeypatch: pytest.MonkeyPatch) -> None:
    """`/api/userbot/acl/update` должен отвергать неподдерживаемые действия."""
    monkeypatch.setenv("WEB_API_KEY", "secret")
    client = _make_client()

    resp = client.post(
        "/api/userbot/acl/update",
        json={"action": "promote", "level": "full", "subject": "@trusted"},
        headers={"X-Krab-Web-Key": "secret"},
    )

    assert resp.status_code == 400
    assert resp.json()["detail"] == "acl_update_invalid_action"
