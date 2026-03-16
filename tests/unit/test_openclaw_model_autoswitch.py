# -*- coding: utf-8 -*-
"""
Тесты autoswitch-скрипта OpenClaw.

Покрываем:
1) нормализацию local model key с обязательным provider-префиксом `lmstudio/`;
2) dry-run profile `local-first` без потери provider;
3) `toggle`-режим (cloud -> local -> cloud) и запись state-файла.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "openclaw_model_autoswitch.py"


def _load_script_module():
    spec = importlib.util.spec_from_file_location("openclaw_model_autoswitch", SCRIPT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _base_openclaw_payload() -> dict:
    return {
        "models": {
            "providers": {
                "lmstudio": {
                    "models": [
                        {"id": "zai-org/glm-4.6v-flash"},
                    ]
                }
            }
        },
        "agents": {
            "defaults": {
                "model": {
                    "primary": "google/gemini-2.5-flash",
                    "fallbacks": ["openai/gpt-4o-mini"],
                },
                "subagents": {"model": "google/gemini-2.5-flash"},
            },
            "list": [{"id": "main", "model": "google/gemini-2.5-flash"}],
        },
    }


def _base_runtime_models_payload() -> dict:
    return {
        "providers": {
            "google": {
                "models": [
                    {"id": "google/gemini-2.5-flash"},
                    {"id": "google/gemini-2.5-flash-lite"},
                ]
            },
            "lmstudio": {
                "models": [
                    {"id": "zai-org/glm-4.6v-flash"},
                ]
            },
            "openai": {
                "models": [
                    {"id": "gpt-4o-mini"},
                ]
            },
            "openai-codex": {
                "models": [
                    {"id": "gpt-4.5-preview"},
                ]
            },
            "google-antigravity": {
                "models": [
                    {"id": "gemini-3.1-pro-preview"},
                ]
            },
        }
    }


def _write_runtime_sidecars(
    tmp_path: Path,
    *,
    runtime_models_payload: dict | None = None,
    auth_profiles_payload: dict | None = None,
    gateway_log_text: str = "",
) -> tuple[Path, Path, Path]:
    models_path = tmp_path / "models.json"
    auth_profiles_path = tmp_path / "auth-profiles.json"
    gateway_log_path = tmp_path / "gateway.err.log"
    models_path.write_text(
        json.dumps(runtime_models_payload or _base_runtime_models_payload(), ensure_ascii=False),
        encoding="utf-8",
    )
    auth_profiles_path.write_text(
        json.dumps(
            auth_profiles_payload
            or {
                "profiles": {
                    "openai-codex:default": {"provider": "openai-codex"},
                    "google-antigravity:vscode-free": {"provider": "google-antigravity"},
                },
                "usageStats": {
                    "google-antigravity:vscode-free": {
                        "disabledReason": "auth_permanent",
                        "failureCounts": {"auth_permanent": 2},
                    },
                    "openai-codex:default": {
                        "failureCounts": {"model_not_found": 2},
                    },
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    gateway_log_path.write_text(gateway_log_text, encoding="utf-8")
    return models_path, auth_profiles_path, gateway_log_path


def _run_script(*args: str, env_overrides: dict[str, str] | None = None) -> dict:
    env = os.environ.copy()
    # Изолируем тесты от реального LOCAL_PREFERRED_MODEL из локального .env.
    env["LOCAL_PREFERRED_MODEL"] = ""
    if env_overrides:
        env.update(env_overrides)
    proc = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), *args],
        capture_output=True,
        text=True,
        check=True,
        env=env,
    )
    lines = [line.strip() for line in (proc.stdout or "").splitlines() if line.strip()]
    assert lines, "autoswitch script returned empty output"
    return json.loads(lines[-1])


def test_normalize_model_key_keeps_provider_for_cloud_and_adds_for_lmstudio():
    mod = _load_script_module()
    providers = {"lmstudio", "google", "openai"}

    assert mod._normalize_model_key("lmstudio", "zai-org/glm-4.6v-flash", providers) == "lmstudio/zai-org/glm-4.6v-flash"
    assert mod._normalize_model_key("lmstudio", "lmstudio/zai-org/glm-4.6v-flash", providers) == "lmstudio/zai-org/glm-4.6v-flash"
    assert mod._normalize_model_key("google", "google/gemini-2.5-flash", providers) == "google/gemini-2.5-flash"


def test_dry_run_local_first_preserves_lmstudio_provider(tmp_path):
    openclaw_path = tmp_path / "openclaw.json"
    agent_path = tmp_path / "agent.json"
    state_path = tmp_path / "state.json"
    models_path, auth_profiles_path, gateway_log_path = _write_runtime_sidecars(tmp_path)
    openclaw_path.write_text(json.dumps(_base_openclaw_payload(), ensure_ascii=False), encoding="utf-8")
    agent_path.write_text(json.dumps({"id": "main", "model": "google/gemini-2.5-flash"}, ensure_ascii=False), encoding="utf-8")

    payload = _run_script(
        "--dry-run",
        "--profile", "local-first",
        "--openclaw-json", str(openclaw_path),
        "--agent-json", str(agent_path),
        "--state-json", str(state_path),
        "--models-json", str(models_path),
        "--auth-profiles-json", str(auth_profiles_path),
        "--gateway-log", str(gateway_log_path),
    )

    assert payload["ok"] is True
    assert payload["details"]["effective_profile"] == "local-first"
    assert payload["details"]["primary_model"] == "lmstudio/zai-org/glm-4.6v-flash"


def test_toggle_switches_between_cloud_and_local_profiles(tmp_path):
    openclaw_path = tmp_path / "openclaw.json"
    agent_path = tmp_path / "agent.json"
    state_path = tmp_path / "state.json"
    models_path, auth_profiles_path, gateway_log_path = _write_runtime_sidecars(tmp_path)
    openclaw_path.write_text(json.dumps(_base_openclaw_payload(), ensure_ascii=False), encoding="utf-8")
    agent_path.write_text(json.dumps({"id": "main", "model": "google/gemini-2.5-flash"}, ensure_ascii=False), encoding="utf-8")

    first = _run_script(
        "--profile", "toggle",
        "--openclaw-json", str(openclaw_path),
        "--agent-json", str(agent_path),
        "--state-json", str(state_path),
        "--models-json", str(models_path),
        "--auth-profiles-json", str(auth_profiles_path),
        "--gateway-log", str(gateway_log_path),
    )
    assert first["ok"] is True
    assert first["details"]["effective_profile"] == "local-first"

    openclaw_after_first = json.loads(openclaw_path.read_text(encoding="utf-8"))
    assert openclaw_after_first["agents"]["defaults"]["model"]["primary"] == "lmstudio/zai-org/glm-4.6v-flash"

    second = _run_script(
        "--profile", "toggle",
        "--openclaw-json", str(openclaw_path),
        "--agent-json", str(agent_path),
        "--state-json", str(state_path),
        "--models-json", str(models_path),
        "--auth-profiles-json", str(auth_profiles_path),
        "--gateway-log", str(gateway_log_path),
    )
    assert second["ok"] is True
    assert second["details"]["effective_profile"] == "cloud-first"

    openclaw_after_second = json.loads(openclaw_path.read_text(encoding="utf-8"))
    assert openclaw_after_second["agents"]["defaults"]["model"]["primary"] == "google/gemini-2.5-flash"
    assert state_path.exists() is True


def test_local_first_uses_env_preferred_local_model(tmp_path):
    openclaw_path = tmp_path / "openclaw.json"
    agent_path = tmp_path / "agent.json"
    state_path = tmp_path / "state.json"
    models_path, auth_profiles_path, gateway_log_path = _write_runtime_sidecars(tmp_path)
    openclaw_path.write_text(json.dumps(_base_openclaw_payload(), ensure_ascii=False), encoding="utf-8")
    agent_path.write_text(json.dumps({"id": "main", "model": "google/gemini-2.5-flash"}, ensure_ascii=False), encoding="utf-8")

    payload = _run_script(
        "--dry-run",
        "--profile", "local-first",
        "--openclaw-json", str(openclaw_path),
        "--agent-json", str(agent_path),
        "--state-json", str(state_path),
        "--models-json", str(models_path),
        "--auth-profiles-json", str(auth_profiles_path),
        "--gateway-log", str(gateway_log_path),
        env_overrides={"LOCAL_PREFERRED_MODEL": "nvidia/nemotron-3-nano"},
    )

    assert payload["ok"] is True
    assert payload["details"]["primary_model"] == "lmstudio/nvidia/nemotron-3-nano"


def test_production_safe_skips_broken_primary_and_disabled_provider(tmp_path):
    openclaw_path = tmp_path / "openclaw.json"
    agent_path = tmp_path / "agent.json"
    state_path = tmp_path / "state.json"
    models_path, auth_profiles_path, gateway_log_path = _write_runtime_sidecars(
        tmp_path,
        gateway_log_text='2026-03-10 [model-fallback] Model "openai-codex/gpt-4.5-preview" not found.\n',
    )
    openclaw_path.write_text(json.dumps(_base_openclaw_payload(), ensure_ascii=False), encoding="utf-8")
    agent_path.write_text(json.dumps({"id": "main", "model": "openai-codex/gpt-4.5-preview"}, ensure_ascii=False), encoding="utf-8")

    payload = _run_script(
        "--dry-run",
        "--profile", "production-safe",
        "--openclaw-json", str(openclaw_path),
        "--agent-json", str(agent_path),
        "--state-json", str(state_path),
        "--models-json", str(models_path),
        "--auth-profiles-json", str(auth_profiles_path),
        "--gateway-log", str(gateway_log_path),
    )

    assert payload["ok"] is True
    assert payload["status"] == "OK"
    assert payload["details"]["primary_model"] == "google/gemini-2.5-flash"
    assert "google-antigravity/gemini-3.1-pro-preview" not in payload["details"]["fallbacks"]


def test_production_safe_skips_runtime_auth_failed_provider(tmp_path):
    openclaw_path = tmp_path / "openclaw.json"
    agent_path = tmp_path / "agent.json"
    state_path = tmp_path / "state.json"
    runtime_models_payload = _base_runtime_models_payload()
    runtime_models_payload["providers"]["openai-codex"]["models"].append({"id": "gpt-5.4"})
    runtime_models_payload["providers"]["google-gemini-cli"] = {
        "models": [
            {"id": "gemini-3.1-pro-preview"},
        ]
    }
    auth_profiles_payload = {
        "profiles": {
            "openai-codex:default": {"provider": "openai-codex"},
            "google-gemini-cli:default": {"provider": "google-gemini-cli"},
        },
        "usageStats": {},
    }
    gateway_log_text = (
        '2026-03-11 [diagnostic] lane task error: '
        'lane=session:agent:main:openai:abc123 durationMs=547 '
        'error="FailoverError: HTTP 401: You have insufficient permissions for this operation. '
        'Missing scopes: model.request."\n'
    )
    models_path, auth_profiles_path, gateway_log_path = _write_runtime_sidecars(
        tmp_path,
        runtime_models_payload=runtime_models_payload,
        auth_profiles_payload=auth_profiles_payload,
        gateway_log_text=gateway_log_text,
    )
    payload_openclaw = _base_openclaw_payload()
    payload_openclaw["agents"]["defaults"]["model"]["primary"] = "openai-codex/gpt-5.4"
    payload_openclaw["agents"]["defaults"]["model"]["fallbacks"] = [
        "google-gemini-cli/gemini-3.1-pro-preview",
        "google/gemini-2.5-flash",
    ]
    openclaw_path.write_text(json.dumps(payload_openclaw, ensure_ascii=False), encoding="utf-8")
    agent_path.write_text(json.dumps({"id": "main", "model": "openai-codex/gpt-5.4"}, ensure_ascii=False), encoding="utf-8")

    payload = _run_script(
        "--dry-run",
        "--profile", "production-safe",
        "--openclaw-json", str(openclaw_path),
        "--agent-json", str(agent_path),
        "--state-json", str(state_path),
        "--models-json", str(models_path),
        "--auth-profiles-json", str(auth_profiles_path),
        "--gateway-log", str(gateway_log_path),
    )

    assert payload["ok"] is True
    assert payload["details"]["primary_model"] == "google-gemini-cli/gemini-3.1-pro-preview"
    assert payload["details"]["runtime_auth_failed_providers"] == {
        "openai-codex": "runtime_missing_scope_model_request"
    }


def test_gpt54_canary_blocks_when_target_missing_from_runtime_registry(tmp_path):
    openclaw_path = tmp_path / "openclaw.json"
    agent_path = tmp_path / "agent.json"
    state_path = tmp_path / "state.json"
    models_path, auth_profiles_path, gateway_log_path = _write_runtime_sidecars(tmp_path)
    openclaw_path.write_text(json.dumps(_base_openclaw_payload(), ensure_ascii=False), encoding="utf-8")
    agent_path.write_text(json.dumps({"id": "main", "model": "google/gemini-2.5-flash"}, ensure_ascii=False), encoding="utf-8")

    payload = _run_script(
        "--dry-run",
        "--profile", "gpt54-canary",
        "--openclaw-json", str(openclaw_path),
        "--agent-json", str(agent_path),
        "--state-json", str(state_path),
        "--models-json", str(models_path),
        "--auth-profiles-json", str(auth_profiles_path),
        "--gateway-log", str(gateway_log_path),
        env_overrides={"OPENCLAW_TARGET_PRIMARY_MODEL": "openai-codex/gpt-5.4"},
    )

    assert payload["ok"] is False
    assert payload["status"] == "BLOCKED"
    assert payload["reason"] == "target_model_not_in_runtime_registry"
    assert payload["details"]["primary_model"] == ""


def test_gpt54_canary_promotes_target_when_registry_ready(tmp_path):
    openclaw_path = tmp_path / "openclaw.json"
    agent_path = tmp_path / "agent.json"
    state_path = tmp_path / "state.json"
    runtime_models_payload = _base_runtime_models_payload()
    runtime_models_payload["providers"]["openai-codex"]["models"].append({"id": "gpt-5.4"})
    models_path, auth_profiles_path, gateway_log_path = _write_runtime_sidecars(
        tmp_path,
        runtime_models_payload=runtime_models_payload,
    )
    openclaw_path.write_text(json.dumps(_base_openclaw_payload(), ensure_ascii=False), encoding="utf-8")
    agent_path.write_text(json.dumps({"id": "main", "model": "google/gemini-2.5-flash"}, ensure_ascii=False), encoding="utf-8")

    payload = _run_script(
        "--dry-run",
        "--profile", "gpt54-canary",
        "--openclaw-json", str(openclaw_path),
        "--agent-json", str(agent_path),
        "--state-json", str(state_path),
        "--models-json", str(models_path),
        "--auth-profiles-json", str(auth_profiles_path),
        "--gateway-log", str(gateway_log_path),
        env_overrides={"OPENCLAW_TARGET_PRIMARY_MODEL": "openai-codex/gpt-5.4"},
    )

    assert payload["ok"] is True
    assert payload["details"]["primary_model"] == "openai-codex/gpt-5.4"
    assert payload["reason"] == "canary_target_ready"


def test_production_safe_blocks_provider_with_only_expired_profiles(tmp_path):
    """Провайдер с одними просроченными OAuth-профилями не должен попадать в safe-chain."""

    openclaw_path = tmp_path / "openclaw.json"
    agent_path = tmp_path / "agent.json"
    state_path = tmp_path / "state.json"
    recent_ts = datetime.now(timezone.utc).isoformat()
    auth_profiles_payload = {
        "profiles": {
            "openai-codex:default": {"provider": "openai-codex"},
            "google-antigravity:pavelr7@gmail.com": {
                "provider": "google-antigravity",
                "email": "pavelr7@gmail.com",
                "expires": int((datetime.now(timezone.utc) - timedelta(minutes=5)).timestamp() * 1000),
            },
        },
        "usageStats": {
            "openai-codex:default": {
                "failureCounts": {"model_not_found": 2},
            },
            "google-antigravity:pavelr7@gmail.com": {},
        },
    }
    models_path, auth_profiles_path, gateway_log_path = _write_runtime_sidecars(
        tmp_path,
        auth_profiles_payload=auth_profiles_payload,
        gateway_log_text=f'{recent_ts} [model-fallback] Model "openai-codex/gpt-4.5-preview" not found.\n',
    )
    payload_openclaw = _base_openclaw_payload()
    payload_openclaw["agents"]["defaults"]["model"]["primary"] = "openai-codex/gpt-4.5-preview"
    payload_openclaw["agents"]["defaults"]["model"]["fallbacks"] = [
        "google-antigravity/gemini-3.1-pro-preview",
        "google/gemini-2.5-flash",
    ]
    openclaw_path.write_text(json.dumps(payload_openclaw, ensure_ascii=False), encoding="utf-8")
    agent_path.write_text(json.dumps({"id": "main", "model": "openai-codex/gpt-4.5-preview"}, ensure_ascii=False), encoding="utf-8")

    payload = _run_script(
        "--dry-run",
        "--profile", "production-safe",
        "--openclaw-json", str(openclaw_path),
        "--agent-json", str(agent_path),
        "--state-json", str(state_path),
        "--models-json", str(models_path),
        "--auth-profiles-json", str(auth_profiles_path),
        "--gateway-log", str(gateway_log_path),
    )

    assert payload["ok"] is True
    assert payload["details"]["primary_model"] == "google/gemini-2.5-flash"
    provider_health = payload["details"]["special_profile"]["provider_health"]["google-antigravity"]
    assert provider_health["disabled"] is True
    assert provider_health["disabled_reason"] == "auth_expired"
    assert provider_health["healthy_profiles"] == []
    assert provider_health["expired_profiles"] == [
        "google-antigravity:pavelr7@gmail.com"
    ]
