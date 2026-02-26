# -*- coding: utf-8 -*-
"""
Тесты страховочного idle-guard для LM Studio.

Проверяем критичные ветки:
- построение каскада payload для unload;
- fallback с `all=true` на instance-параметры;
- CLI-ветку при отсутствии/наличии `lms`.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys

import pytest


def _load_guard_module():
    root = Path(__file__).resolve().parents[1]
    module_path = root / "scripts" / "lmstudio_idle_guard.py"
    spec = importlib.util.spec_from_file_location("lmstudio_idle_guard", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_build_unload_attempts_contains_all_instance_and_model_payloads() -> None:
    guard = _load_guard_module()
    loaded = [
        {
            "id": "zai-org/glm-4.6v-flash",
            "loaded": True,
            "loaded_instances": [
                {"instance_id": "inst-a"},
                {"instanceReference": "inst-b"},
            ],
        }
    ]

    attempts = guard._build_unload_attempts(loaded)
    payloads = [payload for _, payload in attempts]

    assert attempts[0] == ("all", {"all": True})
    assert {"instance_id": "inst-a"} in payloads
    assert {"instanceReference": "inst-b"} in payloads
    assert {"model": "zai-org/glm-4.6v-flash"} in payloads


def test_run_http_unload_fallbacks_from_all_to_instance(monkeypatch: pytest.MonkeyPatch) -> None:
    guard = _load_guard_module()
    loaded = [{"id": "model-a", "loaded": True, "loaded_instances": [{"instance_id": "inst-1"}]}]

    def fake_http_json(method: str, url: str, body=None, timeout: float = 6.0):  # noqa: ARG001
        if body == {"all": True}:
            return guard.HttpResult(ok=False, status=400, payload={"error": "bad_request"}, error="http_400")
        if body == {"instance_id": "inst-1"}:
            return guard.HttpResult(ok=True, status=200, payload={"ok": True}, error="")
        return guard.HttpResult(ok=False, status=404, payload={"error": "not_found"}, error="http_404")

    monkeypatch.setattr(guard, "_http_json", fake_http_json)
    unloaded, err, attempts = guard._run_http_unload("http://127.0.0.1:1234", loaded)

    assert unloaded is True
    assert err == ""
    assert [a.payload for a in attempts][:2] == [{"all": True}, {"instance_id": "inst-1"}]


def test_run_cli_unload_all_returns_not_found_when_lms_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    guard = _load_guard_module()
    monkeypatch.setattr(guard.Path, "home", classmethod(lambda cls: tmp_path))

    ok, err = guard._run_cli_unload_all()
    assert ok is False
    assert err == "lms_cli_not_found"


def test_run_cli_unload_all_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    guard = _load_guard_module()
    monkeypatch.setattr(guard.Path, "home", classmethod(lambda cls: tmp_path))

    lms_path = tmp_path / ".lmstudio" / "bin" / "lms"
    lms_path.parent.mkdir(parents=True, exist_ok=True)
    lms_path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")

    def fake_run(*args, **kwargs):  # noqa: ANN002, ANN003
        return subprocess.CompletedProcess(args=args[0], returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(guard.subprocess, "run", fake_run)
    ok, err = guard._run_cli_unload_all()

    assert ok is True
    assert err == ""
