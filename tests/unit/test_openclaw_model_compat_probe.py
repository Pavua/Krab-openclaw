# -*- coding: utf-8 -*-
"""
Тесты read-only compatibility probe OpenClaw.

Покрываем:
1) честный `BLOCKED`, если target-модели ещё нет в runtime registry;
2) `READY`, если registry/auth в порядке и оба gateway probe успешны.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "openclaw_model_compat_probe.py"


def _load_script_module():
    spec = importlib.util.spec_from_file_location("openclaw_model_compat_probe", SCRIPT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_runtime_files(
    tmp_path: Path,
    *,
    runtime_models: dict | None = None,
    auth_profiles: dict | None = None,
    openclaw_payload: dict | None = None,
    gateway_log: str = "",
) -> tuple[Path, Path, Path, Path]:
    openclaw_path = tmp_path / "openclaw.json"
    models_path = tmp_path / "models.json"
    auth_profiles_path = tmp_path / "auth-profiles.json"
    gateway_log_path = tmp_path / "gateway.err.log"

    openclaw_path.write_text(
        json.dumps(
            openclaw_payload or {"gateway": {"auth": {"token": "gateway-test-token"}}},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    models_path.write_text(
        json.dumps(
            runtime_models
            or {
                "providers": {
                    "openai-codex": {"models": [{"id": "gpt-4.5-preview", "reasoning": False}]},
                    "google": {"models": [{"id": "google/gemini-2.5-flash", "reasoning": False}]},
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    auth_profiles_path.write_text(
        json.dumps(
            auth_profiles
            or {
                "profiles": {"openai-codex:default": {"provider": "openai-codex"}},
                "usageStats": {"openai-codex:default": {}},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    gateway_log_path.write_text(gateway_log, encoding="utf-8")
    return openclaw_path, models_path, auth_profiles_path, gateway_log_path


def test_compat_probe_returns_blocked_when_target_missing_from_registry(tmp_path):
    mod = _load_script_module()
    openclaw_path, models_path, auth_profiles_path, gateway_log_path = _write_runtime_files(
        tmp_path
    )

    result = asyncio.run(
        mod.main_async(
            model="openai-codex/gpt-5.4",
            reasoning="high",
            skip_reasoning=False,
            openclaw_json=openclaw_path,
            models_json=models_path,
            auth_profiles_json=auth_profiles_path,
            gateway_log=gateway_log_path,
            base_url="http://127.0.0.1:18789",
        )
    )

    assert result["ok"] is False
    assert result["status"] == "BLOCKED"
    assert result["reason"] == "target_model_not_in_runtime_registry"
    assert result["promotion_ready"] is False


def test_compat_probe_returns_ready_when_registry_and_gateway_are_ok(tmp_path, monkeypatch):
    mod = _load_script_module()
    openclaw_path, models_path, auth_profiles_path, gateway_log_path = _write_runtime_files(
        tmp_path,
        runtime_models={
            "providers": {
                "openai-codex": {"models": [{"id": "gpt-5.4", "reasoning": True}]},
            }
        },
    )

    async def _fake_non_invasive(base_url: str, token: str, model: str) -> dict:
        assert base_url == "http://127.0.0.1:18789"
        assert token == "gateway-test-token"
        assert model == "openai-codex/gpt-5.4"
        return {"ok": True, "status": 400, "error": "ok_controlled_400"}

    async def _fake_chat(
        base_url: str, token: str, model: str, *, reasoning: str, max_output_tokens: int
    ) -> dict:
        assert model == "openai-codex/gpt-5.4"
        return {
            "ok": True,
            "status": 200,
            "error": "",
            "assistant_text": "ping",
            "reasoning": reasoning,
            "max_output_tokens": max_output_tokens,
        }

    monkeypatch.setattr(mod, "_probe_gateway_non_invasive", _fake_non_invasive)
    monkeypatch.setattr(mod, "_probe_gateway_chat", _fake_chat)

    result = asyncio.run(
        mod.main_async(
            model="openai-codex/gpt-5.4",
            reasoning="high",
            skip_reasoning=False,
            openclaw_json=openclaw_path,
            models_json=models_path,
            auth_profiles_json=auth_profiles_path,
            gateway_log=gateway_log_path,
            base_url="http://127.0.0.1:18789",
        )
    )

    assert result["ok"] is True
    assert result["status"] == "READY"
    assert result["reason"] == "compat_probe_passed"
    assert result["promotion_ready"] is True
