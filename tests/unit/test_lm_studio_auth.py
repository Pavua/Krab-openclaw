# -*- coding: utf-8 -*-
"""Тесты helpers аутентификации к LM Studio."""

from __future__ import annotations

import logging
from pathlib import Path

import httpx
import pytest

from src.core.lm_studio_auth import build_lm_studio_auth_headers, resolve_lm_studio_api_key


def test_resolve_lm_studio_api_key_prefers_canonical_name() -> None:
    env = {
        "LM_STUDIO_AUTH_TOKEN": "legacy-token",
        "LM_STUDIO_API_KEY": "canonical-token",
    }
    assert resolve_lm_studio_api_key(env) == "canonical-token"


def test_build_lm_studio_auth_headers_adds_bearer_and_x_api_key() -> None:
    headers = build_lm_studio_auth_headers(api_key="lm-secret", include_json_accept=True)
    assert headers["Accept"] == "application/json"
    assert headers["Authorization"] == "Bearer lm-secret"
    assert headers["x-api-key"] == "lm-secret"


def test_lm_studio_auth_strips_wrapping_quotes() -> None:
    env = {
        "LM_STUDIO_API_KEY": '"quoted-secret"',
    }
    assert resolve_lm_studio_api_key(env) == "quoted-secret"

    headers = build_lm_studio_auth_headers(api_key="'quoted-secret'")
    assert headers["Authorization"] == "Bearer quoted-secret"
    assert headers["x-api-key"] == "quoted-secret"


def test_resolve_lm_studio_api_key_reads_project_env_when_process_env_missing(
    monkeypatch, tmp_path: Path
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("LM_STUDIO_API_KEY=dotenv-token\n", encoding="utf-8")

    monkeypatch.delenv("LM_STUDIO_API_KEY", raising=False)
    monkeypatch.delenv("LM_STUDIO_AUTH_TOKEN", raising=False)
    monkeypatch.setattr(
        "src.core.lm_studio_auth._project_env_candidates",
        lambda: (env_file,),
    )

    assert resolve_lm_studio_api_key() == "dotenv-token"


def test_resolve_lm_studio_api_key_prefers_process_env_over_project_env(
    monkeypatch, tmp_path: Path
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("LM_STUDIO_API_KEY=dotenv-token\n", encoding="utf-8")

    monkeypatch.setenv("LM_STUDIO_API_KEY", "process-token")
    monkeypatch.setattr(
        "src.core.lm_studio_auth._project_env_candidates",
        lambda: (env_file,),
    )

    assert resolve_lm_studio_api_key() == "process-token"


def test_resolve_lm_studio_api_key_lm_api_token_wins() -> None:
    """`LM_API_TOKEN` имеет высший приоритет (session 32, имя из LM Studio error)."""
    env = {
        "LM_API_TOKEN": "lm-api-token",
        "LM_STUDIO_API_KEY": "canonical",
        "LM_STUDIO_AUTH_TOKEN": "legacy",
    }
    assert resolve_lm_studio_api_key(env) == "lm-api-token"


def test_build_headers_no_token_returns_empty() -> None:
    """Когда токена нет — Authorization не добавляется (локальный LM Studio без auth)."""
    headers = build_lm_studio_auth_headers(api_key="")
    assert "Authorization" not in headers
    assert "x-api-key" not in headers


@pytest.mark.asyncio
async def test_lm_studio_health_logs_auth_required_on_401(
    monkeypatch, capsys
) -> None:
    """401 без токена → структурный warning `lm_studio_auth_required`."""
    from src.core import local_health

    monkeypatch.delenv("LM_API_TOKEN", raising=False)
    monkeypatch.delenv("LM_STUDIO_API_KEY", raising=False)
    monkeypatch.delenv("LM_STUDIO_AUTH_TOKEN", raising=False)
    monkeypatch.setattr(
        "src.core.lm_studio_auth._project_env_candidates",
        lambda: (),
    )

    captured: list[tuple[str, dict]] = []

    def fake_warning(event: str, **kwargs) -> None:
        captured.append((event, kwargs))

    monkeypatch.setattr(local_health.logger, "warning", fake_warning)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "token required"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        ok = await local_health.is_lm_studio_available(
            "http://lmstudio.test", client=client, timeout=1.0
        )
    assert ok is False
    assert any(event == "lm_studio_auth_required" for event, _ in captured)
    # logging.WARNING used implicitly via structlog; placeholder to keep import live.
    assert logging.WARNING == 30
