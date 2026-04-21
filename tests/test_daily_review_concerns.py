# -*- coding: utf-8 -*-
"""
Регрессионные тесты для daily-review замечаний от 2026-04-21.

Проверяем два класса рисков:
- Memory Doctor не должен блокировать async event loop через `subprocess.run`;
- новые runtime-модули имеют хотя бы базовое поведенческое покрытие, чтобы
  будущие агенты не меняли их вслепую.
"""

from __future__ import annotations

import asyncio
import sqlite3
import sys
from pathlib import Path

import pytest


@pytest.mark.asyncio
async def test_memory_doctor_async_command_capture() -> None:
    """Async subprocess-helper возвращает stdout/stderr без блокировки event loop."""
    from src.core.memory_doctor import _run_command_capture

    task = asyncio.create_task(
        _run_command_capture(
            [sys.executable, "-c", "import sys; print('ok'); print('err', file=sys.stderr)"],
            timeout_sec=5,
        )
    )
    await asyncio.sleep(0)
    returncode, stdout, stderr = await task

    assert returncode == 0
    assert stdout.strip() == "ok"
    assert stderr.strip() == "err"


@pytest.mark.asyncio
async def test_memory_doctor_repairs_use_async_subprocess(monkeypatch, tmp_path: Path) -> None:
    """run_repairs запускает backfill/MCP restart через async-helper, а не subprocess.run."""
    import src.core.memory_doctor as memory_doctor

    db_path = tmp_path / "archive.db"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE messages(id INTEGER PRIMARY KEY, text TEXT)")
    conn.commit()
    conn.close()

    calls: list[list[str]] = []

    async def fake_run(argv: list[str], *, timeout_sec: float) -> tuple[int, str, str]:
        calls.append(argv)
        return 0, f"timeout={timeout_sec}", ""

    monkeypatch.setattr(memory_doctor, "_run_command_capture", fake_run)
    monkeypatch.setattr(memory_doctor, "_find_python", lambda: sys.executable)
    monkeypatch.setenv("PATH", "/usr/bin:/bin")

    result = await memory_doctor.run_repairs(
        checks={
            "encoded_ratio": {"status": "fail"},
            "mcp_reachable": {"status": "fail"},
        },
        db_path=db_path,
    )

    actions = [item["action"] for item in result["repairs"]]
    assert result["ok"] is True
    assert "wal_checkpoint" in actions
    assert "backfill_embeddings" in actions
    assert "restart_mcp_yung_nagato" in actions
    assert any("encode_memory_phase2.py" in " ".join(call) for call in calls)
    assert any(call[:3] == ["launchctl", "kickstart", "-k"] for call in calls)


def test_memory_doctor_no_subprocess_run_regression() -> None:
    """В async Memory Doctor не должен вернуться blocking `subprocess.run`."""
    source = Path("src/core/memory_doctor.py").read_text(encoding="utf-8")
    assert "subprocess.run(" not in source


def test_message_priority_dispatcher_core_rules(monkeypatch) -> None:
    """Priority classifier отдаёт P0 для DM/команд и P2 для muted/mention-only."""
    from src.core import message_priority_dispatcher as dispatcher

    assert dispatcher.classify_priority(
        "привет", "PRIVATE", is_dm=False, is_reply_to_self=False, has_mention=False, chat_mode="active"
    ) == (dispatcher.Priority.P0_INSTANT, "dm")
    assert dispatcher.classify_priority(
        " !status", "GROUP", is_dm=False, is_reply_to_self=False, has_mention=False, chat_mode="active"
    ) == (dispatcher.Priority.P0_INSTANT, "command")
    assert dispatcher.classify_priority(
        "тихо", "GROUP", is_dm=False, is_reply_to_self=False, has_mention=False, chat_mode="muted"
    ) == (dispatcher.Priority.P2_LOW, "muted")
    assert dispatcher.classify_priority(
        "обычно", "GROUP", is_dm=False, is_reply_to_self=False, has_mention=False, chat_mode="active"
    ) == (dispatcher.Priority.P1_NORMAL, "active")


def test_runtime_policy_aliases_and_provider_state(monkeypatch) -> None:
    """Runtime policy нормализует режимы и честно помечает login_required."""
    from src.core import runtime_policy

    monkeypatch.setenv("KRAB_RUNTIME_MODE", "release-safe")
    assert runtime_policy.current_runtime_mode() == "release-safe-runtime"
    assert runtime_policy.runtime_mode_release_safe("release-safe-runtime") is True

    policy = runtime_policy.provider_runtime_policy(
        "codex-cli",
        readiness="blocked",
        auth_mode="cli",
        helper_available=True,
        cli_login_ready=False,
        quota_state="limited",
    )

    assert policy["runtime_mode"] == "release-safe-runtime"
    assert policy["primary_policy"] == "personal-primary"
    assert policy["login_state"] == "login_required"
    assert policy["stability_score"] < 0.76


def test_sentry_integration_redacts_pii_and_drops_shutdown_noise() -> None:
    """Sentry hook скрывает секреты и отбрасывает известный benign shutdown noise."""
    from src.core import sentry_integration

    event = {
        "message": "token sk-abcdefghijklmnopqrstuvwxyz123456 and +380 93 264 99 97",
        "exception": {"values": [{"value": "Bearer abcdefghijklmnopqrstuvwxyz1234567890"}]},
        "breadcrumbs": {
            "values": [{"message": "123456789:abcdefghijklmnopqrstuvwxyzABCDE", "data": {"k": "AIzaabcdefghijklmnopqrstuvwxyz123456"}}]
        },
        "extra": {"dsn": "https://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa@bbb.ingest.sentry.io/123"},
    }

    redacted = sentry_integration._before_send(event, {})
    assert redacted is not None
    rendered = repr(redacted)
    assert "sk-abcdefghijklmnopqrstuvwxyz123456" not in rendered
    assert "+380 93 264 99 97" not in rendered
    assert "Bearer abcdefghijklmnopqrstuvwxyz1234567890" not in rendered
    assert "<API_KEY>" in rendered
    assert "<PHONE>" in rendered

    noise = {
        "exception": {
            "values": [
                {
                    "value": "Cannot operate on a closed database",
                    "stacktrace": {"frames": [{"filename": "pyrogram/session/session.py"}]},
                }
            ]
        }
    }
    assert sentry_integration._before_send(noise, {}) is None


def test_browser_bridge_detects_debug_profile_and_stale_ws(monkeypatch, tmp_path: Path) -> None:
    """BrowserBridge читает DevToolsActivePort из debug-profile и ловит stale WS ошибки."""
    from src.integrations.browser_bridge import BrowserBridge

    active_port = tmp_path / ".openclaw" / "chrome-debug-profile" / "DevToolsActivePort"
    active_port.parent.mkdir(parents=True)
    active_port.write_text("9222\n/devtools/browser/test-uuid\n", encoding="utf-8")
    monkeypatch.setenv("KRAB_OPERATOR_HOME", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))

    bridge = BrowserBridge()

    assert bridge._read_devtools_ws_endpoint() == "ws://127.0.0.1:9222/devtools/browser/test-uuid"
    assert bridge._should_prefer_raw_cdp() == (
        True,
        "ws://127.0.0.1:9222/devtools/browser/test-uuid",
    )
    assert BrowserBridge._is_stale_ws_error(RuntimeError("HTTP 404 InvalidStatus")) is True


def test_command_handlers_small_pure_helpers() -> None:
    """Вынесенный command_handlers покрыт через чистые helper-функции без Telegram I/O."""
    from src.handlers import command_handlers

    assert command_handlers._parse_duration("1h30m20s") == 5420
    assert command_handlers._parse_duration("90") == 90
    assert command_handlers._parse_duration("0s") is None
    assert command_handlers._fmt_duration(3661) == "1ч 1м 1с"
    assert command_handlers._format_size_gb(0.49) == "0.49 GB"

    chunks = command_handlers._split_text_for_telegram("абвгдеж", limit=3)
    assert chunks == ["абв", "где", "ж"]
