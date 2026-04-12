# -*- coding: utf-8 -*-
"""
Тесты ACL/identity API endpoint'ов web-панели Krab.

Покрываем:
  GET  /api/userbot/acl/status      — read-only runtime ACL userbot
  POST /api/userbot/acl/update      — обновление ACL через owner web-key
  GET  /api/policy                  — runtime-политика AI + policy_matrix
  GET  /api/policy/matrix           — unified policy matrix (owner/full/partial/guest)
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from src.modules.web_app import WebApp

# ---------------------------------------------------------------------------
# Заглушки
# ---------------------------------------------------------------------------


class _FakeOpenClaw:
    """Минимальный OpenClaw клиент без внешних вызовов."""

    def get_last_runtime_route(self) -> dict:
        return {
            "channel": "cloud",
            "provider": "google",
            "model": "google/gemini-test",
            "status": "ok",
            "error_code": None,
        }

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
    """Минимальный userbot-stub."""

    def get_translator_runtime_profile(self) -> dict:
        return {"language_pair": "es-ru", "enabled": True}

    def get_translator_session_state(self) -> dict:
        return {"session_status": "idle", "active_chats": [], "stats": {}}

    def get_voice_runtime_profile(self) -> dict:
        return {"tts_enabled": False}

    def get_runtime_state(self) -> dict:
        return {"startup_state": "running", "client_connected": True}


# ---------------------------------------------------------------------------
# Фабрика WebApp
# ---------------------------------------------------------------------------

# Стандартный runtime ACL state для моков
_FAKE_ACL_STATE = {
    "owner": ["owner_user"],
    "full": ["full_user"],
    "partial": ["partial_user"],
}


def _make_app() -> WebApp:
    """Создаёт WebApp с полным набором заглушек."""
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
    return WebApp(deps, port=18091, host="127.0.0.1")


def _client() -> TestClient:
    return TestClient(_make_app().app)


# ---------------------------------------------------------------------------
# GET /api/userbot/acl/status
# ---------------------------------------------------------------------------


def test_acl_status_ok() -> None:
    """GET /api/userbot/acl/status возвращает ok=True."""
    with (
        patch("src.modules.web_app.load_acl_runtime_state", return_value=_FAKE_ACL_STATE),
        patch("src.modules.web_app.get_effective_owner_label", return_value="owner_user"),
        patch("src.modules.web_app.get_effective_owner_subjects", return_value=["owner_user"]),
    ):
        resp = _client().get("/api/userbot/acl/status")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_acl_status_has_acl_key() -> None:
    """Ответ /api/userbot/acl/status содержит поле acl."""
    with (
        patch("src.modules.web_app.load_acl_runtime_state", return_value=_FAKE_ACL_STATE),
        patch("src.modules.web_app.get_effective_owner_label", return_value="owner_user"),
        patch("src.modules.web_app.get_effective_owner_subjects", return_value=["owner_user"]),
    ):
        data = _client().get("/api/userbot/acl/status").json()
    assert "acl" in data


def test_acl_status_acl_fields() -> None:
    """acl содержит поля owner_username, owner_subjects, state, partial_commands."""
    with (
        patch("src.modules.web_app.load_acl_runtime_state", return_value=_FAKE_ACL_STATE),
        patch("src.modules.web_app.get_effective_owner_label", return_value="owner_user"),
        patch("src.modules.web_app.get_effective_owner_subjects", return_value=["owner_user"]),
    ):
        acl = _client().get("/api/userbot/acl/status").json()["acl"]
    for field in ("owner_username", "owner_subjects", "state", "partial_commands"):
        assert field in acl, f"отсутствует поле: {field}"


def test_acl_status_state_levels() -> None:
    """acl.state содержит ключи owner/full/partial."""
    with (
        patch("src.modules.web_app.load_acl_runtime_state", return_value=_FAKE_ACL_STATE),
        patch("src.modules.web_app.get_effective_owner_label", return_value="owner_user"),
        patch("src.modules.web_app.get_effective_owner_subjects", return_value=["owner_user"]),
    ):
        state = _client().get("/api/userbot/acl/status").json()["acl"]["state"]
    for level in ("owner", "full", "partial"):
        assert level in state, f"уровень отсутствует: {level}"


def test_acl_status_partial_commands_is_list() -> None:
    """partial_commands — список строк."""
    with (
        patch("src.modules.web_app.load_acl_runtime_state", return_value=_FAKE_ACL_STATE),
        patch("src.modules.web_app.get_effective_owner_label", return_value="owner_user"),
        patch("src.modules.web_app.get_effective_owner_subjects", return_value=["owner_user"]),
    ):
        cmds = _client().get("/api/userbot/acl/status").json()["acl"]["partial_commands"]
    assert isinstance(cmds, list)


# ---------------------------------------------------------------------------
# POST /api/userbot/acl/update
# ---------------------------------------------------------------------------


def test_acl_update_no_auth_rejected() -> None:
    """POST /api/userbot/acl/update с заданным WEB_API_KEY и неверным токеном — 403."""
    # Если WEB_API_KEY не задан, _assert_write_access пропускает любого;
    # имитируем среду с настроенным ключом через мок _web_api_key.
    with patch("src.modules.web_app.WebApp._web_api_key", return_value="secret123"):
        resp = _client().post(
            "/api/userbot/acl/update",
            json={"action": "grant", "level": "full", "subject": "test_user"},
        )
    assert resp.status_code == 403


def test_acl_update_invalid_action_returns_400() -> None:
    """POST с невалидным action возвращает 400."""
    with patch("src.modules.web_app.WebApp._assert_write_access"):
        resp = _client().post(
            "/api/userbot/acl/update",
            json={"action": "invalid", "level": "full", "subject": "test_user"},
        )
    assert resp.status_code == 400


def test_acl_update_grant_ok() -> None:
    """POST с корректным grant возвращает ok=True и changed."""
    fake_result = {
        "changed": True,
        "level": "full",
        "subject": "new_user",
        "path": "/tmp/test_acl.json",
        "state": {"owner": [], "full": ["new_user"], "partial": []},
    }
    with (
        patch("src.modules.web_app.WebApp._assert_write_access"),
        patch("src.modules.web_app.update_acl_subject", return_value=fake_result),
    ):
        resp = _client().post(
            "/api/userbot/acl/update",
            json={"action": "grant", "level": "full", "subject": "new_user"},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["acl"]["changed"] is True


def test_acl_update_revoke_ok() -> None:
    """POST с action=revoke возвращает ok=True."""
    fake_result = {
        "changed": True,
        "level": "full",
        "subject": "old_user",
        "path": "/tmp/test_acl.json",
        "state": {"owner": [], "full": [], "partial": []},
    }
    with (
        patch("src.modules.web_app.WebApp._assert_write_access"),
        patch("src.modules.web_app.update_acl_subject", return_value=fake_result),
    ):
        resp = _client().post(
            "/api/userbot/acl/update",
            json={"action": "revoke", "level": "full", "subject": "old_user"},
        )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_acl_update_invalid_level_returns_400() -> None:
    """POST с невалидным level возвращает 400 (ValueError от update_acl_subject)."""
    with (
        patch("src.modules.web_app.WebApp._assert_write_access"),
        patch(
            "src.modules.web_app.update_acl_subject",
            side_effect=ValueError("unsupported_acl_level:superadmin"),
        ),
    ):
        resp = _client().post(
            "/api/userbot/acl/update",
            json={"action": "grant", "level": "superadmin", "subject": "user"},
        )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# GET /api/policy
# ---------------------------------------------------------------------------


def test_policy_no_ai_runtime_returns_error_flag() -> None:
    """GET /api/policy без ai_runtime возвращает ok=False."""
    with patch("src.modules.web_app.load_acl_runtime_state", return_value=_FAKE_ACL_STATE):
        resp = _client().get("/api/policy")
    assert resp.status_code == 200
    assert resp.json()["ok"] is False


def test_policy_no_ai_runtime_has_policy_matrix() -> None:
    """GET /api/policy без ai_runtime всё равно содержит policy_matrix."""
    with patch("src.modules.web_app.load_acl_runtime_state", return_value=_FAKE_ACL_STATE):
        data = _client().get("/api/policy").json()
    assert "policy_matrix" in data
