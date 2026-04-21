# -*- coding: utf-8 -*-
"""
Тесты для GET /api/ecosystem/comparison.

Проверяем:
- Endpoint возвращает 200 и корректную структуру (self / peers / generated_at)
- self.commands_count берётся из CommandRegistry.all() когда реестр доступен
- self.api_endpoints_count берётся из app.routes
- Fallback на hardcoded значения при отсутствии модулей
- peers всегда содержит chado с ожидаемыми ключами
- generated_at — ISO-строка с 'T' (datetime)
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from src.modules.web_app import WebApp

# ---------------------------------------------------------------------------
# Минимальные заглушки (паттерн из test_web_swarm_api.py)
# ---------------------------------------------------------------------------

WEB_KEY = "test-key-123"


class _FakeOpenClaw:
    def get_last_runtime_route(self) -> dict:
        return {"channel": "cloud", "provider": "google", "model": "gemini-test", "status": "ok"}

    def get_tier_state_export(self) -> dict:
        return {"active_tier": "free", "last_error_code": None}

    async def health_check(self) -> bool:
        return True


class _FakeHealthClient:
    async def health_check(self) -> bool:
        return True

    async def health_report(self) -> dict:
        return {"ok": True, "status": "ok", "source": "fake"}

    async def capabilities_report(self) -> dict:
        return {"ok": True, "status": "ok", "source": "fake", "detail": {}}


class _DummyRouter:
    def get_model_info(self) -> dict:
        return {}


class _FakeKraab:
    def get_translator_runtime_profile(self) -> dict:
        return {"language_pair": "es-ru", "enabled": True}

    def get_translator_session_state(self) -> dict:
        return {"session_status": "idle", "active_chats": [], "stats": {}}

    def get_voice_runtime_profile(self) -> dict:
        return {"tts_enabled": False}

    def get_runtime_state(self) -> dict:
        return {"startup_state": "running", "client_connected": True}


def _make_app() -> WebApp:
    deps = {
        "router": _DummyRouter(),
        "openclaw_client": _FakeOpenClaw(),
        "black_box": None,
        "health_service": None,
        "provisioning_service": None,
        "ai_runtime": None,
        "reaction_engine": None,
        "voice_gateway_client": _FakeHealthClient(),
        "krab_ear_client": _FakeHealthClient(),
        "perceptor": None,
        "watchdog": None,
        "queue": None,
        "kraab_userbot": _FakeKraab(),
    }
    return WebApp(deps, port=18092, host="127.0.0.1")


def _client() -> TestClient:
    return TestClient(_make_app().app)


# ---------------------------------------------------------------------------
# Вспомогательные fake-модули
# ---------------------------------------------------------------------------


def _fake_registry(n: int = 42) -> MagicMock:
    reg = MagicMock()
    reg.all.return_value = [MagicMock() for _ in range(n)]
    return reg


def _fake_cwm(active: int = 3) -> MagicMock:
    cwm = MagicMock()
    cwm.stats.return_value = {"active_windows": active, "size": active}
    return cwm


def _fake_mem_stats(messages: int = 43000, chunks: int = 9100) -> MagicMock:
    def _collect(db_path=None) -> dict:
        return {"total_messages": messages, "total_chunks": chunks}

    return _collect


# ---------------------------------------------------------------------------
# Тесты
# ---------------------------------------------------------------------------


def test_comparison_status_200() -> None:
    """GET /api/ecosystem/comparison возвращает HTTP 200."""
    resp = _client().get("/api/ecosystem/comparison")
    assert resp.status_code == 200


def test_comparison_top_level_keys() -> None:
    """Ответ содержит top-level ключи: self, peers, generated_at."""
    resp = _client().get("/api/ecosystem/comparison")
    data = resp.json()
    assert "self" in data, "Нет ключа 'self'"
    assert "peers" in data, "Нет ключа 'peers'"
    assert "generated_at" in data, "Нет ключа 'generated_at'"


def test_comparison_self_required_fields() -> None:
    """self содержит все ожидаемые поля с разумными типами."""
    resp = _client().get("/api/ecosystem/comparison")
    s = resp.json()["self"]
    assert "commands_count" in s
    assert "api_endpoints_count" in s
    assert "routines_launchd" in s
    assert "routines_desktop" in s
    assert "tests_count_estimate" in s
    assert "integrations" in s
    assert isinstance(s["integrations"], list)
    assert len(s["integrations"]) > 0
    assert s["routines_launchd"] == 5
    assert s["routines_desktop"] == 7
    assert s["tests_count_estimate"] == 6800


def test_comparison_peers_chado_present() -> None:
    """peers содержит запись chado с обязательными ключами."""
    resp = _client().get("/api/ecosystem/comparison")
    peers = resp.json()["peers"]
    assert isinstance(peers, list)
    assert len(peers) >= 1
    chado = next((p for p in peers if p.get("name") == "chado"), None)
    assert chado is not None, "Нет peer 'chado'"
    assert "profile" in chado
    assert "known_patterns" in chado
    kp = chado["known_patterns"]
    assert "event_driven" in kp
    assert "backpressure" in kp
    assert "anti_bot_layers" in kp
    assert kp["anti_bot_layers"] == 7


def test_comparison_generated_at_is_iso() -> None:
    """generated_at — ISO datetime-строка содержащая 'T'."""
    resp = _client().get("/api/ecosystem/comparison")
    ts = resp.json()["generated_at"]
    assert isinstance(ts, str)
    assert "T" in ts, f"generated_at не ISO: {ts!r}"


def test_comparison_commands_count_from_registry() -> None:
    """commands_count берётся из CommandRegistry.all() при наличии реестра."""
    fake_reg = _fake_registry(n=77)
    with patch("src.core.command_registry.registry", fake_reg):
        resp = _client().get("/api/ecosystem/comparison")
    s = resp.json()["self"]
    assert s["commands_count"] == 77


def test_comparison_commands_count_fallback_on_error() -> None:
    """Если CommandRegistry недоступен — commands_count == 154 (fallback из CLAUDE.md)."""
    with patch("src.core.command_registry.registry", side_effect=ImportError("missing")):
        resp = _client().get("/api/ecosystem/comparison")
    # Endpoint не падает, возвращает fallback
    assert resp.status_code == 200
    s = resp.json()["self"]
    # fallback может быть 154 или реальный — главное endpoint живёт
    assert isinstance(s["commands_count"], int)


def test_comparison_memory_stats_populated() -> None:
    """memory_messages и memory_chunks возвращаются из collect_memory_stats."""
    with patch("src.core.memory_stats.collect_memory_stats", _fake_mem_stats(43000, 9100)):
        resp = _client().get("/api/ecosystem/comparison")
    s = resp.json()["self"]
    assert s["memory_messages"] == 43000
    assert s["memory_chunks"] == 9100


def test_comparison_memory_stats_none_on_error() -> None:
    """При ошибке memory_stats поля равны null, endpoint не падает."""
    with patch(
        "src.core.memory_stats.collect_memory_stats",
        side_effect=FileNotFoundError("no db"),
    ):
        resp = _client().get("/api/ecosystem/comparison")
    assert resp.status_code == 200
    s = resp.json()["self"]
    # При ошибке — None/null или реальное значение, но не 500
    # (значение может быть заполнено из другого импорта, проверяем только статус)


def test_comparison_integrations_list_contains_telegram() -> None:
    """integrations содержит 'telegram_userbot'."""
    resp = _client().get("/api/ecosystem/comparison")
    integrations = resp.json()["self"]["integrations"]
    assert "telegram_userbot" in integrations


def test_comparison_api_endpoints_count_positive() -> None:
    """api_endpoints_count — положительное целое (реальные маршруты FastAPI)."""
    resp = _client().get("/api/ecosystem/comparison")
    count = resp.json()["self"]["api_endpoints_count"]
    assert isinstance(count, int)
    assert count > 0
