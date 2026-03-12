# -*- coding: utf-8 -*-
"""
Тесты bootstrap helper для новой macOS-учётки OpenClaw.

Покрываем:
1) bootstrap запускает официальный onboard, если `openclaw.json` ещё нет;
2) helper создаёт минимальные `models.json` и `auth-profiles.json`;
3) если runtime skeleton уже существует, повторный bootstrap не трогает onboarding.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import openclaw_account_bootstrap as mod


def test_bootstrap_runs_onboard_and_creates_missing_json(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Пустая учётка должна получить config + models/auth skeleton."""
    monkeypatch.setattr(mod.Path, "home", classmethod(lambda cls: tmp_path))

    calls: list[str] = []

    def _fake_onboard(openclaw_bin: str) -> dict[str, object]:
        calls.append(openclaw_bin)
        config_path = tmp_path / ".openclaw" / "openclaw.json"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(json.dumps({"gateway": {"mode": "local"}}, ensure_ascii=False), encoding="utf-8")
        return {"cmd": [openclaw_bin, "onboard"], "rc": 0, "output": "ok"}

    monkeypatch.setattr(mod, "_run_openclaw_onboard", _fake_onboard)

    report = mod.bootstrap_openclaw_account("openclaw")

    assert report["ok"] is True
    assert report["bootstrapped_config"] is True
    assert calls == ["openclaw"]
    assert (tmp_path / ".openclaw" / "openclaw.json").exists() is True
    assert (tmp_path / ".openclaw" / "agents" / "main" / "agent" / "models.json").exists() is True
    assert (tmp_path / ".openclaw" / "agents" / "main" / "agent" / "auth-profiles.json").exists() is True
    config_payload = json.loads((tmp_path / ".openclaw" / "openclaw.json").read_text(encoding="utf-8"))
    assert config_payload["models"]["providers"]["google"]["baseUrl"] == "https://generativelanguage.googleapis.com/v1beta"
    assert config_payload["models"]["providers"]["google"]["models"] == []
    assert config_payload["models"]["providers"]["lmstudio"]["baseUrl"] == "http://localhost:1234/v1"


def test_bootstrap_is_idempotent_when_files_exist(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Повторный запуск не должен снова звать onboarding."""
    monkeypatch.setattr(mod.Path, "home", classmethod(lambda cls: tmp_path))
    openclaw_root = tmp_path / ".openclaw"
    (openclaw_root / "agents" / "main" / "agent").mkdir(parents=True, exist_ok=True)
    (openclaw_root / "openclaw.json").write_text("{}", encoding="utf-8")
    (openclaw_root / "agents" / "main" / "agent" / "models.json").write_text("{}", encoding="utf-8")
    (openclaw_root / "agents" / "main" / "agent" / "auth-profiles.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(
        mod,
        "_run_openclaw_onboard",
        lambda openclaw_bin: pytest.fail("onboard не должен вызываться повторно"),
    )

    report = mod.bootstrap_openclaw_account("openclaw")

    assert report["ok"] is True
    assert report["bootstrapped_config"] is False
    assert report["models"]["created"] is False
    assert report["auth_profiles"]["created"] is False


def test_bootstrap_returns_error_when_onboard_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Если официальный onboard упал, helper должен вернуть честный error report."""
    monkeypatch.setattr(mod.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(
        mod,
        "_run_openclaw_onboard",
        lambda openclaw_bin: {"cmd": [openclaw_bin, "onboard"], "rc": 7, "output": "boom"},
    )

    report = mod.bootstrap_openclaw_account("openclaw")

    assert report["ok"] is False
    assert report["bootstrapped_config"] is False
    assert report["onboard"]["rc"] == 7


def test_bootstrap_normalizes_existing_openclaw_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Если config уже есть, helper должен добить provider-skeleton до валидной формы."""
    monkeypatch.setattr(mod.Path, "home", classmethod(lambda cls: tmp_path))
    openclaw_root = tmp_path / ".openclaw"
    (openclaw_root / "agents" / "main" / "agent").mkdir(parents=True, exist_ok=True)
    (openclaw_root / "openclaw.json").write_text(
        json.dumps({"models": {"providers": {"google": {"apiKey": ""}}}}, ensure_ascii=False),
        encoding="utf-8",
    )

    report = mod.bootstrap_openclaw_account("openclaw")
    payload = json.loads((openclaw_root / "openclaw.json").read_text(encoding="utf-8"))

    assert report["ok"] is True
    assert payload["models"]["providers"]["google"]["baseUrl"] == "https://generativelanguage.googleapis.com/v1beta"
    assert payload["models"]["providers"]["google"]["models"] == []
    assert payload["models"]["providers"]["lmstudio"]["baseUrl"] == "http://localhost:1234/v1"
