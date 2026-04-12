# -*- coding: utf-8 -*-
"""Тесты helpers аутентификации к LM Studio."""

from pathlib import Path

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
