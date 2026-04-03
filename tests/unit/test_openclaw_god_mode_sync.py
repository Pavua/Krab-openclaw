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
import subprocess
from pathlib import Path
from unittest.mock import Mock, patch

from scripts.openclaw_god_mode_sync import (
    apply_exec_approvals_to_gateway,
    sync_exec_approvals,
    sync_god_mode,
    sync_openclaw_json,
)


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

    first = sync_god_mode(openclaw_path, approvals_path, apply_gateway=False)
    second = sync_god_mode(openclaw_path, approvals_path, apply_gateway=False)

    assert first["changed"] is True
    assert second["changed"] is False


def test_apply_exec_approvals_to_gateway_skips_when_cli_missing(tmp_path: Path) -> None:
    approvals_path = tmp_path / "exec-approvals.json"
    approvals_path.write_text("{}", encoding="utf-8")

    with patch("scripts.openclaw_god_mode_sync.shutil.which", return_value=None):
        report = apply_exec_approvals_to_gateway(approvals_path, openclaw_bin="")

    assert report["attempted"] is False
    assert report["applied"] is False
    assert report["reason"] == "openclaw_cli_not_found"


def test_apply_exec_approvals_to_gateway_uses_cli_and_reports_success(tmp_path: Path) -> None:
    approvals_path = tmp_path / "exec-approvals.json"
    approvals_path.write_text(
        json.dumps(
            {
                "defaults": {"security": "allowlist"},
                "agents": {
                    "main": {
                        "allowlist": [
                            {
                                "pattern": "*",
                                "id": "abc",
                                "lastUsedAt": 123,
                                "source": "god-mode-sync",
                            }
                        ]
                    }
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    captured_payload: dict[str, object] = {}

    def _fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        file_index = command.index("--file") + 1
        upload_path = Path(command[file_index])
        captured_payload.update(json.loads(upload_path.read_text(encoding="utf-8")))
        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout=json.dumps({"ok": True, "target": "gateway"}),
            stderr="",
        )

    with patch("scripts.openclaw_god_mode_sync.subprocess.run", side_effect=_fake_run) as run_mock:
        report = apply_exec_approvals_to_gateway(
            approvals_path,
            openclaw_bin="/opt/homebrew/bin/openclaw",
            timeout_ms=4321,
        )

    assert report["attempted"] is True
    assert report["applied"] is True
    assert report["returncode"] == 0
    assert report["payload"] == {"ok": True, "target": "gateway"}
    run_mock.assert_called_once()
    command = run_mock.call_args.args[0]
    # Command may be [openclaw, ...] or [node, openclaw.mjs, ...] depending on environment
    assert "approvals" in command
    assert "set" in command
    assert "--gateway" in command
    assert "--file" in command
    assert "--json" in command
    assert "4321" in command
    allowlist_entry = captured_payload["agents"]["main"]["allowlist"][0]
    assert allowlist_entry == {
        "pattern": "*",
        "id": "abc",
        "lastUsedAt": 123,
    }


def test_sync_god_mode_reports_gateway_apply(tmp_path: Path) -> None:
    openclaw_path = tmp_path / "openclaw.json"
    approvals_path = tmp_path / "exec-approvals.json"

    with patch(
        "scripts.openclaw_god_mode_sync.apply_exec_approvals_to_gateway",
        return_value={"attempted": True, "applied": True},
    ) as gateway_apply_mock:
        report = sync_god_mode(
            openclaw_path,
            approvals_path,
            apply_gateway=True,
            openclaw_bin="/opt/homebrew/bin/openclaw",
        )

    assert report["gateway_apply"]["applied"] is True
    gateway_apply_mock.assert_called_once_with(
        approvals_path,
        openclaw_bin="/opt/homebrew/bin/openclaw",
    )
