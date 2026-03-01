# -*- coding: utf-8 -*-
"""
Тесты для scripts/openclaw_runtime_repair.py.

Проверяют:
1) Выбор корректного AI Studio ключа.
2) Снятие залипших local-overrides в channel sessions.
3) Нормализацию allowlist.
"""

from __future__ import annotations

import json
from pathlib import Path

from scripts.openclaw_runtime_repair import (
    apply_dm_policy,
    choose_target_key,
    normalize_allowlist,
    repair_sessions,
)


def test_choose_target_key_prefers_free_in_auto() -> None:
    tier, key = choose_target_key(
        free_key="AIzaFREE1234567890123456789012345",
        paid_key="AIzaPAID1234567890123456789012345",
        tier="auto",
    )
    assert tier == "free"
    assert key.startswith("AIzaFREE")


def test_choose_target_key_paid_when_free_invalid() -> None:
    tier, key = choose_target_key(
        free_key="AQ.INVALID",
        paid_key="AIzaPAID1234567890123456789012345",
        tier="auto",
    )
    assert tier == "paid"
    assert key.startswith("AIzaPAID")


def test_repair_sessions_clears_local_overrides(tmp_path: Path) -> None:
    sessions_path = tmp_path / "sessions.json"
    payload = {
        "agent:main:telegram:direct:312322764": {
            "modelOverride": "local",
            "providerOverride": "lmstudio",
            "modelProvider": "lmstudio",
            "model": "local",
        },
        "agent:main:openai:abc": {
            "modelOverride": "local",
            "providerOverride": "lmstudio",
            "modelProvider": "lmstudio",
            "model": "local",
        },
    }
    sessions_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    report = repair_sessions(
        sessions_path,
        channels=("telegram",),
        default_provider="google",
        default_model="google/gemini-2.5-flash",
    )
    assert report["changed"] is True
    assert report["fixed_entries"] == 1

    updated = json.loads(sessions_path.read_text(encoding="utf-8"))
    item = updated["agent:main:telegram:direct:312322764"]
    assert "modelOverride" not in item
    assert "providerOverride" not in item
    assert item["modelProvider"] == "google"
    assert item["model"] == "google/gemini-2.5-flash"

    untouched = updated["agent:main:openai:abc"]
    assert untouched["modelOverride"] == "local"
    assert untouched["providerOverride"] == "lmstudio"


def test_normalize_allowlist_removes_wildcards_and_duplicates(tmp_path: Path) -> None:
    allow_path = tmp_path / "imessage-allowFrom.json"
    allow_path.write_text(
        json.dumps(["+", "", "user@example.com", "user@example.com", "*", "+34600000000"], ensure_ascii=False),
        encoding="utf-8",
    )
    report = normalize_allowlist(allow_path)
    assert report["changed"] is True
    result = json.loads(allow_path.read_text(encoding="utf-8"))
    assert result == ["user@example.com", "+34600000000"]


def test_apply_dm_policy_open_adds_wildcard_allow_from(tmp_path: Path) -> None:
    openclaw_path = tmp_path / "openclaw.json"
    openclaw_path.write_text(
        json.dumps(
            {
                "channels": {
                    "telegram": {"enabled": True, "dmPolicy": "open"},
                    "imessage": {"enabled": True, "dmPolicy": "open", "allowFrom": []},
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    report = apply_dm_policy(openclaw_path, ("telegram", "imessage"), "open")
    assert report["changed"] is True
    assert report["allow_from_changes"] == 2
    payload = json.loads(openclaw_path.read_text(encoding="utf-8"))
    assert payload["channels"]["telegram"]["allowFrom"] == ["*"]
    assert payload["channels"]["imessage"]["allowFrom"] == ["*"]
