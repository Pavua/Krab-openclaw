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

from src.core.auth_recovery_readiness import provider_oauth_scope_truth


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
