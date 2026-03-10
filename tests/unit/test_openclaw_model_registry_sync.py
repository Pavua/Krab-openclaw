# -*- coding: utf-8 -*-
"""
Тесты безопасного sync canary-модели в runtime registry OpenClaw.

Покрываем:
1) добавление target-модели в `models.json` и `openclaw.json`;
2) обновление только reasoning-флага, если модель уже есть;
3) отсутствие влияния на production routing section.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "openclaw_model_registry_sync.py"


def _load_script_module():
    spec = importlib.util.spec_from_file_location("openclaw_model_registry_sync", SCRIPT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_sync_registry_adds_target_model_to_both_runtime_files(tmp_path: Path) -> None:
    mod = _load_script_module()
    models_json = tmp_path / "models.json"
    openclaw_json = tmp_path / "openclaw.json"

    models_json.write_text(
        json.dumps(
            {
                "providers": {
                    "openai-codex": {
                        "models": [
                            {
                                "id": "gpt-4.5-preview",
                                "name": "ChatGPT 4.5 Preview",
                                "reasoning": False,
                                "contextWindow": 128000,
                                "maxTokens": 16384,
                            }
                        ]
                    }
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    openclaw_json.write_text(
        json.dumps(
            {
                "models": {
                    "providers": {
                        "openai-codex": {
                            "models": [
                                {
                                    "id": "gpt-4.5-preview",
                                    "name": "ChatGPT 4.5 Preview",
                                    "reasoning": False,
                                    "contextWindow": 128000,
                                    "maxTokens": 16384,
                                }
                            ]
                        }
                    }
                },
                "agents": {
                    "defaults": {
                        "model": {
                            "primary": "openai-codex/gpt-4.5-preview",
                            "fallbacks": ["google/gemini-2.5-flash"],
                        }
                    }
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    report = mod.sync_registry(
        target_model="openai-codex/gpt-5.4",
        reasoning=True,
        models_json=models_json,
        openclaw_json=openclaw_json,
    )

    assert report["ok"] is True
    assert report["models_json"]["changed"] is True
    assert report["openclaw_json"]["changed"] is True

    models_payload = json.loads(models_json.read_text(encoding="utf-8"))
    runtime_models = models_payload["providers"]["openai-codex"]["models"]
    added = next(item for item in runtime_models if item["id"] == "gpt-5.4")
    assert added["reasoning"] is True
    assert added["contextWindow"] == 128000
    assert added["maxTokens"] == 16384

    openclaw_payload = json.loads(openclaw_json.read_text(encoding="utf-8"))
    runtime_primary = openclaw_payload["agents"]["defaults"]["model"]["primary"]
    assert runtime_primary == "openai-codex/gpt-4.5-preview"


def test_sync_registry_updates_reasoning_when_model_already_present(tmp_path: Path) -> None:
    mod = _load_script_module()
    models_json = tmp_path / "models.json"
    openclaw_json = tmp_path / "openclaw.json"

    payload = {
        "providers": {
            "openai-codex": {
                "models": [
                    {
                        "id": "gpt-5.4",
                        "name": "ChatGPT GPT-5.4",
                        "reasoning": False,
                    }
                ]
            }
        }
    }
    models_json.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    openclaw_json.write_text(
        json.dumps({"models": payload}, ensure_ascii=False),
        encoding="utf-8",
    )

    report = mod.sync_registry(
        target_model="openai-codex/gpt-5.4",
        reasoning=True,
        models_json=models_json,
        openclaw_json=openclaw_json,
    )

    assert report["ok"] is True
    assert report["models_json"]["reason"] == "reasoning_updated"
    assert report["openclaw_json"]["reason"] == "reasoning_updated"

    models_payload = json.loads(models_json.read_text(encoding="utf-8"))
    assert models_payload["providers"]["openai-codex"]["models"][0]["reasoning"] is True


def test_sync_registry_rejects_invalid_target_model(tmp_path: Path) -> None:
    mod = _load_script_module()
    report = mod.sync_registry(
        target_model="",
        reasoning=True,
        models_json=tmp_path / "models.json",
        openclaw_json=tmp_path / "openclaw.json",
    )
    assert report["ok"] is False
    assert report["error"] == "invalid_target_model"
