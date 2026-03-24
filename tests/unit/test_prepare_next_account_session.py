# -*- coding: utf-8 -*-
"""
Тесты orchestrator'а подготовки следующей macOS-учётки.
"""

from __future__ import annotations

import json
from pathlib import Path

from scripts import prepare_next_account_session as orchestrator


def test_prepare_next_account_session_writes_latest_artifacts(monkeypatch, tmp_path: Path) -> None:
    """Orchestrator должен сохранять latest JSON/MD и завершаться успешно при зелёных шагах."""
    monkeypatch.setattr(orchestrator, "OPS_DIR", tmp_path)
    monkeypatch.setattr(
        orchestrator,
        "_run_command",
        lambda name, argv: {
            "name": name,
            "ok": True,
            "returncode": 0,
            "stdout": "ok",
            "stderr": "",
        },
    )
    monkeypatch.setattr(
        orchestrator,
        "_latest_handoff_targets",
        lambda: {
            "dir": "/tmp/handoff_bundle_dir",
            "zip": "/tmp/handoff_bundle.zip",
        },
    )

    rc = orchestrator.main()

    assert rc == 0
    latest_json = tmp_path / "prepare_next_account_session_latest.json"
    latest_md = tmp_path / "prepare_next_account_session_latest.md"
    assert latest_json.exists() is True
    assert latest_md.exists() is True
    payload = json.loads(latest_json.read_text(encoding="utf-8"))
    assert payload["ok"] is True
    assert payload["latest_handoff_dir"] == "/tmp/handoff_bundle_dir"
    assert payload["latest_handoff_zip"] == "/tmp/handoff_bundle.zip"
