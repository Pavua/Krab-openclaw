"""
Тесты совместимости thinking-режимов OpenClaw.

Зачем нужен этот файл:
- фиксирует регрессию, из-за которой owner UI записывал legacy `auto` в
  `~/.openclaw/openclaw.json`, а OpenClaw 2026.3.11 после этого переставал
  поднимать gateway на `:18789`;
- проверяет и read-path, и write-path без зависимости от большого dirty-файла
  `test_web_app_runtime_endpoints.py`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.modules.web_app import WebApp
from tests.unit.test_web_app_runtime_endpoints import _DummyRouter, _make_client_with_router


def test_build_openclaw_runtime_controls_maps_legacy_auto_to_adaptive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Legacy `auto` должен читаться как `adaptive`, иначе runtime reload снова сломает gateway."""
    runtime_config = {
        "agents": {
            "defaults": {
                "model": {
                    "primary": "openai-codex/gpt-5.4",
                    "fallbacks": ["google/gemini-2.5-flash"],
                },
                "contextTokens": 128000,
                "thinkingDefault": "auto",
                "maxConcurrent": 4,
                "subagents": {"maxConcurrent": 8},
                "models": {
                    "google/gemini-2.5-flash": {"params": {"thinking": "auto"}},
                },
            }
        }
    }
    monkeypatch.setattr(
        WebApp,
        "_load_openclaw_runtime_config",
        classmethod(lambda cls: runtime_config),
    )

    payload = WebApp._build_openclaw_runtime_controls()

    assert payload["thinking_default"] == "adaptive"
    assert payload["thinking_modes"] == [
        "off",
        "minimal",
        "low",
        "medium",
        "high",
        "xhigh",
        "adaptive",
    ]
    chain_items = {item["model_id"]: item for item in payload["chain_items"]}
    assert chain_items["google/gemini-2.5-flash"]["explicit_thinking"] == "adaptive"
    assert chain_items["google/gemini-2.5-flash"]["effective_thinking"] == "adaptive"


def test_model_apply_set_runtime_chain_converts_legacy_auto_to_adaptive(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Write-path тоже должен конвертировать `auto`, чтобы не записывать невалидный runtime config."""
    openclaw_path = tmp_path / "openclaw.json"
    agent_path = tmp_path / "agent.json"
    openclaw_path.write_text(
        json.dumps(
            {
                "agents": {
                    "defaults": {
                        "model": {"primary": "openai-codex/gpt-5.4", "fallbacks": []},
                        "contextTokens": 128000,
                        "thinkingDefault": "off",
                        "maxConcurrent": 4,
                        "subagents": {"maxConcurrent": 8},
                        "models": {},
                    },
                    "list": [{"id": "main", "model": "openai-codex/gpt-5.4"}],
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    agent_path.write_text(
        json.dumps({"model": "openai-codex/gpt-5.4"}, ensure_ascii=False), encoding="utf-8"
    )

    monkeypatch.setattr(WebApp, "_openclaw_config_path", classmethod(lambda cls: openclaw_path))
    monkeypatch.setattr(WebApp, "_openclaw_agent_config_path", classmethod(lambda cls: agent_path))

    async def _fake_local_truth(*_args, **_kwargs) -> dict[str, object]:
        return {
            "preferred_model": "",
            "engine": "lm_studio",
            "active_model": "",
            "runtime_reachable": False,
            "loaded_models": [],
        }

    monkeypatch.setattr(WebApp, "_resolve_local_runtime_truth", _fake_local_truth)

    class _Router(_DummyRouter):
        def __init__(self) -> None:
            self.models = {"chat": "google/gemini-2.5-flash"}
            self.force_mode = "auto"
            self.local_engine = "lm_studio"

    client = _make_client_with_router(_Router())

    resp = client.post(
        "/api/model/apply",
        json={
            "action": "set_runtime_chain",
            "primary": "openai-codex/gpt-5.4",
            "fallbacks": ["google/gemini-2.5-flash"],
            "context_tokens": 128000,
            "thinking_default": "auto",
            "execution_preset": "parallel",
            "slot_thinking": {
                "openai-codex/gpt-5.4": "auto",
                "google/gemini-2.5-flash": "auto",
            },
        },
        headers={"X-Krab-Web-Key": "secret"},
    )

    assert resp.status_code == 200
    openclaw_payload = json.loads(openclaw_path.read_text(encoding="utf-8"))
    defaults = openclaw_payload["agents"]["defaults"]
    assert defaults["thinkingDefault"] == "adaptive"
    assert defaults["models"]["openai-codex/gpt-5.4"]["params"]["thinking"] == "adaptive"
    assert defaults["models"]["google/gemini-2.5-flash"]["params"]["thinking"] == "adaptive"
