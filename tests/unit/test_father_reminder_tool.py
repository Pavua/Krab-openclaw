"""Тесты личного помощника по напоминаниям отцу.

Проверяем только безопасные сценарии: приватный конфиг, черновик, dry-run и
read-only обработку недоступной Messages БД. Реальных Telegram/iMessage
отправок в тестах нет.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TOOLS_DIR = REPO_ROOT / "scripts" / "agent_tools"
SCRIPT = TOOLS_DIR / "krab_father_reminder.py"
PYTHON = sys.executable


def _load_tool_module():
    spec = importlib.util.spec_from_file_location("krab_father_reminder", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["krab_father_reminder"] = module
    spec.loader.exec_module(module)
    return module


def _run(args: list[str], *, home: Path) -> tuple[int, dict]:
    # Wave 65-F: conftest.py outputs KRAB_RUNTIME_STATE_DIR in tmp dir для
    # subprocess isolation. Здесь явно ре-привязываем state dir к
    # передаваемому ``home`` — иначе script писал бы state в conftest's
    # tmp, а тест читал бы из ``home/.openclaw/krab_runtime_state``.
    env = {
        **os.environ,
        "HOME": str(home),
        "KRAB_RUNTIME_STATE_DIR": str(home / ".openclaw" / "krab_runtime_state"),
    }
    proc = subprocess.run(
        [PYTHON, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=15,
    )
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    return proc.returncode, payload


def test_init_status_and_draft_use_private_config(tmp_path: Path) -> None:
    rc, out = _run(
        [
            "init",
            "--telegram-username",
            "@father",
            "--imessage-handle",
            "+10000000000",
            "--objective",
            "документы",
        ],
        home=tmp_path,
    )
    assert rc == 0
    assert out["ok"] is True
    assert out["telegram_username"] == "@fa***"

    rc, status = _run(["status"], home=tmp_path)
    assert rc == 0
    assert status["config_exists"] is True
    assert status["objective_set"] is True

    rc, draft = _run(["draft"], home=tmp_path)
    assert rc == 0
    assert "документы" in draft["draft"]
    assert "когда реально сможешь" in draft["draft"]


def test_send_telegram_dry_run_does_not_import_pyrogram(tmp_path: Path) -> None:
    _run(["init", "--telegram-username", "@father", "--objective", "документы"], home=tmp_path)
    rc, out = _run(["send", "--channel", "telegram", "--dry-run"], home=tmp_path)
    assert rc == 0
    assert out["ok"] is True
    assert out["dry_run"] is True
    assert out["channel"] == "telegram"
    assert out["recipient"] == "@fa***"


def test_run_due_dry_run_does_not_persist_last_send(tmp_path: Path) -> None:
    _run(["init", "--telegram-username", "@father", "--objective", "документы"], home=tmp_path)
    rc, out = _run(["run-due", "--channel", "telegram", "--dry-run"], home=tmp_path)
    assert rc == 0
    assert out["ok"] is True
    assert out["dry_run"] is True
    assert out["sent"] is False

    state_path = tmp_path / ".openclaw" / "krab_runtime_state" / "father_reminder_state.json"
    assert not state_path.exists()


def test_run_due_respects_cadence_state(tmp_path: Path) -> None:
    _run(["init", "--telegram-username", "@father", "--objective", "документы"], home=tmp_path)
    state_dir = tmp_path / ".openclaw" / "krab_runtime_state"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "father_reminder_state.json").write_text(
        json.dumps({"last_sent_at": "2999-01-01T00:00:00+00:00"}),
        encoding="utf-8",
    )

    rc, out = _run(["run-due", "--channel", "telegram", "--dry-run"], home=tmp_path)
    assert rc == 0
    assert out["ok"] is True
    assert out["sent"] is False
    assert out["reason"] == "not_due"


def test_analyze_reports_missing_handle(tmp_path: Path) -> None:
    rc, out = _run(["analyze"], home=tmp_path)
    assert rc == 1
    assert out["ok"] is False
    assert out["error"] == "imessage_handle_not_configured"


def test_send_requires_configured_recipient(tmp_path: Path) -> None:
    rc, out = _run(["send", "--channel", "telegram", "--dry-run"], home=tmp_path)
    assert rc == 1
    assert out["ok"] is False
    assert out["error"] == "telegram_username_not_configured"


def test_extract_attributed_body_and_normalize_phone_handle() -> None:
    tool = _load_tool_module()
    text = "Пап, привет. Это тестовый iMessage."
    body = (
        b"\x04\x0bstreamtyped\x81\xe8\x03\x84\x01@\x84\x84\x84\x12"
        b"NSAttributedString\x00\x84\x84\x08NSObject\x00\x85\x92\x84\x84\x84"
        b"\x08NSString\x01\x94\x84\x01+"
        + bytes([len(text.encode("utf-8"))])
        + text.encode("utf-8")
        + b"\x86\x84\x02iI\x01\x0e\x92\x84\x84\x84\x0cNSDictionary"
    )

    assert tool._extract_attributed_body_text(body) == text
    assert tool._normalize_phone_handle("+380 (67) 523 58 54") == "+380675235854"
