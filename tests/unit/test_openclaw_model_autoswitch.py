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
import subprocess
import sys
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


def _run_script(*args: str) -> dict:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), *args],
        capture_output=True,
        text=True,
        check=True,
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
    openclaw_path.write_text(json.dumps(_base_openclaw_payload(), ensure_ascii=False), encoding="utf-8")
    agent_path.write_text(json.dumps({"id": "main", "model": "google/gemini-2.5-flash"}, ensure_ascii=False), encoding="utf-8")

    payload = _run_script(
        "--dry-run",
        "--profile", "local-first",
        "--openclaw-json", str(openclaw_path),
        "--agent-json", str(agent_path),
        "--state-json", str(state_path),
    )

    assert payload["ok"] is True
    assert payload["details"]["effective_profile"] == "local-first"
    assert payload["details"]["primary_model"] == "lmstudio/zai-org/glm-4.6v-flash"


def test_toggle_switches_between_cloud_and_local_profiles(tmp_path):
    openclaw_path = tmp_path / "openclaw.json"
    agent_path = tmp_path / "agent.json"
    state_path = tmp_path / "state.json"
    openclaw_path.write_text(json.dumps(_base_openclaw_payload(), ensure_ascii=False), encoding="utf-8")
    agent_path.write_text(json.dumps({"id": "main", "model": "google/gemini-2.5-flash"}, ensure_ascii=False), encoding="utf-8")

    first = _run_script(
        "--profile", "toggle",
        "--openclaw-json", str(openclaw_path),
        "--agent-json", str(agent_path),
        "--state-json", str(state_path),
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
    )
    assert second["ok"] is True
    assert second["details"]["effective_profile"] == "cloud-first"

    openclaw_after_second = json.loads(openclaw_path.read_text(encoding="utf-8"))
    assert openclaw_after_second["agents"]["defaults"]["model"]["primary"] == "google/gemini-2.5-flash"
    assert state_path.exists() is True
