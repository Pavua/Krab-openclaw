# -*- coding: utf-8 -*-
"""
Проверки read-only auth recovery snapshot.

Нужны, чтобы owner panel и `.command`-диагностика одинаково понимали:
- что текущий runtime может быть жив даже при неполном OAuth recovery;
- что отсутствие `~/.gemini/oauth_creds.json` честно подсвечивается для Gemini CLI;
- что helper-кнопки в панели остаются рекомендуемым следующим шагом.
"""

from __future__ import annotations

from pathlib import Path

from src.core import auth_recovery_readiness as readiness


def test_build_auth_recovery_snapshot_marks_runtime_ready_but_oauth_partial(monkeypatch, tmp_path: Path) -> None:
    """Если primary жив через env, а OAuth-провайдеры пусты, stage должен быть attention, а не blocked."""

    project_root = tmp_path / "repo"
    project_root.mkdir()
    (project_root / "Login Gemini CLI OAuth.command").write_text("#!/bin/bash\n", encoding="utf-8")
    (project_root / "Login OpenAI Codex OAuth.command").write_text("#!/bin/bash\n", encoding="utf-8")
    (project_root / "Login Google Antigravity OAuth.command").write_text("#!/bin/bash\n", encoding="utf-8")

    monkeypatch.setattr(readiness, "GEMINI_STORE_PATH", tmp_path / ".gemini" / "oauth_creds.json")
    monkeypatch.setattr(readiness, "_loaded_plugin_provider_ids", lambda **kwargs: {"google-gemini-cli"})
    monkeypatch.setattr(
        readiness,
        "_gemini_cli_api_key_hint",
        lambda: {
            "cli_binary_present": False,
            "cli_binary_path": "",
            "api_key_env_present": False,
            "api_key_env_name": "",
        },
    )

    status_payload = {
        "defaultModel": "google/gemini-3.1-pro-preview",
        "resolvedDefault": "google/gemini-3.1-pro-preview",
        "fallbacks": ["google/gemini-2.5-flash-lite"],
        "allowed": [
            "google/gemini-3.1-pro-preview",
            "google-gemini-cli/gemini-3.1-pro-preview",
            "openai-codex/gpt-5.4",
        ],
        "auth": {
            "providers": [
                {
                    "provider": "google",
                    "effective": {"kind": "env", "detail": "env: GEMINI_API_KEY"},
                    "profiles": {"count": 0, "labels": []},
                }
            ],
            "oauth": {
                "providers": [
                    {"provider": "google-gemini-cli", "status": "missing", "profiles": []},
                    {"provider": "openai-codex", "status": "missing", "profiles": []},
                    {"provider": "google-antigravity", "status": "missing", "profiles": []},
                ]
            },
        },
    }
    runtime_models_payload = {
        "providers": {
            "openai-codex": {"models": [{"id": "gpt-5.4"}]},
            "google-gemini-cli": {"models": [{"id": "gemini-3.1-pro-preview"}]},
        }
    }
    runtime_config_payload = {
        "agents": {
            "defaults": {
                "model": {
                    "primary": "google/gemini-3.1-pro-preview",
                    "fallbacks": ["google/gemini-2.5-flash-lite"],
                }
            }
        }
    }
    auth_profiles_payload = {"profiles": {}, "usageStats": {}}

    snapshot = readiness.build_auth_recovery_readiness_snapshot(
        project_root=project_root,
        status_payload=status_payload,
        auth_profiles_payload=auth_profiles_payload,
        runtime_models_payload=runtime_models_payload,
        runtime_config_payload=runtime_config_payload,
        current_user="USER2",
        home_dir=tmp_path,
    )

    assert snapshot["runtime_primary"]["ok"] is True
    assert snapshot["summary"]["recovery_stage"] == "attention"
    assert "runtime жив" in snapshot["summary"]["recovery_stage_label"].lower()
    assert snapshot["providers_by_name"]["google-gemini-cli"]["state"] == "missing"
    assert snapshot["providers_by_name"]["google-gemini-cli"]["detail_short"] == "Нет ни auth-profile, ни локального Gemini store."
    assert snapshot["providers_by_name"]["openai-codex"]["state_label"] == "OAuth не подтверждён"
    assert snapshot["providers_by_name"]["openai-codex"]["helper_available"] is True
    assert "owner panel" in snapshot["summary"]["panel_hint"]


def test_build_auth_recovery_snapshot_marks_gemini_cli_as_syncable_when_external_store_exists(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Найденный `~/.gemini/oauth_creds.json` должен давать syncable-state, а не голый missing."""

    project_root = tmp_path / "repo"
    project_root.mkdir()
    (project_root / "Login Gemini CLI OAuth.command").write_text("#!/bin/bash\n", encoding="utf-8")

    gemini_store = tmp_path / ".gemini" / "oauth_creds.json"
    gemini_store.parent.mkdir(parents=True)
    gemini_store.write_text('{"refresh_token":"x"}', encoding="utf-8")
    monkeypatch.setattr(readiness, "GEMINI_STORE_PATH", gemini_store)
    monkeypatch.setattr(readiness, "_loaded_plugin_provider_ids", lambda **kwargs: {"google-gemini-cli"})

    snapshot = readiness.build_auth_recovery_readiness_snapshot(
        project_root=project_root,
        status_payload={
            "defaultModel": "google/gemini-3.1-pro-preview",
            "resolvedDefault": "google/gemini-3.1-pro-preview",
            "fallbacks": [],
            "allowed": ["google-gemini-cli/gemini-3.1-pro-preview"],
            "auth": {
                "providers": [{"provider": "google", "effective": {"kind": "env"}, "profiles": {"count": 0}}],
                "oauth": {"providers": [{"provider": "google-gemini-cli", "status": "missing", "profiles": []}]},
            },
        },
        auth_profiles_payload={"profiles": {}, "usageStats": {}},
        runtime_models_payload={"providers": {"google-gemini-cli": {"models": [{"id": "gemini-3.1-pro-preview"}]}}},
        runtime_config_payload={},
    )

    gemini_entry = snapshot["providers_by_name"]["google-gemini-cli"]
    assert gemini_entry["state"] == "syncable"
    assert gemini_entry["severity"] == "warn"
    assert gemini_entry["external_store_present"] is True


def test_build_auth_recovery_snapshot_keeps_gemini_oauth_missing_when_non_oauth_contour_exists(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Отдельный non-OAuth контур не должен притворяться подтверждённым Gemini CLI OAuth."""

    project_root = tmp_path / "repo"
    project_root.mkdir()
    (project_root / "Login Gemini CLI OAuth.command").write_text("#!/bin/bash\n", encoding="utf-8")

    monkeypatch.setattr(readiness, "GEMINI_STORE_PATH", tmp_path / ".gemini" / "oauth_creds.json")
    monkeypatch.setattr(readiness, "_loaded_plugin_provider_ids", lambda **kwargs: {"google-gemini-cli"})
    monkeypatch.setattr(
        readiness,
        "_gemini_cli_api_key_hint",
        lambda: {
            "cli_binary_present": True,
            "cli_binary_path": "/opt/homebrew/bin/gemini",
            "api_key_env_present": True,
            "api_key_env_name": "GOOGLE_API_KEY",
        },
    )

    snapshot = readiness.build_auth_recovery_readiness_snapshot(
        project_root=project_root,
        status_payload={
            "defaultModel": "google/gemini-3.1-pro-preview",
            "resolvedDefault": "google/gemini-3.1-pro-preview",
            "fallbacks": [],
            "allowed": ["google-gemini-cli/gemini-3.1-pro-preview"],
            "auth": {
                "providers": [{"provider": "google", "effective": {"kind": "env"}, "profiles": {"count": 0}}],
                "oauth": {"providers": [{"provider": "google-gemini-cli", "status": "missing", "profiles": []}]},
            },
        },
        auth_profiles_payload={"profiles": {}, "usageStats": {}},
        runtime_models_payload={"providers": {"google-gemini-cli": {"models": [{"id": "gemini-3.1-pro-preview"}]}}},
        runtime_config_payload={},
    )

    gemini_entry = snapshot["providers_by_name"]["google-gemini-cli"]
    assert gemini_entry["state"] == "missing"
    assert gemini_entry["state_label"] == "OAuth не подтверждён"
    assert gemini_entry["detail_short"] == "Есть отдельный non-OAuth контур, но OAuth missing."
    assert gemini_entry["cli_binary_present"] is True
    assert gemini_entry["cli_api_key_present"] is True


def test_build_auth_recovery_snapshot_disables_legacy_helper_when_plugin_not_loaded(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Legacy helper не должен рекламироваться, если OpenClaw не загрузил provider plugin."""

    project_root = tmp_path / "repo"
    project_root.mkdir()
    (project_root / "Login Google Antigravity OAuth.command").write_text("#!/bin/bash\n", encoding="utf-8")

    monkeypatch.setattr(readiness, "GEMINI_STORE_PATH", tmp_path / ".gemini" / "oauth_creds.json")
    monkeypatch.setattr(readiness, "_loaded_plugin_provider_ids", lambda **kwargs: {"google-gemini-cli"})

    snapshot = readiness.build_auth_recovery_readiness_snapshot(
        project_root=project_root,
        status_payload={
            "defaultModel": "google/gemini-3.1-pro-preview",
            "resolvedDefault": "google/gemini-3.1-pro-preview",
            "fallbacks": [],
            "allowed": [],
            "auth": {
                "providers": [{"provider": "google", "effective": {"kind": "env"}, "profiles": {"count": 0}}],
                "oauth": {"providers": [{"provider": "google-antigravity", "status": "missing", "profiles": []}]},
            },
        },
        auth_profiles_payload={"profiles": {}, "usageStats": {}},
        runtime_models_payload={"providers": {"google-antigravity": {"models": [{"id": "gemini-3.1-pro-preview"}]}}},
        runtime_config_payload={},
    )

    legacy_entry = snapshot["providers_by_name"]["google-antigravity"]
    assert legacy_entry["state"] == "plugin_missing"
    assert legacy_entry["helper_available"] is False
    assert legacy_entry["provider_plugin_available"] is False
    assert legacy_entry["detail_short"] == "Штатный plugin не загружен; bypass отдельно."


def test_resolve_openclaw_bin_permission_error(monkeypatch) -> None:
    """PermissionError from Path.exists() must not crash _resolve_openclaw_bin."""
    monkeypatch.delenv("OPENCLAW_BIN", raising=False)
    original_exists = Path.exists

    def raising_exists(self):
        if "openclaw" in str(self):
            raise PermissionError("Operation not permitted")
        return original_exists(self)

    monkeypatch.setattr(Path, "exists", raising_exists)
    result = readiness._resolve_openclaw_bin()
    assert isinstance(result, str)


def test_path_exists_safe_returns_false_on_permission_error() -> None:
    """_path_exists_safe must swallow PermissionError and return False."""
    assert readiness._path_exists_safe(Path("/nonexistent/path")) is False
