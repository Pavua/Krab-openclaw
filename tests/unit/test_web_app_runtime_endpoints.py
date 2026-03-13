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
import time
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from src.config import config
from src.core.inbox_service import InboxService
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


class _FakeUserbot:
    """Минимальная заглушка userbot runtime-state для health/runtime тестов."""

    def __init__(
        self,
        *,
        startup_state: str = "running",
        client_connected: bool = True,
        startup_error_code: str = "",
    ) -> None:
        self._payload = {
            "startup_state": startup_state,
            "startup_error_code": startup_error_code,
            "client_connected": client_connected,
            "authorized_user": "pablito",
            "authorized_user_id": 312322764,
        }

    def get_runtime_state(self) -> dict:
        return dict(self._payload)


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


def _make_app(*, openclaw_client=None, kraab_userbot=None) -> WebApp:
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
        "kraab_userbot": kraab_userbot,
    }
    return WebApp(deps, port=18080, host="127.0.0.1")


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


def test_runtime_lite_treats_sqlite_journal_as_ready_when_userbot_is_live(monkeypatch):
    """Живой userbot не должен светиться как `open_or_unclean` только из-за journal sidecar."""

    async def _fake_lmstudio_snapshot(self):
        return {"state": "idle", "loaded_models": []}

    def _fake_telegram_snapshot(self):
        return {
            "state": "open_or_unclean",
            "session_exists": True,
            "journal_exists": True,
            "wal_exists": False,
            "shm_exists": False,
            "sqlite_quick_check_ok": True,
            "sqlite_error": "",
        }

    monkeypatch.setattr(WebApp, "_lmstudio_model_snapshot", _fake_lmstudio_snapshot)
    monkeypatch.setattr(WebApp, "_telegram_session_snapshot", _fake_telegram_snapshot)

    app = _make_app(kraab_userbot=_FakeUserbot(startup_state="running", client_connected=True))
    snapshot = asyncio.run(app._build_runtime_lite_snapshot_uncached())

    assert snapshot["telegram_session_state"] == "ready"
    assert snapshot["telegram_session"]["state"] == "ready"
    assert snapshot["telegram_session"]["state_file_raw"] == "open_or_unclean"
    assert snapshot["telegram_session"]["state_reason"] == "sqlite_sidecars_expected_while_userbot_running"


def test_runtime_lite_keeps_open_or_unclean_when_userbot_is_not_live(monkeypatch):
    """Если userbot не живой, sidecar-файлы по-прежнему считаем подозрительным состоянием."""

    async def _fake_lmstudio_snapshot(self):
        return {"state": "idle", "loaded_models": []}

    def _fake_telegram_snapshot(self):
        return {
            "state": "open_or_unclean",
            "session_exists": True,
            "journal_exists": True,
            "wal_exists": False,
            "shm_exists": False,
            "sqlite_quick_check_ok": True,
            "sqlite_error": "",
        }

    monkeypatch.setattr(WebApp, "_lmstudio_model_snapshot", _fake_lmstudio_snapshot)
    monkeypatch.setattr(WebApp, "_telegram_session_snapshot", _fake_telegram_snapshot)

    app = _make_app(kraab_userbot=_FakeUserbot(startup_state="stopped", client_connected=False))
    snapshot = asyncio.run(app._build_runtime_lite_snapshot_uncached())

    assert snapshot["telegram_session_state"] == "open_or_unclean"
    assert snapshot["telegram_session"]["state"] == "open_or_unclean"
    assert snapshot["telegram_session"]["state_reason"] == "sqlite_sidecars_without_live_userbot"


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
    monkeypatch.setattr(
        WebApp,
        "_openclaw_models_status_snapshot",
        classmethod(lambda cls: {"providers": {}}),
    )
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


def test_model_catalog_cloud_presets_expose_full_runtime_registry_and_provider_states(monkeypatch):
    """Cloud catalog должен разделять runtime-ready модели и provider inventory без потери provider truth."""

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
            "google": {
                "models": [
                    {"id": "gemini-3.1-pro-preview", "name": "Gemini 3.1 Pro Preview", "reasoning": False, "contextWindow": 128000, "maxTokens": 16384}
                ]
            },
            "qwen-portal": {
                "models": [
                    {"id": "coder-model", "name": "Qwen Coder", "reasoning": False, "contextWindow": 128000, "maxTokens": 8192}
                ]
            },
        }
    }
    runtime_config = {
        "agents": {
            "defaults": {
                "model": {
                    "primary": "google-gemini-cli/gemini-3.1-pro-preview",
                    "fallbacks": [
                        "google/gemini-3.1-pro-preview",
                        "qwen-portal/coder-model",
                    ],
                }
            },
            "list": [{"model": "google-gemini-cli/gemini-3.1-pro-preview"}],
        }
    }
    auth_profiles = {
        "profiles": {
            "openai-codex:default": {"provider": "openai-codex"},
            "google-gemini-cli:default": {"provider": "google-gemini-cli"},
        }
    }
    full_catalog = {
        "count": 5,
        "providers": {
            "openai-codex": [
                {"key": "openai-codex/gpt-4.5-preview", "name": "ChatGPT 4.5 Preview", "contextWindow": 128000, "maxTokens": 16384, "available": True, "tags": ["configured"]},
                {"key": "openai-codex/gpt-5.4", "name": "GPT-5.4", "contextWindow": 272000, "maxTokens": 16384, "available": True, "tags": []},
            ],
            "google-gemini-cli": [
                {"key": "google-gemini-cli/gemini-3.1-pro-preview", "name": "Gemini 3.1 Pro Preview (Cloud Code Assist)", "contextWindow": 1048576, "maxTokens": 16384, "available": True, "tags": ["configured", "default"]},
                {"key": "google-gemini-cli/gemini-2.5-pro", "name": "Gemini 2.5 Pro (Cloud Code Assist)", "contextWindow": 1048576, "maxTokens": 8192, "available": True, "tags": []},
            ],
        },
    }
    status_snapshot = {
        "providers": {
            "openai-codex": {
                "provider": "openai-codex",
                "effective_kind": "profiles",
                "effective_detail": "~/.openclaw/agents/main/agent/auth-profiles.json",
                "oauth_status": "ok",
                "oauth_remaining_ms": 856013633,
                "oauth_remaining_human": "9д 21ч",
            },
            "google-gemini-cli": {
                "provider": "google-gemini-cli",
                "effective_kind": "profiles",
                "effective_detail": "~/.openclaw/agents/main/agent/auth-profiles.json",
                "oauth_status": "ok",
                "oauth_remaining_ms": 2877006,
                "oauth_remaining_human": "47м",
            },
        }
    }

    monkeypatch.setattr(WebApp, "_lmstudio_model_snapshot", _fake_lm_snapshot)
    monkeypatch.setattr(
        WebApp,
        "_load_openclaw_runtime_models",
        classmethod(lambda cls: runtime_payload),
    )
    monkeypatch.setattr(
        WebApp,
        "_load_openclaw_runtime_config",
        classmethod(lambda cls: runtime_config),
    )
    monkeypatch.setattr(
        WebApp,
        "_load_openclaw_auth_profiles",
        classmethod(lambda cls: auth_profiles),
    )
    monkeypatch.setattr(
        WebApp,
        "_runtime_signal_failed_providers",
        classmethod(lambda cls: {"openai-codex": "runtime_missing_scope_model_request"}),
    )
    monkeypatch.setattr(
        WebApp,
        "_openclaw_models_full_catalog",
        classmethod(lambda cls: full_catalog),
    )
    monkeypatch.setattr(
        WebApp,
        "_openclaw_models_status_snapshot",
        classmethod(lambda cls: status_snapshot),
    )
    client = _make_client_with_router(_TruthRouter())

    resp = client.get("/api/model/catalog")

    assert resp.status_code == 200
    data = resp.json()["catalog"]
    inventory_ids = {item["id"] for item in data["cloud_inventory"]}
    cloud_ids = {item["id"] for item in data["cloud_presets"]}
    assert "openai-codex/gpt-4.5-preview" in cloud_ids
    assert "google/gemini-3.1-pro-preview" in cloud_ids
    assert "qwen-portal/coder-model" in cloud_ids
    assert "google-antigravity/gemini-3.1-pro-preview" in cloud_ids
    assert "google-gemini-cli/gemini-3.1-pro-preview" in cloud_ids
    assert "openai-codex/gpt-5.4" in inventory_ids
    assert "openai-codex/gpt-5.4" not in cloud_ids
    assert "google-gemini-cli/gemini-2.5-pro" in inventory_ids
    assert "google-gemini-cli/gemini-2.5-pro" not in cloud_ids
    assert "openai/gpt-5-codex" not in cloud_ids
    provider_groups = {item["provider"]: item for item in data["cloud_provider_groups"]}
    assert provider_groups["openai-codex"]["provider_readiness_label"] == "Scope fail"
    assert provider_groups["google-gemini-cli"]["provider_readiness_label"] == "OAuth OK"
    assert provider_groups["google-gemini-cli"]["configured_model_count"] == 1
    assert provider_groups["google-gemini-cli"]["catalog_only_model_count"] == 1
    assert provider_groups["google-antigravity"]["legacy"] is True
    assert data["runtime_registry_source"] == "openclaw_models_json+openclaw_models_list_all"
    assert data["parallelism_truth"]["main_max_concurrent"] is None
    assert "queue concurrency" in data["parallelism_truth"]["summary_label"]


def test_runtime_quick_presets_keep_cloud_slots_cloud_only(monkeypatch):
    """Local Focus не должен записывать локальные модели в cloud-слоты."""

    monkeypatch.setattr(
        WebApp,
        "_build_runtime_cloud_presets",
        classmethod(
            lambda cls, current_slots=None: [
                {"id": "google/gemini-2.5-flash"},
                {"id": "google-gemini-cli/gemini-3.1-pro-preview"},
                {"id": "qwen-portal/coder-model"},
            ]
        ),
    )

    presets = WebApp._build_runtime_quick_presets(
        current_slots={
            "chat": "google/gemini-2.5-flash",
            "thinking": "google-gemini-cli/gemini-3.1-pro-preview",
            "pro": "google-gemini-cli/gemini-3.1-pro-preview",
            "coding": "qwen-portal/coder-model",
        },
        local_override="nvidia/nemotron-3-nano",
    )

    local_focus_slots = presets["local_focus"]["slots"]
    assert local_focus_slots["chat"] == "google/gemini-2.5-flash"
    assert local_focus_slots["thinking"] == "google-gemini-cli/gemini-3.1-pro-preview"
    assert all(not str(model_id).startswith("nvidia/") for model_id in local_focus_slots.values())


def test_runtime_provider_state_does_not_mark_api_key_provider_as_expired():
    """API-key провайдер не должен становиться Expired только из-за отсутствия OAuth-статуса."""
    payload = WebApp._runtime_provider_state(
        "google",
        runtime_models={
            "providers": {
                "google": {
                    "auth": "api-key",
                    "apiKey": "AIza-test",
                    "models": [
                        {"id": "gemini-2.5-flash", "name": "Gemini 2.5 Flash"},
                    ],
                }
            }
        },
        auth_profiles={"profiles": {}, "usageStats": {}},
        runtime_signal_failures={},
        status_snapshot={
            "providers": {
                "google": {
                    "provider": "google",
                    "effective_kind": "env",
                    "effective_detail": "AIzaSyAi...masked",
                    "oauth_status": "missing",
                }
            }
        },
    )

    assert payload["auth_mode"] == "api-key"
    assert payload["readiness"] == "ready"
    assert payload["readiness_label"] == "API key"
    assert payload["detail"] == "API key сконфигурирован."


def test_build_openclaw_runtime_controls_reads_context_and_thinking(monkeypatch):
    """Runtime-controls должны честно читать chain, contextTokens и thinking из live OpenClaw config."""
    runtime_config = {
        "agents": {
            "defaults": {
                "model": {
                    "primary": "openai-codex/gpt-5.4",
                    "fallbacks": [
                        "google/gemini-2.5-flash",
                        "lmstudio/local",
                    ],
                },
                "contextTokens": 200000,
                "thinkingDefault": "medium",
                "models": {
                    "openai-codex/gpt-5.4": {"params": {"thinking": "high"}},
                    "lmstudio/local": {"params": {"thinking": "off"}},
                },
            }
        }
    }
    monkeypatch.setattr(
        WebApp,
        "_load_openclaw_runtime_config",
        classmethod(lambda cls: runtime_config),
    )

    payload = WebApp._build_openclaw_runtime_controls()

    assert payload["primary"] == "openai-codex/gpt-5.4"
    assert payload["fallbacks"] == ["google/gemini-2.5-flash", "lmstudio/local"]
    assert payload["context_tokens"] == 200000
    assert payload["thinking_default"] == "medium"
    chain_items = {item["model_id"]: item for item in payload["chain_items"]}
    assert chain_items["openai-codex/gpt-5.4"]["explicit_thinking"] == "high"
    assert chain_items["openai-codex/gpt-5.4"]["effective_thinking"] == "high"
    assert chain_items["google/gemini-2.5-flash"]["effective_thinking"] == "medium"
    assert chain_items["lmstudio/local"]["explicit_thinking"] == "off"


def test_build_openclaw_parallelism_truth_reads_queue_caps(monkeypatch):
    """Parallelism truth должен показывать queue caps отдельно от named modes."""
    runtime_config = {
        "agents": {
            "defaults": {
                "maxConcurrent": 4,
                "subagents": {
                    "maxConcurrent": 8,
                },
            }
        }
    }
    monkeypatch.setattr(
        WebApp,
        "_load_openclaw_runtime_config",
        classmethod(lambda cls: runtime_config),
    )

    payload = WebApp._build_openclaw_parallelism_truth()

    assert payload["main_max_concurrent"] == 4
    assert payload["subagent_max_concurrent"] == 8
    assert "queue concurrency" in payload["summary_label"]
    assert "broadcast strategy" in payload["broadcast_note"]


def test_model_provider_action_launches_known_helper(monkeypatch, tmp_path: Path):
    """Provider-action должен открывать существующий helper для one-click OAuth repair."""
    monkeypatch.setenv("WEB_API_KEY", "secret")

    helper_path = tmp_path / "Login Gemini CLI OAuth.command"
    helper_path.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
    helper_path.chmod(0o755)

    launched: dict[str, object] = {}

    def _fake_launch(self, target_path: Path):
        launched["path"] = str(target_path)
        return {
            "ok": True,
            "exit_code": 0,
            "error": "",
            "launched": True,
            "path": str(target_path),
        }

    monkeypatch.setattr(
        WebApp,
        "_provider_repair_helper_path",
        classmethod(lambda cls, provider_name: helper_path if provider_name == "google-gemini-cli" else None),
    )
    monkeypatch.setattr(WebApp, "_launch_local_app", _fake_launch)

    class _Router:
        models = {"chat": "google-gemini-cli/gemini-3.1-pro-preview"}
        force_mode = "auto"

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

    monkeypatch.setattr(WebApp, "_lmstudio_model_snapshot", _fake_lm_snapshot)
    monkeypatch.setattr(WebApp, "_build_runtime_cloud_presets", classmethod(lambda cls, current_slots=None: []))
    monkeypatch.setattr(WebApp, "_build_openclaw_model_routing_status", lambda self: {})
    monkeypatch.setattr(WebApp, "_build_openclaw_runtime_controls", classmethod(lambda cls: {}))

    client = _make_client_with_router(_Router())
    response = client.post(
        "/api/model/provider-action",
        headers={"X-Krab-Web-Key": "secret"},
        json={"provider": "google-gemini-cli", "action": "repair_oauth"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["provider"] == "google-gemini-cli"
    assert data["action"] == "repair_oauth"
    assert str(launched["path"]).endswith("Login Gemini CLI OAuth.command")


def test_model_apply_set_runtime_chain_updates_live_openclaw_files(monkeypatch, tmp_path: Path):
    """`/api/model/apply` должен обновлять глобальную chain и runtime knobs в live OpenClaw config."""
    monkeypatch.setenv("WEB_API_KEY", "secret")

    openclaw_path = tmp_path / "openclaw.json"
    agent_path = tmp_path / "agent.json"
    openclaw_path.write_text(
        json.dumps(
            {
                "agents": {
                    "defaults": {
                        "model": {
                            "primary": "google/gemini-2.5-flash",
                            "fallbacks": ["qwen-portal/coder-model"],
                        },
                        "models": {
                            "google/gemini-2.5-flash": {"params": {"thinking": "off"}},
                            "qwen-portal/coder-model": {"params": {"thinking": "off"}},
                        },
                        "contextTokens": 128000,
                        "thinkingDefault": "off",
                        "subagents": {"model": "google/gemini-2.5-flash"},
                    },
                    "list": [
                        {"id": "main", "model": "google/gemini-2.5-flash"},
                    ],
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    agent_path.write_text(
        json.dumps({"id": "main", "model": "google/gemini-2.5-flash"}, ensure_ascii=False),
        encoding="utf-8",
    )

    monkeypatch.setattr(WebApp, "_openclaw_config_path", staticmethod(lambda: openclaw_path))
    monkeypatch.setattr(WebApp, "_openclaw_agent_config_path", classmethod(lambda cls: agent_path))
    monkeypatch.setattr(
        WebApp,
        "_openclaw_models_full_catalog",
        classmethod(lambda cls: {"providers": {}}),
    )
    monkeypatch.setattr(
        WebApp,
        "_openclaw_models_status_snapshot",
        classmethod(lambda cls: {"providers": {}}),
    )
    monkeypatch.setattr(
        WebApp,
        "_load_openclaw_auth_profiles",
        classmethod(lambda cls: {"profiles": {}, "usageStats": {}}),
    )
    monkeypatch.setattr(
        WebApp,
        "_build_runtime_cloud_presets",
        classmethod(
            lambda cls, current_slots=None: [
                {
                    "id": "openai-codex/gpt-5.4",
                    "provider": "openai-codex",
                    "provider_label": "OpenAI Codex",
                    "provider_auth": "oauth",
                    "configured_runtime": True,
                    "reasoning": True,
                    "context_window": 128000,
                },
                {
                    "id": "google/gemini-2.5-flash",
                    "provider": "google",
                    "provider_label": "Google",
                    "provider_auth": "api-key",
                    "configured_runtime": True,
                    "reasoning": False,
                    "context_window": 1000000,
                },
            ]
        ),
    )

    async def _fake_local_truth(self, router_obj, *, force_refresh: bool = False):
        return {
            "engine": "lm_studio",
            "active_model": "",
            "runtime_reachable": False,
            "loaded_models": [],
        }

    monkeypatch.setattr(WebApp, "_resolve_local_runtime_truth", _fake_local_truth)

    class _Router(_DummyRouter):
        def __init__(self) -> None:
            self.models = {"chat": "google/gemini-2.5-flash"}
            self.force_mode = "auto"
            self.local_engine = "lm_studio"

    client = _make_client_with_router(_Router())

    resp = client.post(
        "/api/model/apply",
        json={
            "action": "set_runtime_chain",
            "primary": "openai-codex/gpt-5.4",
            "fallbacks": [
                "google/gemini-2.5-flash",
                "lmstudio/local",
                "google/gemini-2.5-flash",
            ],
            "context_tokens": 200000,
            "thinking_default": "high",
            "slot_thinking": {
                "openai-codex/gpt-5.4": "high",
                "google/gemini-2.5-flash": "medium",
                "lmstudio/local": "off",
            },
        },
        headers={"X-Krab-Web-Key": "secret"},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["result"]["runtime"]["primary"] == "openai-codex/gpt-5.4"
    assert data["result"]["runtime"]["fallbacks"] == ["google/gemini-2.5-flash", "lmstudio/local"]
    assert Path(data["result"]["runtime"]["backup_openclaw_json"]).exists()
    assert Path(data["result"]["runtime"]["backup_agent_json"]).exists()

    openclaw_payload = json.loads(openclaw_path.read_text(encoding="utf-8"))
    defaults = openclaw_payload["agents"]["defaults"]
    assert defaults["model"]["primary"] == "openai-codex/gpt-5.4"
    assert defaults["model"]["fallbacks"] == ["google/gemini-2.5-flash", "lmstudio/local"]
    assert defaults["contextTokens"] == 200000
    assert defaults["thinkingDefault"] == "high"
    assert defaults["subagents"]["model"] == "openai-codex/gpt-5.4"
    assert defaults["models"]["openai-codex/gpt-5.4"]["params"]["thinking"] == "high"
    assert defaults["models"]["google/gemini-2.5-flash"]["params"]["thinking"] == "medium"
    assert defaults["models"]["lmstudio/local"]["params"]["thinking"] == "off"
    assert openclaw_payload["agents"]["list"][0]["model"] == "openai-codex/gpt-5.4"

    agent_payload = json.loads(agent_path.read_text(encoding="utf-8"))
    assert agent_payload["model"] == "openai-codex/gpt-5.4"


def test_openclaw_model_routing_status_reports_broken_primary_and_legacy_google_fallback(monkeypatch):
    """Routing status должен честно отражать сломанный primary и legacy Google fallback."""

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
            "openai-codex:default": {
                "failureCounts": {"model_not_found": 2},
                "cooldownUntil": int((time.time() + 60.0) * 1000.0),
            },
            "google-antigravity:vscode-free": {"disabledReason": "auth_permanent"},
        },
    }

    monkeypatch.setattr(WebApp, "_load_openclaw_runtime_config", classmethod(lambda cls: runtime_config))
    monkeypatch.setattr(WebApp, "_load_openclaw_runtime_models", classmethod(lambda cls: runtime_models))
    monkeypatch.setattr(WebApp, "_load_openclaw_auth_profiles", classmethod(lambda cls: auth_profiles))
    monkeypatch.setattr(WebApp, "_runtime_signal_failed_providers", classmethod(lambda cls: {}))
    monkeypatch.setattr(WebApp, "_openclaw_models_status_snapshot", classmethod(lambda cls: {"providers": {}}))
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
    assert data["google_antigravity_legacy_removed"] is True
    assert any("model_not_found" in item for item in data["warnings"])
    assert any("legacy" in item.lower() or "удал" in item.lower() for item in data["warnings"])


def test_openclaw_model_routing_status_skips_expired_google_gemini_cli_fallback(monkeypatch):
    """Временная рекомендация не должна выбирать `google-gemini-cli`, если OAuth уже expired/cooldown."""

    runtime_config = {
        "agents": {
            "defaults": {
                "model": {
                    "primary": "openai-codex/gpt-5.4",
                    "fallbacks": [
                        "google-gemini-cli/gemini-3.1-pro-preview",
                        "google/gemini-3.1-pro-preview",
                        "qwen-portal/coder-model",
                    ],
                },
                "workspace": "/Users/pablito/.openclaw/workspace-main-messaging",
            }
        }
    }
    runtime_models = {
        "providers": {
            "openai-codex": {"models": [{"id": "gpt-5.4"}]},
            "google": {"models": [{"id": "google/gemini-3.1-pro-preview"}]},
        }
    }
    auth_profiles = {
        "profiles": {
            "openai-codex:default": {"provider": "openai-codex"},
            "google-gemini-cli:default": {
                "provider": "google-gemini-cli",
                "expires": int((time.time() - 60.0) * 1000.0),
            },
        },
        "usageStats": {
            "openai-codex:default": {
                "failureCounts": {"model_not_found": 1},
                "cooldownUntil": int((time.time() + 60.0) * 1000.0),
            },
            "google-gemini-cli:default": {
                "failureCounts": {"auth": 1},
                "cooldownUntil": int((time.time() + 60.0) * 1000.0),
            },
        },
    }

    monkeypatch.setattr(WebApp, "_load_openclaw_runtime_config", classmethod(lambda cls: runtime_config))
    monkeypatch.setattr(WebApp, "_load_openclaw_runtime_models", classmethod(lambda cls: runtime_models))
    monkeypatch.setattr(WebApp, "_load_openclaw_auth_profiles", classmethod(lambda cls: auth_profiles))
    monkeypatch.setattr(WebApp, "_runtime_signal_failed_providers", classmethod(lambda cls: {}))
    monkeypatch.setattr(WebApp, "_openclaw_models_status_snapshot", classmethod(lambda cls: {"providers": {}}))

    client = _make_client_with_router(_DummyRouter())
    resp = client.get("/api/openclaw/model-routing/status")

    assert resp.status_code == 200
    data = resp.json()["routing"]
    assert data["current_primary_broken"] is True
    assert data["temporary_primary_recommendation"] == "google/gemini-3.1-pro-preview"
    assert data["google_gemini_cli"]["cooldown_active"] is True
    assert data["google_gemini_cli"]["expired_profiles"] == [
        {"profile": "google-gemini-cli:default", "reason": "expired"}
    ]
    assert any("google gemini cli" in item.lower() for item in data["warnings"])


def test_openclaw_model_routing_status_clears_broken_flag_when_live_primary_verified(monkeypatch):
    """Последний успешный live route на primary должен сбрасывать stale broken-диагностику."""

    runtime_config = {
        "agents": {
            "defaults": {
                "model": {
                    "primary": "openai-codex/gpt-5.4",
                    "fallbacks": [
                        "google/gemini-3.1-pro-preview",
                    ],
                },
                "workspace": "/Users/pablito/.openclaw/workspace-main-messaging",
            }
        }
    }
    runtime_models = {
        "providers": {
            "openai-codex": {"models": [{"id": "gpt-5.4"}]},
            "google": {"models": [{"id": "google/gemini-3.1-pro-preview"}]},
        }
    }
    auth_profiles = {
        "profiles": {
            "openai-codex:default": {"provider": "openai-codex"},
        },
        "usageStats": {
            "openai-codex:default": {
                "failureCounts": {"model_not_found": 3},
                "cooldownUntil": int((time.time() + 60.0) * 1000.0),
            },
        },
    }

    class _PrimaryVerifiedOpenClaw(_FakeOpenClaw):
        def get_last_runtime_route(self):
            return {
                "channel": "openclaw_cloud",
                "provider": "openai-codex",
                "model": "openai-codex/gpt-5.4",
                "status": "ok",
            }

    monkeypatch.setattr(WebApp, "_load_openclaw_runtime_config", classmethod(lambda cls: runtime_config))
    monkeypatch.setattr(WebApp, "_load_openclaw_runtime_models", classmethod(lambda cls: runtime_models))
    monkeypatch.setattr(WebApp, "_load_openclaw_auth_profiles", classmethod(lambda cls: auth_profiles))
    monkeypatch.setattr(WebApp, "_runtime_signal_failed_providers", classmethod(lambda cls: {}))
    monkeypatch.setattr(WebApp, "_openclaw_models_status_snapshot", classmethod(lambda cls: {"providers": {}}))

    client = _make_client_with_router(_DummyRouter(), openclaw_client=_PrimaryVerifiedOpenClaw())
    resp = client.get("/api/openclaw/model-routing/status")

    assert resp.status_code == 200
    data = resp.json()["routing"]
    assert data["current_primary_broken"] is False
    assert data["temporary_primary_recommendation"] == "openai-codex/gpt-5.4"
    assert data["live_primary_verified"] is True
    assert not any("openai primary падает" in item.lower() for item in data["warnings"])


def test_openclaw_model_routing_status_marks_live_fallback_as_active(monkeypatch):
    """Если live route уже ушёл на fallback, endpoint должен показывать активный fallback, а не primary."""

    runtime_config = {
        "agents": {
            "defaults": {
                "model": {
                    "primary": "openai-codex/gpt-5.4",
                    "fallbacks": [
                        "google/gemini-3.1-pro-preview",
                        "qwen-portal/coder-model",
                    ],
                },
                "workspace": "/Users/pablito/.openclaw/workspace-main-messaging",
            }
        }
    }
    runtime_models = {
        "providers": {
            "openai-codex": {"models": [{"id": "gpt-5.4"}]},
            "google": {"models": [{"id": "google/gemini-3.1-pro-preview"}]},
        }
    }
    auth_profiles = {
        "profiles": {
            "openai-codex:default": {"provider": "openai-codex"},
        },
        "usageStats": {
            "openai-codex:default": {},
        },
    }

    class _FallbackActiveOpenClaw(_FakeOpenClaw):
        def get_last_runtime_route(self):
            return {
                "channel": "openclaw_cloud",
                "provider": "google",
                "model": "google/gemini-3.1-pro-preview",
                "status": "ok",
                "route_reason": "openclaw_response_ok",
                "route_detail": "Ответ получен через OpenClaw API; gateway fallback -> google/gemini-3.1-pro-preview",
            }

    monkeypatch.setattr(WebApp, "_load_openclaw_runtime_config", classmethod(lambda cls: runtime_config))
    monkeypatch.setattr(WebApp, "_load_openclaw_runtime_models", classmethod(lambda cls: runtime_models))
    monkeypatch.setattr(WebApp, "_load_openclaw_auth_profiles", classmethod(lambda cls: auth_profiles))
    monkeypatch.setattr(WebApp, "_runtime_signal_failed_providers", classmethod(lambda cls: {}))
    monkeypatch.setattr(WebApp, "_openclaw_models_status_snapshot", classmethod(lambda cls: {"providers": {}}))

    client = _make_client_with_router(_DummyRouter(), openclaw_client=_FallbackActiveOpenClaw())
    resp = client.get("/api/openclaw/model-routing/status")

    assert resp.status_code == 200
    data = resp.json()["routing"]
    assert data["current_primary_broken"] is True
    assert data["temporary_primary_recommendation"] == "google/gemini-3.1-pro-preview"
    assert data["live_primary_verified"] is False
    assert data["live_fallback_active"] is True
    assert data["live_active_model"] == "google/gemini-3.1-pro-preview"
    assert any("active route идёт через fallback" in item for item in data["warnings"])


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


def test_routing_effective_prefers_last_route_model_over_stale_router_slot(monkeypatch):
    """`/api/openclaw/routing/effective` должен показывать фактическую cloud-модель, а не stale default-slot."""

    class _TruthModelManager:
        def get_current_model(self):
            return ""

        async def get_loaded_models(self):
            return []

    class _TruthRouter(_DummyRouter):
        def __init__(self) -> None:
            self._mm = _TruthModelManager()
            self.is_local_available = False
            self.active_local_model = ""
            self.local_engine = "lm_studio"
            self.lm_studio_url = "http://127.0.0.1:1234"
            self.models = {"chat": "openai-codex/gpt-5.4"}
            self.force_mode = "force_cloud"
            self.routing_policy = "free_first_hybrid"
            self.cloud_soft_cap_reached = False

        def get_last_route(self):
            return {
                "status": "ok",
                "channel": "openclaw_cloud",
                "model": "google-gemini-cli/gemini-3.1-pro-preview",
                "route_reason": "openclaw_response_ok",
                "route_detail": "Ответ получен через OpenClaw API; gateway fallback -> google-gemini-cli/gemini-3.1-pro-preview",
            }

    async def _fake_lm_snapshot(self, *args, **kwargs):
        return {
            "state": "not_loaded",
            "base_url": "http://127.0.0.1:1234",
            "loaded_count": 0,
            "loaded_models": [],
            "error": "",
        }

    monkeypatch.setattr(WebApp, "_lmstudio_model_snapshot", _fake_lm_snapshot)
    client = _make_client_with_router(_TruthRouter())

    resp = client.get("/api/openclaw/routing/effective")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["assistant_default_model"] == "openai-codex/gpt-5.4"
    assert data["active_slot_or_model"] == "google-gemini-cli/gemini-3.1-pro-preview"
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


def test_parse_openclaw_channels_probe_prefers_probe_works_over_disconnected_tail():
    """Успешный probe должен побеждать промежуточный `disconnected` в transport meta."""

    sample = """
Checking channel status (probe)…
Gateway reachable.
- Discord default: enabled, configured, running, disconnected, bot:@OpenClaw, token:config, intents:content=limited, works
""".strip()

    parsed = WebApp._parse_openclaw_channels_probe(sample)
    assert parsed["gateway_reachable"] is True
    assert len(parsed["channels"]) == 1
    assert parsed["channels"][0]["name"] == "Discord default"
    assert parsed["channels"][0]["status"] == "OK"


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

        async def get(self, url: str, *, headers=None):
            assert url == "http://127.0.0.1:18791/"
            assert "Authorization" not in (headers or {})
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
    monkeypatch.setattr(WebApp, "_openclaw_gateway_token_from_config", staticmethod(lambda: ""))
    client = _make_client()

    resp = client.get("/api/openclaw/browser-smoke")
    assert resp.status_code == 200
    smoke = resp.json()["report"]["browser_smoke"]
    assert smoke["relay_reachable"] is True
    assert smoke["browser_http_state"] == "auth_required"
    assert smoke["tab_attached"] is False
    assert smoke["browser_auth_required"] is True


def test_browser_smoke_uses_gateway_token_and_marks_authorized(monkeypatch):
    """Авторизованный browser relay probe должен ходить с gateway token и не светить ложный auth_required."""

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

    seen_headers: dict[str, str] = {}

    class _AsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url: str, *, headers=None):
            assert url == "http://127.0.0.1:18791/"
            seen_headers.update(headers or {})
            return _Resp(200)

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
    monkeypatch.setattr(
        WebApp,
        "_openclaw_gateway_token_from_config",
        staticmethod(lambda: "gateway-token-from-config"),
    )
    client = _make_client()

    resp = client.get("/api/openclaw/browser-smoke")
    assert resp.status_code == 200
    smoke = resp.json()["report"]["browser_smoke"]
    assert seen_headers["Authorization"] == "Bearer gateway-token-from-config"
    assert smoke["relay_reachable"] is True
    assert smoke["browser_http_state"] == "authorized"
    assert smoke["tab_attached"] is False
    assert smoke["browser_auth_required"] is False


def test_browser_mcp_readiness_reports_stage_and_mcp_drift(monkeypatch, tmp_path: Path):
    """`/api/openclaw/browser-mcp-readiness` должен показывать staged browser state и drift LM Studio MCP."""

    class _Proc:
        def __init__(self, stdout_text: str = "", stderr_text: str = "", *, returncode: int = 0):
            self.returncode = returncode
            self._stdout = stdout_text.encode("utf-8")
            self._stderr = stderr_text.encode("utf-8")

        async def communicate(self):
            return self._stdout, self._stderr

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

        async def get(self, url: str, *, headers=None):
            assert url == "http://127.0.0.1:18791/"
            assert "Authorization" not in (headers or {})
            return _Resp(401)

    async def _fake_create_subprocess_exec(*cmd, **kwargs):
        if cmd[:3] == ("openclaw", "gateway", "probe"):
            stdout_text = """
Gateway Status
Reachable: yes
Probe budget: 3000ms

Targets
Local loopback ws://127.0.0.1:18789
  Connect: ok (15ms) · RPC: ok
""".strip()
            return _Proc(stdout_text, returncode=0)
        if cmd[:4] == ("openclaw", "browser", "--json", "status"):
            return _Proc(json.dumps({
                "running": True,
                "cdpReady": True,
                "profile": "openclaw",
                "cdpUrl": "http://127.0.0.1:18800",
                "cdpPort": 18800,
                "detectedBrowser": "chrome",
            }), returncode=0)
        if cmd[:4] == ("openclaw", "browser", "--json", "tabs"):
            return _Proc(json.dumps({"tabs": []}), returncode=0)
        raise AssertionError(f"Неожиданный вызов subprocess: {cmd}")

    managed_registry = {
        "filesystem": {"description": "files"},
        "memory": {"description": "memory"},
        "openclaw-browser": {"description": "browser"},
        "chrome-profile": {"description": "chrome"},
    }

    def _fake_resolve(name: str):
        base = {
            "name": name,
            "description": name,
            "risk": "medium",
            "missing_env": [],
            "manual_setup": [],
        }
        if name == "chrome-profile":
            base["manual_setup"] = ["Включить Remote Debugging в Chrome."]
        return base

    lmstudio_path = tmp_path / "mcp.json"
    lmstudio_path.write_text(
        json.dumps({"mcpServers": {"filesystem": {}, "memory": {}}}),
        encoding="utf-8",
    )

    monkeypatch.setattr("src.modules.web_app.asyncio.create_subprocess_exec", _fake_create_subprocess_exec)
    monkeypatch.setattr("src.modules.web_app.httpx.AsyncClient", _AsyncClient)
    monkeypatch.setattr(WebApp, "_openclaw_gateway_token_from_config", staticmethod(lambda: ""))
    monkeypatch.setattr("src.modules.web_app.get_managed_mcp_servers", lambda: managed_registry)
    monkeypatch.setattr("src.modules.web_app.resolve_managed_server_launch", _fake_resolve)
    monkeypatch.setattr(
        "src.modules.web_app.build_lmstudio_mcp_json",
        lambda include_optional_missing=False, include_high_risk=False: (
            {"mcpServers": {"filesystem": {}, "memory": {}, "openclaw-browser": {}}},
            {
                "included": ["filesystem", "memory", "openclaw-browser"],
                "skipped_missing": [],
                "skipped_risk": ["filesystem-home", "shell"],
                "managed_names": ["filesystem", "memory", "openclaw-browser", "chrome-profile"],
            },
        ),
    )
    monkeypatch.setattr("src.modules.web_app.LMSTUDIO_MCP_PATH", lmstudio_path)
    client = _make_client()

    resp = client.get("/api/openclaw/browser-mcp-readiness")
    assert resp.status_code == 200
    data = resp.json()
    assert data["overall"]["readiness"] == "attention"
    assert data["browser"]["state"] == "auth_required"
    assert data["browser"]["readiness"] == "attention"
    assert data["browser"]["runtime"]["tabs_count"] == 0
    assert data["mcp"]["readiness"] == "attention"
    assert data["mcp"]["sync"]["status"] == "drift"
    assert data["mcp"]["summary"]["required_ready"] == 2
    assert data["mcp"]["summary"]["required_attention"] == 1
    chrome_profile = next(item for item in data["mcp"]["servers"] if item["name"] == "chrome-profile")
    assert chrome_profile["state"] == "manual_setup_required"


def test_browser_mcp_readiness_treats_reachable_auth_relay_as_attention_even_if_status_stale():
    """Reachable relay с `auth_required` не должен краснеть только из-за stale `running=false`."""

    browser = WebApp._classify_browser_stage(
        {"running": False, "cdpReady": False, "profile": "openclaw"},
        {"tabs": []},
        {
            "relay_reachable": True,
            "browser_http_reachable": True,
            "browser_auth_required": True,
            "tab_attached": False,
            "browser_http_state": "auth_required",
            "detail": "browser relay auth required (401)",
        },
    )

    assert browser["state"] == "auth_required"
    assert browser["readiness"] == "attention"
    assert any("stale status" in item for item in browser["warnings"])


def test_browser_mcp_readiness_marks_authorized_running_browser_with_tabs_as_ready():
    """Авторизованный relay + running browser + вкладки должен давать ready stage без ложного auth_required."""

    browser = WebApp._classify_browser_stage(
        {"running": True, "cdpReady": True, "profile": "openclaw"},
        {"tabs": [{"targetId": "abc"}]},
        {
            "relay_reachable": True,
            "browser_http_reachable": True,
            "browser_auth_required": False,
            "tab_attached": False,
            "browser_http_state": "authorized",
            "detail": "browser relay authorized (200)",
        },
    )

    assert browser["state"] == "attached"
    assert browser["readiness"] == "ready"
    assert browser["runtime"]["tabs_count"] == 1
    assert browser["warnings"] == []


def test_browser_mcp_readiness_distinguishes_dedicated_debug_browser_from_owner_attach():
    """Dedicated OpenClaw profile не должен притворяться owner attach к обычному Chrome."""

    browser = WebApp._classify_browser_stage(
        {
            "running": True,
            "cdpReady": True,
            "profile": "openclaw",
            "attachOnly": False,
            "chosenBrowser": "chrome",
            "detectedBrowser": "chrome",
            "userDataDir": "/Users/test/.openclaw/browser/openclaw/user-data",
        },
        {"tabs": [{"targetId": "abc"}]},
        {
            "relay_reachable": True,
            "browser_http_reachable": True,
            "browser_auth_required": False,
            "tab_attached": False,
            "browser_http_state": "authorized",
            "detail": "browser relay authorized (200)",
        },
    )

    assert browser["state"] == "debug_attached"
    assert browser["readiness"] == "attention"
    assert browser["stage_label"] == "Подключён Debug browser"
    assert browser["runtime"]["active_contour"] == "debug_browser"
    assert browser["runtime"]["active_contour_label"] == "Debug browser"
    assert browser["runtime"]["owner_attach_confirmed"] is False
    assert browser["runtime"]["debug_attach_confirmed"] is True
    assert any("dedicated debug browser" in item for item in browser["warnings"])


def test_browser_access_paths_include_relay_and_chrome_devtools():
    """Browser readiness должен явно отдавать оба канонических пути доступа: relay и Chrome DevTools."""

    browser = {
        "readiness": "attention",
        "state": "tab_not_connected",
        "summary": "Dedicated debug browser жив, но owner Chrome ещё не attach-нут.",
        "next_step": "Открой вкладку в debug browser или переключись на attach к обычному Chrome.",
        "runtime": {
            "running": True,
            "active_contour": "debug_browser",
            "active_contour_label": "Debug browser",
            "owner_attach_confirmed": False,
        },
    }
    mcp = {
        "servers": [
            {
                "name": "chrome-profile",
                "readiness": "attention",
                "state": "manual_setup_required",
                "detail": "В Chrome профиле включить Remote Debugging на chrome://inspect/#remote-debugging.",
                "manual_setup": ["В Chrome профиле включить Remote Debugging на chrome://inspect/#remote-debugging."],
            }
        ]
    }

    paths = WebApp._build_browser_access_paths(browser, mcp)

    assert len(paths) == 2
    relay_path = next(item for item in paths if item["kind"] == "openclaw_relay")
    devtools_path = next(item for item in paths if item["kind"] == "chrome_devtools")
    assert relay_path["active"] is True
    assert relay_path["active_label"] == "Debug browser"
    assert relay_path["confirmed"] is False
    assert devtools_path["active"] is False
    assert devtools_path["state"] == "manual_setup_required"
    assert "Remote Debugging" in devtools_path["next_step"]


def test_openclaw_runtime_config_exposes_runtime_policy(monkeypatch):
    """`/api/openclaw/runtime-config` должен отдавать read-only policy truth для панели."""

    monkeypatch.setattr(WebApp, "_openclaw_gateway_token_from_config", staticmethod(lambda: "runtime-token-123"))
    monkeypatch.setattr("src.modules.web_app.config.OPENCLAW_URL", "http://127.0.0.1:18789")
    monkeypatch.setattr("src.modules.web_app.config.LM_STUDIO_NATIVE_REASONING_MODE", "off")
    monkeypatch.setattr("src.modules.web_app.config.USERBOT_MAX_OUTPUT_TOKENS", 1200)
    monkeypatch.setattr("src.modules.web_app.config.USERBOT_PHOTO_MAX_OUTPUT_TOKENS", 420)
    monkeypatch.setattr("src.modules.web_app.config.HISTORY_WINDOW_MESSAGES", 50)
    monkeypatch.setattr("src.modules.web_app.config.HISTORY_WINDOW_MAX_CHARS", None)
    monkeypatch.setattr("src.modules.web_app.config.LOCAL_HISTORY_WINDOW_MESSAGES", 18)
    monkeypatch.setattr("src.modules.web_app.config.LOCAL_HISTORY_WINDOW_MAX_CHARS", 12000)
    monkeypatch.setattr("src.modules.web_app.config.RETRY_HISTORY_WINDOW_MESSAGES", 8)
    monkeypatch.setattr("src.modules.web_app.config.RETRY_HISTORY_WINDOW_MAX_CHARS", 4000)
    monkeypatch.setattr("src.modules.web_app.config.RETRY_MESSAGE_MAX_CHARS", 1200)
    monkeypatch.setattr("src.modules.web_app.config.OPENCLAW_CHUNK_TIMEOUT_SEC", 180.0)
    monkeypatch.setattr("src.modules.web_app.config.OPENCLAW_FIRST_CHUNK_TIMEOUT_SEC", 420.0)
    monkeypatch.setattr("src.modules.web_app.config.OPENCLAW_PHOTO_FIRST_CHUNK_TIMEOUT_SEC", 540.0)
    monkeypatch.setattr("src.modules.web_app.config.FORCE_CLOUD", False)
    monkeypatch.setattr("src.modules.web_app.config.LOCAL_FALLBACK_ENABLED", True)
    monkeypatch.setattr("src.modules.web_app.config.USERBOT_FORCE_CLOUD_FOR_PHOTO", True)
    client = _make_client()

    resp = client.get("/api/openclaw/runtime-config")
    assert resp.status_code == 200
    data = resp.json()
    assert data["gateway_auth_state"] == "configured"
    assert data["gateway_token_masked"] == "run...123"
    assert data["runtime_policy"]["native_reasoning_mode"] == "off"
    assert data["runtime_policy"]["output_tokens"]["text"] == 1200
    assert data["runtime_policy"]["history_budget"]["local_max_chars"] == 12000
    assert data["runtime_policy"]["timeouts_sec"]["photo_first_chunk"] == 540.0


def test_browser_mcp_readiness_retries_transient_empty_cli_state_when_relay_authorized(monkeypatch, tmp_path: Path):
    """При `authorized` relay endpoint должен пережидать краткий CLI-флап status/tabs и не застревать в false attention."""

    class _Proc:
        def __init__(self, stdout_text: str = "", stderr_text: str = "", *, returncode: int = 0):
            self.returncode = returncode
            self._stdout = stdout_text.encode("utf-8")
            self._stderr = stderr_text.encode("utf-8")

        async def communicate(self):
            return self._stdout, self._stderr

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

        async def get(self, url: str, *, headers=None):
            assert url == "http://127.0.0.1:18791/"
            assert headers == {"Accept": "application/json", "Authorization": "Bearer gateway-token-from-config"}
            return _Resp(200)

    status_calls = {"count": 0}
    tabs_calls = {"count": 0}

    async def _fast_sleep(_sec: float):
        return None

    async def _fake_create_subprocess_exec(*cmd, **kwargs):
        if cmd[:3] == ("openclaw", "gateway", "probe"):
            stdout_text = """
Gateway Status
Reachable: yes
Probe budget: 3000ms

Targets
Local loopback ws://127.0.0.1:18789
  Connect: ok (15ms) · RPC: ok
""".strip()
            return _Proc(stdout_text, returncode=0)
        if cmd[:4] == ("openclaw", "browser", "--json", "status"):
            status_calls["count"] += 1
            payload = {"running": False, "cdpReady": False, "profile": "openclaw"}
            if status_calls["count"] >= 2:
                payload = {
                    "running": True,
                    "cdpReady": True,
                    "profile": "openclaw",
                    "cdpUrl": "http://127.0.0.1:18800",
                    "cdpPort": 18800,
                    "detectedBrowser": "chrome",
                }
            return _Proc(json.dumps(payload), returncode=0)
        if cmd[:4] == ("openclaw", "browser", "--json", "tabs"):
            tabs_calls["count"] += 1
            payload = {"tabs": []}
            if tabs_calls["count"] >= 2:
                payload = {"tabs": [{"targetId": "abc", "title": "Example Domain", "url": "https://example.com/"}]}
            return _Proc(json.dumps(payload), returncode=0)
        raise AssertionError(f"Неожиданный вызов subprocess: {cmd}")

    lmstudio_path = tmp_path / "mcp.json"
    lmstudio_path.write_text(
        json.dumps({"mcpServers": {"filesystem": {}, "memory": {}, "openclaw-browser": {}}}),
        encoding="utf-8",
    )

    monkeypatch.setattr("src.modules.web_app.asyncio.create_subprocess_exec", _fake_create_subprocess_exec)
    monkeypatch.setattr("src.modules.web_app.httpx.AsyncClient", _AsyncClient)
    monkeypatch.setattr(WebApp, "_openclaw_gateway_token_from_config", staticmethod(lambda: "gateway-token-from-config"))
    monkeypatch.setattr("src.modules.web_app.get_managed_mcp_servers", lambda: {"filesystem": {}, "memory": {}, "openclaw-browser": {}})
    monkeypatch.setattr(
        "src.modules.web_app.resolve_managed_server_launch",
        lambda name: {"name": name, "description": name, "risk": "medium", "missing_env": [], "manual_setup": []},
    )
    monkeypatch.setattr(
        "src.modules.web_app.build_lmstudio_mcp_json",
        lambda include_optional_missing=False, include_high_risk=False: (
            {"mcpServers": {"filesystem": {}, "memory": {}, "openclaw-browser": {}}},
            {
                "included": ["filesystem", "memory", "openclaw-browser"],
                "skipped_missing": [],
                "skipped_risk": [],
                "managed_names": ["filesystem", "memory", "openclaw-browser"],
            },
        ),
    )
    monkeypatch.setattr("src.modules.web_app.LMSTUDIO_MCP_PATH", lmstudio_path)
    monkeypatch.setattr("src.modules.web_app.asyncio.sleep", _fast_sleep)
    client = _make_client()

    resp = client.get("/api/openclaw/browser-mcp-readiness")
    assert resp.status_code == 200
    data = resp.json()
    assert data["browser"]["state"] == "attached"
    assert data["browser"]["readiness"] == "ready"
    assert data["browser"]["runtime"]["tabs_count"] == 1
    assert status_calls["count"] >= 2
    assert tabs_calls["count"] >= 2


def test_browser_start_endpoint_returns_updated_readiness(monkeypatch):
    """`POST /api/openclaw/browser/start` должен поднимать browser и возвращать обновлённый staged readiness."""

    class _Proc:
        def __init__(self, stdout_text: str = "", stderr_text: str = "", *, returncode: int = 0):
            self.returncode = returncode
            self._stdout = stdout_text.encode("utf-8")
            self._stderr = stderr_text.encode("utf-8")

        async def communicate(self):
            return self._stdout, self._stderr

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

        async def get(self, url: str, *, headers=None):
            assert url == "http://127.0.0.1:18791/"
            assert headers == {"Accept": "application/json", "Authorization": "Bearer gateway-token-from-config"}
            return _Resp(200)

    async def _fake_create_subprocess_exec(*cmd, **kwargs):
        if cmd[:3] == ("openclaw", "gateway", "probe"):
            return _Proc(
                """
Gateway Status
Reachable: yes
Probe budget: 3000ms

Targets
Local loopback ws://127.0.0.1:18789
  Connect: ok (15ms) · RPC: ok
""".strip(),
                returncode=0,
            )
        if cmd[:4] == ("openclaw", "browser", "--json", "start"):
            return _Proc(
                json.dumps(
                    {
                        "running": True,
                        "cdpReady": True,
                        "profile": "openclaw",
                        "cdpUrl": "http://127.0.0.1:18800",
                        "cdpPort": 18800,
                        "detectedBrowser": "chrome",
                    }
                ),
                returncode=0,
            )
        if cmd[:4] == ("openclaw", "browser", "--json", "status"):
            return _Proc(
                json.dumps(
                    {
                        "running": True,
                        "cdpReady": True,
                        "profile": "openclaw",
                        "cdpUrl": "http://127.0.0.1:18800",
                        "cdpPort": 18800,
                        "detectedBrowser": "chrome",
                    }
                ),
                returncode=0,
            )
        if cmd[:4] == ("openclaw", "browser", "--json", "tabs"):
            return _Proc(
                json.dumps({"tabs": [{"targetId": "abc", "title": "Example Domain", "url": "https://example.com/"}]}),
                returncode=0,
            )
        raise AssertionError(f"Неожиданный вызов subprocess: {cmd}")

    monkeypatch.setattr("src.modules.web_app.asyncio.create_subprocess_exec", _fake_create_subprocess_exec)
    monkeypatch.setattr("src.modules.web_app.httpx.AsyncClient", _AsyncClient)
    monkeypatch.setattr(WebApp, "_openclaw_gateway_token_from_config", staticmethod(lambda: "gateway-token-from-config"))
    client = _make_client()

    resp = client.post("/api/openclaw/browser/start", json={})
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["browser"]["state"] == "attached"
    assert data["browser"]["readiness"] == "ready"
    assert data["browser"]["runtime"]["tabs_count"] == 1


def test_open_owner_chrome_endpoint_uses_existing_command_helper(monkeypatch, tmp_path: Path):
    """`POST /api/openclaw/browser/open-owner-chrome` должен использовать существующий `.command` helper."""

    helper_path = tmp_path / "new Enable Chrome Remote Debugging.command"
    helper_path.write_text("#!/bin/zsh\nexit 0\n", encoding="utf-8")
    helper_path.chmod(0o755)
    calls: list[tuple[str, ...]] = []

    class _Popen:
        def __init__(self, cmd, stdout=None, stderr=None):
            calls.append(tuple(str(part) for part in cmd))

    monkeypatch.setattr(WebApp, "_project_root", staticmethod(lambda: tmp_path))
    monkeypatch.setattr("src.modules.web_app.subprocess.Popen", _Popen)
    client = _make_client()

    resp = client.post("/api/openclaw/browser/open-owner-chrome", json={})
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["launcher"] == "command"
    assert data["helper_path"] == str(helper_path)
    assert calls == [("open", str(helper_path))]


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


def test_classify_browser_http_probe_authorized_state():
    """200 в browser probe означает авторизованный relay, но не автоматически attach."""
    parsed = WebApp._classify_browser_http_probe(200, "")
    assert parsed["state"] == "authorized"
    assert parsed["reachable"] is True
    assert parsed["auth_required"] is False


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


def test_inbox_status_and_items_return_persisted_summary(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Inbox endpoints должны отдавать persisted summary и open items."""
    inbox = InboxService(state_path=tmp_path / "inbox.json")
    inbox.upsert_reminder(
        reminder_id="abc123",
        chat_id="-10077",
        text="проверить контракт",
        due_at_iso="2026-03-12T10:00:00+00:00",
    )
    monkeypatch.setattr("src.modules.web_app.inbox_service", inbox)
    client = _make_client()

    status_resp = client.get("/api/inbox/status")
    items_resp = client.get("/api/inbox/items")

    assert status_resp.status_code == 200
    assert items_resp.status_code == 200
    status_payload = status_resp.json()
    items_payload = items_resp.json()
    assert status_payload["ok"] is True
    assert status_payload["summary"]["open_items"] == 1
    assert items_payload["ok"] is True
    assert items_payload["items"][0]["kind"] == "reminder"


def test_inbox_update_requires_web_key(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Write endpoint inbox должен уважать WEB_API_KEY."""
    monkeypatch.setenv("WEB_API_KEY", "secret")
    inbox = InboxService(state_path=tmp_path / "inbox.json")
    item = inbox.upsert_item(
        dedupe_key="watch:gateway_down",
        kind="watch_alert",
        source="proactive-watch",
        title="Gateway недоступен",
        body="gateway down",
        severity="error",
    )["item"]
    monkeypatch.setattr("src.modules.web_app.inbox_service", inbox)
    client = _make_client()

    denied = client.post("/api/inbox/update", json={"item_id": item["item_id"], "status": "acked"})
    allowed = client.post(
        "/api/inbox/update",
        json={"item_id": item["item_id"], "status": "acked", "note": "owner ui saw it"},
        headers={"X-Krab-Web-Key": "secret"},
    )

    assert denied.status_code == 403
    assert denied.json()["detail"] == "forbidden: invalid WEB_API_KEY"
    assert allowed.status_code == 200
    assert allowed.json()["result"]["item"]["status"] == "acked"
    assert allowed.json()["result"]["item"]["metadata"]["last_action_actor"] == "owner-ui"
    assert allowed.json()["result"]["item"]["metadata"]["last_action_note"] == "owner ui saw it"


def test_inbox_create_builds_owner_task_and_approval_request(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Create endpoint должен уметь создавать owner-task и approval-request."""
    monkeypatch.setenv("WEB_API_KEY", "secret")
    inbox = InboxService(state_path=tmp_path / "inbox.json")
    monkeypatch.setattr("src.modules.web_app.inbox_service", inbox)
    client = _make_client()

    task_resp = client.post(
        "/api/inbox/create",
        json={
            "kind": "owner_task",
            "title": "Проверить reserve bot",
            "body": "Нужен post-restart smoke.",
            "task_key": "reserve-bot-smoke",
        },
        headers={"X-Krab-Web-Key": "secret"},
    )
    approval_resp = client.post(
        "/api/inbox/create",
        json={
            "kind": "approval_request",
            "title": "Разрешить платный cloud route",
            "body": "Нужен production smoke.",
            "request_key": "paid-cloud-route",
            "trace_id": "approval:manual-trace",
            "approval_scope": "money",
            "requested_action": "enable_paid_cloud_route",
        },
        headers={"X-Krab-Web-Key": "secret"},
    )

    assert task_resp.status_code == 200
    assert approval_resp.status_code == 200
    assert task_resp.json()["result"]["item"]["kind"] == "owner_task"
    assert approval_resp.json()["result"]["item"]["kind"] == "approval_request"
    assert approval_resp.json()["result"]["item"]["identity"]["approval_scope"] == "money"
    assert approval_resp.json()["result"]["item"]["identity"]["trace_id"] == "approval:manual-trace"


def test_inbox_update_approval_path_preserves_owner_note(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Approval update через web API должен писать owner decision trail, а не обычный status change."""
    monkeypatch.setenv("WEB_API_KEY", "secret")
    inbox = InboxService(state_path=tmp_path / "inbox.json")
    item = inbox.upsert_approval_request(
        title="Разрешить платный cloud route",
        body="Нужен production smoke.",
        request_key="paid-cloud-route",
        approval_scope="money",
    )["item"]
    monkeypatch.setattr("src.modules.web_app.inbox_service", inbox)
    client = _make_client()

    resp = client.post(
        "/api/inbox/update",
        json={
            "item_id": item["item_id"],
            "status": "approved",
            "actor": "owner-ui",
            "note": "approved after smoke",
        },
        headers={"X-Krab-Web-Key": "secret"},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["result"]["item"]["status"] == "approved"
    assert data["result"]["item"]["metadata"]["approval_decision"] == "approved"
    assert data["result"]["item"]["metadata"]["resolution_note"] == "approved after smoke"


def test_inbox_create_can_escalate_from_existing_source_item(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Create endpoint должен уметь создавать linked followup из входящего owner item."""
    monkeypatch.setenv("WEB_API_KEY", "secret")
    inbox = InboxService(state_path=tmp_path / "inbox.json")
    source_item = inbox.upsert_incoming_owner_request(
        chat_id="-100777",
        message_id="41",
        text="Вынеси это в owner-task",
        sender_username="owner",
        chat_type="group",
        is_reply_to_me=True,
    )["item"]
    monkeypatch.setattr("src.modules.web_app.inbox_service", inbox)
    client = _make_client()

    resp = client.post(
        "/api/inbox/create",
        json={
            "kind": "owner_task",
            "source_item_id": source_item["item_id"],
            "title": "Разобрать кейс",
            "body": "Нужен linked followup task.",
            "task_key": "linked-followup",
        },
        headers={"X-Krab-Web-Key": "secret"},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["result"]["item"]["metadata"]["source_item_id"] == source_item["item_id"]
    assert data["result"]["item"]["identity"]["trace_id"] == source_item["identity"]["trace_id"]


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
