# -*- coding: utf-8 -*-
"""
Тесты для scripts/openclaw_god_mode_sync.py.

Проверяют:
1) закрепление `tools.exec`/`approvals.exec`/`agents.list[main].tools.profile`;
2) создание wildcard allowlist в `exec-approvals.json`;
3) идемпотентность повторного прогона.
"""

from __future__ import annotations

import json
from pathlib import Path

from scripts.openclaw_god_mode_sync import sync_exec_approvals, sync_god_mode, sync_openclaw_json


def test_sync_openclaw_json_enables_full_exec_profile(tmp_path: Path) -> None:
    openclaw_path = tmp_path / "openclaw.json"
    openclaw_path.write_text(
        json.dumps(
            {
                "tools": {"exec": {"security": "allowlist", "ask": "on-miss"}},
                "approvals": {"exec": {"enabled": True}},
                "agents": {
                    "list": [
                        {
                            "id": "main",
                            "tools": {"profile": "messaging"},
                        }
                    ]
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    report = sync_openclaw_json(openclaw_path)

    assert report["changed"] is True
    payload = json.loads(openclaw_path.read_text(encoding="utf-8"))
    assert payload["tools"]["exec"]["host"] == "gateway"
    assert payload["tools"]["exec"]["security"] == "full"
    assert payload["tools"]["exec"]["ask"] == "off"
    assert payload["approvals"]["exec"]["enabled"] is False
    assert payload["agents"]["list"][0]["tools"]["profile"] == "full"


def test_sync_exec_approvals_adds_wildcard_for_main_and_global(tmp_path: Path) -> None:
    approvals_path = tmp_path / "exec-approvals.json"

    report = sync_exec_approvals(approvals_path)

    assert report["changed"] is True
    payload = json.loads(approvals_path.read_text(encoding="utf-8"))
    assert payload["defaults"]["security"] == "allowlist"
    assert payload["defaults"]["autoAllowSkills"] is True
    assert any(item["pattern"] == "*" for item in payload["agents"]["main"]["allowlist"])
    assert any(item["pattern"] == "*" for item in payload["agents"]["*"]["allowlist"])


def test_sync_god_mode_is_idempotent(tmp_path: Path) -> None:
    openclaw_path = tmp_path / "openclaw.json"
    approvals_path = tmp_path / "exec-approvals.json"

    first = sync_god_mode(openclaw_path, approvals_path)
    second = sync_god_mode(openclaw_path, approvals_path)

    assert first["changed"] is True
    assert second["changed"] is False
