# -*- coding: utf-8 -*-
"""
Тесты read-only scope-диагностики auth recovery.

Зачем:
- не делаем жёстких выводов о работоспособности провайдера по одному JWT;
- но обязаны уметь честно извлекать наблюдаемые scopes из локального OAuth-профиля.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

from src.core import auth_recovery_readiness
from src.core.auth_recovery_readiness import (
    build_auth_recovery_readiness_snapshot,
    provider_oauth_scope_truth,
)


def _build_fake_jwt(payload: dict) -> str:
    """Собирает минимальный JWT для тестовой scope-диагностики."""
    header = (
        base64.urlsafe_b64encode(json.dumps({"alg": "none", "typ": "JWT"}).encode("utf-8"))
        .decode("utf-8")
        .rstrip("=")
    )
    body = base64.urlsafe_b64encode(json.dumps(payload).encode("utf-8")).decode("utf-8").rstrip("=")
    return f"{header}.{body}.signature"


def test_provider_oauth_scope_truth_extracts_scopes_from_openai_codex_profile() -> None:
    """Scope helper должен корректно вытаскивать scopes из JWT payload."""

    auth_profiles_payload = {
        "profiles": {
            "openai-codex:default": {
                "provider": "openai-codex",
                "access": _build_fake_jwt(
                    {"scope": ["openid", "profile", "email", "offline_access"]}
                ),
            }
        }
    }

    truth = provider_oauth_scope_truth("openai-codex", auth_profiles_payload)

    assert truth["scope_truth_available"] is True
    assert truth["profiles"] == ["openai-codex:default"]
    assert truth["has_model_request"] is False
    assert truth["scopes"] == ["email", "offline_access", "openid", "profile"]


def test_build_auth_recovery_snapshot_keeps_codex_cli_ready_state(
    monkeypatch, tmp_path: Path
) -> None:
    """Готовый Codex CLI не должен деградировать в `Recovery блокирован` только из-за usage-role."""

    monkeypatch.setattr(
        auth_recovery_readiness,
        "_codex_cli_hint",
        lambda: {
            "cli_binary_present": True,
            "binary_path": "/opt/homebrew/bin/codex",
            "login_ready": True,
            "status_text": "Logged in using ChatGPT",
        },
    )
    monkeypatch.setattr(
        auth_recovery_readiness,
        "_loaded_plugin_provider_ids",
        lambda project_root: set(),
    )

    snapshot = build_auth_recovery_readiness_snapshot(
        project_root=tmp_path,
        status_payload={},
        auth_profiles_payload={"profiles": {}, "usageStats": {}},
        runtime_models_payload={"providers": {}},
        runtime_config_payload={
            "agents": {
                "defaults": {
                    "model": {
                        "primary": "codex-cli/gpt-5.4",
                        "fallbacks": [],
                    }
                }
            }
        },
    )

    entry = snapshot["providers_by_name"]["codex-cli"]
    assert entry["state"] == "ready"
    assert entry["severity"] == "ok"
    assert entry["state_label"] == "CLI OK"
    assert entry["primary_policy"] == "personal-primary"
    assert entry["login_state"] == "ready"
    assert entry["cost_tier"] == "subscription"


# ---------------------------------------------------------------------------
# _decode_jwt_payload
# ---------------------------------------------------------------------------


def test_decode_jwt_payload_valid_token() -> None:
    """Корректный JWT: payload декодируется без ошибок."""
    from src.core.auth_recovery_readiness import _decode_jwt_payload

    payload = {"sub": "user123", "scope": ["openid", "profile"]}
    token = _build_fake_jwt(payload)
    result = _decode_jwt_payload(token)
    assert result["sub"] == "user123"
    assert result["scope"] == ["openid", "profile"]


def test_decode_jwt_payload_empty_string_returns_empty_dict() -> None:
    """Пустая строка → пустой dict без исключения."""
    from src.core.auth_recovery_readiness import _decode_jwt_payload

    assert _decode_jwt_payload("") == {}


def test_decode_jwt_payload_garbage_returns_empty_dict() -> None:
    """Произвольный мусор → пустой dict без исключения."""
    from src.core.auth_recovery_readiness import _decode_jwt_payload

    assert _decode_jwt_payload("not.a.valid.jwt.at.all!!!!") == {}


def test_decode_jwt_payload_missing_dots_returns_empty_dict() -> None:
    """Строка без двух точек (неполный JWT) → пустой dict."""
    from src.core.auth_recovery_readiness import _decode_jwt_payload

    assert _decode_jwt_payload("header.payload") == {}


# ---------------------------------------------------------------------------
# provider_oauth_scope_truth
# ---------------------------------------------------------------------------


def test_provider_oauth_scope_truth_no_profiles_returns_empty() -> None:
    """Провайдер без профилей — scope_truth_available=False, пустые scopes."""
    truth = provider_oauth_scope_truth("openai-codex", {"profiles": {}})
    assert truth["scope_truth_available"] is False
    assert truth["scopes"] == []
    assert truth["profiles"] == []
    assert truth["has_model_request"] is False


def test_provider_oauth_scope_truth_has_model_request_scope() -> None:
    """Если JWT содержит 'model.request' — флаг has_model_request=True."""
    auth_profiles_payload = {
        "profiles": {
            "openai-codex:main": {
                "provider": "openai-codex",
                "access": _build_fake_jwt({"scope": ["openid", "model.request"]}),
            }
        }
    }
    truth = provider_oauth_scope_truth("openai-codex", auth_profiles_payload)
    assert truth["has_model_request"] is True
    assert "model.request" in truth["scopes"]


def test_provider_oauth_scope_truth_scope_as_space_separated_string() -> None:
    """scope в JWT может быть пробело-разделённой строкой — должно работать."""
    auth_profiles_payload = {
        "profiles": {
            "openai-codex:def": {
                "provider": "openai-codex",
                "access": _build_fake_jwt({"scope": "openid profile email"}),
            }
        }
    }
    truth = provider_oauth_scope_truth("openai-codex", auth_profiles_payload)
    assert "openid" in truth["scopes"]
    assert "profile" in truth["scopes"]
    assert "email" in truth["scopes"]


def test_provider_oauth_scope_truth_merges_multiple_profiles() -> None:
    """Scopes из нескольких профилей одного провайдера объединяются."""
    auth_profiles_payload = {
        "profiles": {
            "openai-codex:a": {
                "provider": "openai-codex",
                "access": _build_fake_jwt({"scope": ["openid"]}),
            },
            "openai-codex:b": {
                "provider": "openai-codex",
                "access": _build_fake_jwt({"scope": ["profile", "model.request"]}),
            },
        }
    }
    truth = provider_oauth_scope_truth("openai-codex", auth_profiles_payload)
    assert truth["scope_truth_available"] is True
    assert len(truth["profiles"]) == 2
    assert "openid" in truth["scopes"]
    assert "model.request" in truth["scopes"]


# ---------------------------------------------------------------------------
# _gemini_cli_api_key_hint
# ---------------------------------------------------------------------------


def test_gemini_cli_api_key_hint_no_env(monkeypatch) -> None:
    """Без переменных окружения — api_key_env_present=False."""
    from src.core.auth_recovery_readiness import _gemini_cli_api_key_hint

    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    result = _gemini_cli_api_key_hint()
    assert result["api_key_env_present"] is False
    assert result["api_key_env_name"] == ""


def test_gemini_cli_api_key_hint_with_env(monkeypatch) -> None:
    """При наличии GEMINI_API_KEY — флаг api_key_env_present=True."""
    from src.core.auth_recovery_readiness import _gemini_cli_api_key_hint

    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-value")
    result = _gemini_cli_api_key_hint()
    assert result["api_key_env_present"] is True
    assert result["api_key_env_name"] == "GEMINI_API_KEY"


# ---------------------------------------------------------------------------
# build_auth_recovery_readiness_snapshot — структура ответа
# ---------------------------------------------------------------------------


def test_build_auth_recovery_snapshot_top_level_keys(monkeypatch, tmp_path: Path) -> None:
    """Снэпшот содержит все обязательные верхнеуровневые ключи."""
    monkeypatch.setattr(
        auth_recovery_readiness,
        "_codex_cli_hint",
        lambda: {
            "cli_binary_present": False,
            "cli_binary_path": "",
            "login_ready": False,
            "status_text": "",
        },
    )
    monkeypatch.setattr(
        auth_recovery_readiness, "_loaded_plugin_provider_ids", lambda project_root: set()
    )

    snapshot = build_auth_recovery_readiness_snapshot(
        project_root=tmp_path,
        status_payload={},
        auth_profiles_payload={"profiles": {}},
        runtime_models_payload={"providers": {}},
        runtime_config_payload={},
    )
    for key in (
        "ok",
        "generated_at_utc",
        "project_root",
        "current_account",
        "runtime_primary",
        "summary",
        "providers",
        "providers_by_name",
    ):
        assert key in snapshot, f"Missing key: {key}"
    assert snapshot["ok"] is True


def test_build_auth_recovery_snapshot_summary_stage_blocked_when_no_runtime(
    monkeypatch, tmp_path: Path
) -> None:
    """Если runtime primary не определён, stage='blocked'."""
    monkeypatch.setattr(
        auth_recovery_readiness,
        "_codex_cli_hint",
        lambda: {
            "cli_binary_present": False,
            "cli_binary_path": "",
            "login_ready": False,
            "status_text": "",
        },
    )
    monkeypatch.setattr(
        auth_recovery_readiness, "_loaded_plugin_provider_ids", lambda project_root: set()
    )

    snapshot = build_auth_recovery_readiness_snapshot(
        project_root=tmp_path,
        status_payload={},
        auth_profiles_payload={"profiles": {}},
        runtime_models_payload={"providers": {}},
        runtime_config_payload={},
    )
    assert snapshot["summary"]["recovery_stage"] == "blocked"


def test_build_auth_recovery_snapshot_stage_ready_when_runtime_ok(
    monkeypatch, tmp_path: Path
) -> None:
    """Если runtime primary auth подтверждён и нет провалов — stage='ready'."""
    monkeypatch.setattr(
        auth_recovery_readiness,
        "_codex_cli_hint",
        lambda: {
            "cli_binary_present": False,
            "cli_binary_path": "",
            "login_ready": False,
            "status_text": "",
        },
    )
    monkeypatch.setattr(
        auth_recovery_readiness, "_loaded_plugin_provider_ids", lambda project_root: set()
    )

    status_payload = {
        "resolvedDefault": "google-gemini-cli/gemini-2.5-pro",
        "auth": {
            "providers": [
                {
                    "provider": "google-gemini-cli",
                    "effective": {"kind": "oauth", "detail": "ok"},
                }
            ],
            "oauth": {"providers": [{"provider": "google-gemini-cli", "status": "ok"}]},
        },
    }
    auth_profiles_payload = {
        "profiles": {
            "google-gemini-cli:default": {
                "provider": "google-gemini-cli",
                "access": _build_fake_jwt({"scope": ["openid"]}),
            }
        },
        "usageStats": {},
    }

    snapshot = build_auth_recovery_readiness_snapshot(
        project_root=tmp_path,
        status_payload=status_payload,
        auth_profiles_payload=auth_profiles_payload,
        runtime_models_payload={"providers": {}},
        runtime_config_payload={},
    )
    assert snapshot["runtime_primary"]["ok"] is True
    assert snapshot["summary"]["runtime_ready"] is True


# ---------------------------------------------------------------------------
# _provider_recovery_entry — состояния провайдеров
# ---------------------------------------------------------------------------


def test_provider_recovery_entry_openai_codex_missing_profile(monkeypatch, tmp_path: Path) -> None:
    """openai-codex без профилей → state='missing', severity='warn' или 'bad'."""
    monkeypatch.setattr(auth_recovery_readiness, "_codex_cli_hint", lambda: {})
    monkeypatch.setattr(
        auth_recovery_readiness, "_loaded_plugin_provider_ids", lambda project_root: set()
    )

    from src.core.auth_recovery_readiness import _provider_recovery_entry

    entry = _provider_recovery_entry(
        "openai-codex",
        project_root=tmp_path,
        status_payload={},
        providers_map={},
        oauth_map={},
        auth_profiles_payload={"profiles": {}},
        runtime_models_payload={"providers": {}},
        runtime_config_payload={},
        loaded_plugin_providers=set(),
    )
    assert entry["provider"] == "openai-codex"
    assert entry["state"] == "missing"
    assert entry["severity"] in ("warn", "bad")
    assert entry["scope_truth_available"] is False


def test_provider_recovery_entry_gemini_cli_syncable_when_store_present(
    monkeypatch, tmp_path: Path
) -> None:
    """google-gemini-cli: если внешний store присутствует — state='syncable'."""
    # Создаём заглушку GEMINI_STORE_PATH в tmp_path и патчим путь
    fake_store = tmp_path / "oauth_creds.json"
    fake_store.write_text('{"token": "x"}')

    monkeypatch.setattr(auth_recovery_readiness, "GEMINI_STORE_PATH", fake_store)
    monkeypatch.setattr(
        auth_recovery_readiness,
        "_gemini_cli_api_key_hint",
        lambda: {
            "cli_binary_present": False,
            "cli_binary_path": "",
            "api_key_env_present": False,
            "api_key_env_name": "",
        },
    )

    from src.core.auth_recovery_readiness import _provider_recovery_entry

    entry = _provider_recovery_entry(
        "google-gemini-cli",
        project_root=tmp_path,
        status_payload={},
        providers_map={},
        oauth_map={},
        auth_profiles_payload={"profiles": {}},
        runtime_models_payload={"providers": {}},
        runtime_config_payload={},
        loaded_plugin_providers=set(),
    )
    assert entry["state"] == "syncable"
    assert entry["external_store_present"] is True
