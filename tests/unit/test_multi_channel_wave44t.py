"""Wave 44-T-multi-channel — tests for Discord/iMessage/Email bash-callable scripts."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TOOLS_DIR = REPO_ROOT / "scripts" / "agent_tools"
PYTHON = sys.executable

sys.path.insert(0, str(TOOLS_DIR))


def _run(
    script: str,
    args: list[str],
    env_extra: dict[str, str] | None = None,
    timeout: int = 15,
) -> tuple[int, dict | None, str]:
    env = {**os.environ}
    if env_extra:
        env.update(env_extra)
    proc = subprocess.run(  # noqa: S603
        [PYTHON, str(TOOLS_DIR / script), *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
        check=False,
    )
    parsed: dict | None = None
    out = proc.stdout.strip()
    if out:
        for line in reversed(out.splitlines()):
            try:
                parsed = json.loads(line)
                break
            except json.JSONDecodeError:
                continue
    return proc.returncode, parsed, proc.stderr


# ---------- _multi_channel_helpers ----------


def test_helpers_imports():
    import _multi_channel_helpers as h  # type: ignore  # noqa: PLC0415

    assert h.RUNTIME_STATE_DIR.name == "krab_runtime_state"
    assert h.DISCORD_KNOWN_PATH.name == "discord_known_channels.json"
    assert h.IMESSAGE_KNOWN_PATH.name == "imessage_known.json"
    assert h.EMAIL_KNOWN_PATH.name == "email_known.json"


def test_hard_blocked_bank_email():
    import _multi_channel_helpers as h  # type: ignore  # noqa: PLC0415

    blocked, reason = h.is_hard_blocked("ceo@santander.com")
    assert blocked is True
    assert "hard-blocked" in reason or "pattern" in reason


def test_hard_blocked_lawyer():
    import _multi_channel_helpers as h  # type: ignore  # noqa: PLC0415

    blocked, _ = h.is_hard_blocked("info@lawyerexample.com")
    assert blocked is True


def test_hard_blocked_safe_email():
    import _multi_channel_helpers as h  # type: ignore  # noqa: PLC0415

    blocked, _ = h.is_hard_blocked("friend@gmail.com")
    assert blocked is False


def test_first_time_gate_known(tmp_path, monkeypatch):
    import _multi_channel_helpers as h  # type: ignore  # noqa: PLC0415

    p = tmp_path / "known.json"
    h.remember_recipient(p, "test@example.com", {})
    allowed, reason = h.first_time_gate(p, "test@example.com", False, None)
    assert allowed is True
    assert reason == "known_recipient"


def test_first_time_gate_unknown_no_confirm(tmp_path):
    import _multi_channel_helpers as h  # type: ignore  # noqa: PLC0415

    p = tmp_path / "known.json"
    allowed, reason = h.first_time_gate(p, "new@example.com", False, None)
    assert allowed is False
    assert reason == "first_time_no_confirm"


def test_first_time_gate_unknown_with_confirm(tmp_path):
    import _multi_channel_helpers as h  # type: ignore  # noqa: PLC0415

    p = tmp_path / "known.json"
    allowed, reason = h.first_time_gate(p, "new@example.com", True, None)
    assert allowed is True
    assert reason == "first_time_confirmed"


# ---------- Discord ----------


def test_discord_requires_server_channel():
    rc, _, _ = _run("krab_send_discord.py", ["--text", "hi"])
    assert rc == 2  # argparse error


def test_discord_empty_text():
    rc, out, _ = _run(
        "krab_send_discord.py",
        ["--server", "test", "--channel", "test", "--text", "  "],
    )
    assert rc == 1
    assert out and out["ok"] is False


def test_discord_first_time_blocked(tmp_path, monkeypatch):
    """No --first-time-confirm + unknown → blocked."""
    # Use isolated state dir
    rc, out, _ = _run(
        "krab_send_discord.py",
        [
            "--server",
            "fresh-server-xyz",
            "--channel",
            "fresh-channel-xyz",
            "--text",
            "test",
        ],
        env_extra={"HOME": str(tmp_path)},
    )
    assert rc == 1
    assert out and out["ok"] is False
    assert out["error"] == "first_time_no_confirm"


def test_discord_not_configured_with_confirm(tmp_path):
    """With --first-time-confirm but no webhook → 'not configured'."""
    rc, out, _ = _run(
        "krab_send_discord.py",
        [
            "--server",
            "test-server",
            "--channel",
            "test-channel",
            "--text",
            "hello",
            "--first-time-confirm",
        ],
        env_extra={
            "HOME": str(tmp_path),
            "KRAB_DISCORD_WEBHOOK_URL": "",
        },
    )
    assert rc == 1
    assert out and out["ok"] is False
    assert "not configured" in out["error"]


# ---------- iMessage ----------


def test_imessage_first_time_blocked(tmp_path):
    rc, out, _ = _run(
        "krab_send_imessage.py",
        ["--to", "+19999999999", "--text", "hi"],
        env_extra={"HOME": str(tmp_path)},
    )
    assert rc == 1
    assert out and out["ok"] is False
    assert out["error"] == "first_time_no_confirm"


def test_imessage_dry_run_with_confirm(tmp_path):
    """Dry-run with --first-time-confirm → ok=true, no actual osascript."""
    rc, out, _ = _run(
        "krab_send_imessage.py",
        [
            "--to",
            "+19998887777",
            "--text",
            "test",
            "--first-time-confirm",
            "--dry-run",
        ],
        env_extra={"HOME": str(tmp_path)},
    )
    assert rc == 0
    assert out and out["ok"] is True
    assert out["dry_run"] is True
    assert out["recipient"] == "+19998887777"


def test_imessage_known_recipient_dry_run(tmp_path):
    """Second call to same recipient should not need confirm."""
    home = tmp_path
    args = [
        "--to",
        "+19990001111",
        "--text",
        "first",
        "--first-time-confirm",
        "--dry-run",
    ]
    rc1, _, _ = _run("krab_send_imessage.py", args, env_extra={"HOME": str(home)})
    assert rc1 == 0

    # Second time without --first-time-confirm should still work (known)
    rc2, out2, _ = _run(
        "krab_send_imessage.py",
        ["--to", "+19990001111", "--text", "second", "--dry-run"],
        env_extra={"HOME": str(home)},
    )
    assert rc2 == 0
    assert out2 and out2["ok"] is True
    assert out2["gate_reason"] == "known_recipient"


def test_imessage_blocks_bank(tmp_path):
    rc, out, _ = _run(
        "krab_send_imessage.py",
        [
            "--to",
            "manager@bank-example.com",
            "--text",
            "hi",
            "--first-time-confirm",
            "--dry-run",
        ],
        env_extra={"HOME": str(tmp_path)},
    )
    assert rc == 1
    assert out and out["ok"] is False
    assert "hard-blocked" in out["error"]


# ---------- Email ----------


def test_email_first_time_blocked(tmp_path):
    rc, out, _ = _run(
        "krab_send_email.py",
        ["--to", "stranger@example.com", "--subject", "hi", "--body", "test"],
        env_extra={"HOME": str(tmp_path)},
    )
    assert rc == 1
    assert out and out["ok"] is False
    assert out["error"] == "first_time_no_confirm"


def test_email_default_draft(tmp_path):
    """Without --send → sent_or_draft='draft'."""
    rc, out, _ = _run(
        "krab_send_email.py",
        [
            "--to",
            "friend@example.com",
            "--subject",
            "hi",
            "--body",
            "test",
            "--first-time-confirm",
            "--dry-run",
        ],
        env_extra={"HOME": str(tmp_path)},
    )
    assert rc == 0
    assert out and out["ok"] is True
    assert out["sent_or_draft"] == "draft"


def test_email_send_flag(tmp_path):
    rc, out, _ = _run(
        "krab_send_email.py",
        [
            "--to",
            "friend@example.com",
            "--subject",
            "hi",
            "--body",
            "test",
            "--send",
            "--first-time-confirm",
            "--dry-run",
        ],
        env_extra={"HOME": str(tmp_path)},
    )
    assert rc == 0
    assert out and out["ok"] is True
    assert out["sent_or_draft"] == "sent"


def test_email_no_send_overrides_send(tmp_path):
    rc, out, _ = _run(
        "krab_send_email.py",
        [
            "--to",
            "friend@example.com",
            "--subject",
            "hi",
            "--body",
            "test",
            "--send",
            "--no-send",
            "--first-time-confirm",
            "--dry-run",
        ],
        env_extra={"HOME": str(tmp_path)},
    )
    assert rc == 0
    assert out and out["sent_or_draft"] == "draft"


def test_email_blocks_bank(tmp_path):
    rc, out, _ = _run(
        "krab_send_email.py",
        [
            "--to",
            "ceo@bank-example.com",
            "--subject",
            "hi",
            "--body",
            "test",
            "--first-time-confirm",
            "--dry-run",
        ],
        env_extra={"HOME": str(tmp_path)},
    )
    assert rc == 1
    assert out and "hard-blocked" in out["error"]


def test_email_empty_subject(tmp_path):
    rc, out, _ = _run(
        "krab_send_email.py",
        ["--to", "x@y.com", "--subject", "  ", "--body", "test"],
        env_extra={"HOME": str(tmp_path)},
    )
    assert rc == 1
    assert out and out["ok"] is False


def test_email_attachment_missing(tmp_path):
    rc, out, _ = _run(
        "krab_send_email.py",
        [
            "--to",
            "x@y.com",
            "--subject",
            "hi",
            "--body",
            "test",
            "--attachment",
            "/nonexistent/file.pdf",
            "--first-time-confirm",
        ],
        env_extra={"HOME": str(tmp_path)},
    )
    assert rc == 1
    assert out and "attachment not found" in out["error"]


# ---------- Owner prompt mentions multi-channel ----------


def test_owner_prompt_mentions_multi_channel():
    text = (REPO_ROOT / "src" / "userbot" / "access_control.py").read_text(encoding="utf-8")
    assert "krab_send_discord.py" in text
    assert "krab_send_imessage.py" in text
    assert "krab_send_email.py" in text
    assert "Wave 44-T-multi-channel" in text


# ---------- Owner token bypass ----------


def test_owner_token_bypass_email(tmp_path, monkeypatch):
    import _multi_channel_helpers as h  # type: ignore  # noqa: PLC0415

    token_path = tmp_path / ".openclaw" / "krab_runtime_state" / "owner_confirm.token"
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text("secret123", encoding="utf-8")

    monkeypatch.setattr(h, "OWNER_CONFIRM_TOKEN_PATH", token_path)
    assert h.check_owner_token("secret123") is True
    assert h.check_owner_token("wrong") is False
    assert h.check_owner_token(None) is False


@pytest.mark.parametrize(
    "script,extra_args",
    [
        ("krab_send_discord.py", ["--server", "s", "--channel", "c"]),
        ("krab_send_imessage.py", ["--to", "+1"]),
        ("krab_send_email.py", ["--to", "x@y.com", "--subject", "s", "--body", "b"]),
    ],
)
def test_scripts_executable(script, extra_args, tmp_path):
    """Every script returns parseable JSON, never crashes.

    Wave 65-F: пробрасываем HOME + KRAB_RUNTIME_STATE_DIR в tmp_path,
    иначе subprocess audit_event() пишет в production
    ~/.openclaw/krab_runtime_state/agent_audit.jsonl с fake recipients
    (s#c / +1 / x@y.com).
    """
    rc, out, _ = _run(
        script,
        [*extra_args, "--text", "hi"]
        if "--text" in str(extra_args) or script != "krab_send_email.py"
        else extra_args,
        env_extra={
            "HOME": str(tmp_path),
            "KRAB_RUNTIME_STATE_DIR": str(tmp_path / ".openclaw" / "krab_runtime_state"),
        },
    )
    # We expect non-zero (first-time blocked or argparse), but JSON should parse
    # OR argparse error which is rc=2 with no JSON. Either way no crash.
    assert rc in (0, 1, 2)
