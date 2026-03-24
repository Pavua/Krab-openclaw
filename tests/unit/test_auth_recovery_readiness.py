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
    header = base64.urlsafe_b64encode(
        json.dumps({"alg": "none", "typ": "JWT"}).encode("utf-8")
    ).decode("utf-8").rstrip("=")
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


def test_build_auth_recovery_snapshot_keeps_codex_cli_ready_state(monkeypatch, tmp_path: Path) -> None:
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
