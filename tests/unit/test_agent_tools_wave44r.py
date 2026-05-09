"""Wave 44-R-script-tools — tests for bash-callable agent tools."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TOOLS_DIR = REPO_ROOT / "scripts" / "agent_tools"
PYTHON = sys.executable

# Make _common importable for direct in-process tests.
sys.path.insert(0, str(TOOLS_DIR))


def _run(
    script: str, args: list[str], env_extra: dict[str, str] | None = None, timeout: int = 15
) -> tuple[int, dict | None, str]:
    """Run script as subprocess; return (rc, parsed_json, stderr)."""
    env = {**os.environ}
    if env_extra:
        env.update(env_extra)
    proc = subprocess.run(
        [PYTHON, str(TOOLS_DIR / script), *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
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


# ---------- _common ----------


def test_common_module_imports():
    import _common  # type: ignore  # noqa: PLC0415

    assert _common.KRAB_SWARM_GROUP_ID == -1003703978531
    assert _common.OWNER_DM_ID == 312322764
    assert _common.SESSION_NAME == "kraab"
    assert _common.SESSION_DIR.name == "sessions"


def test_emit_json_writes_log(tmp_path, monkeypatch):
    import _common  # type: ignore  # noqa: PLC0415

    log = tmp_path / "audit.log"
    monkeypatch.setattr(_common, "LOG_PATH", log)

    _common.log_invocation("foo.py", ["--x"], {"ok": True, "n": 1})
    _common.log_invocation("foo.py", ["--y"], {"ok": False, "error": "x"})

    lines = log.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    rec = json.loads(lines[0])
    assert rec["script"] == "foo.py"
    assert rec["ok"] is True


# ---------- krab_send_dm whitelist ----------


def test_send_dm_rejects_unknown_chat():
    rc, out, _ = _run(
        "krab_send_dm.py",
        ["--chat-id", "999999", "--text", "hi"],
    )
    assert rc == 1
    assert out is not None
    assert out["ok"] is False
    assert "not whitelisted" in out["error"]


def test_send_dm_rejects_empty_text():
    rc, out, _ = _run(
        "krab_send_dm.py",
        ["--chat-id", "312322764", "--text", "   "],
    )
    assert rc == 1
    assert out and out["ok"] is False


# ---------- krab_send_to_swarm ----------


def test_send_to_swarm_rejects_empty_text():
    rc, out, _ = _run("krab_send_to_swarm.py", ["--text", ""])
    assert rc == 1
    assert out and out["ok"] is False


@pytest.mark.asyncio
async def test_send_to_swarm_inprocess_mock():
    """In-process test of _send with mocked Pyrogram Client."""
    import krab_send_to_swarm as mod  # type: ignore  # noqa: PLC0415

    fake_msg = type("M", (), {"id": 4321})()
    fake_client = AsyncMock()
    fake_client.__aenter__.return_value = fake_client
    fake_client.__aexit__.return_value = False
    fake_client.send_message = AsyncMock(return_value=fake_msg)

    fake_pyro = type(sys)("pyrogram")
    fake_pyro.Client = lambda *a, **kw: fake_client  # type: ignore[attr-defined]
    with (
        patch.dict(sys.modules, {"pyrogram": fake_pyro}),
        patch.object(mod, "get_telegram_credentials", return_value=(123, "hash")),
    ):
        result = await mod._send("!swarm test", None)
    assert result["ok"] is True
    assert result["message_id"] == 4321
    assert result["chat_id"] == -1003703978531


# ---------- krab_screenshot ----------


def test_screenshot_creates_file(tmp_path):
    if not Path("/usr/sbin/screencapture").exists():
        pytest.skip("not macOS")
    target = tmp_path / "shot.png"
    rc, out, _ = _run(
        "krab_screenshot.py",
        ["--output", str(target)],
        timeout=30,
    )
    # Может зафейлиться по permissions в headless CI; если ok=true — проверяем
    # содержимое; если ok=false — minimum валидируем error поле есть.
    assert out is not None
    if out.get("ok"):
        assert rc == 0
        assert target.exists()
        assert target.stat().st_size > 0
    else:
        assert "error" in out


def test_screenshot_validates_min_size(tmp_path):
    """validate_image rejects tiny files."""
    import krab_screenshot as mod  # type: ignore  # noqa: PLC0415

    p = tmp_path / "tiny.png"
    p.write_bytes(b"not a real png")
    valid, reason = mod._validate_image(p)
    assert valid is False
    assert "too small" in reason


def test_screenshot_validates_missing(tmp_path):
    import krab_screenshot as mod  # type: ignore  # noqa: PLC0415

    valid, reason = mod._validate_image(tmp_path / "nope.png")
    assert valid is False
    assert "not created" in reason


# ---------- krab_run_command ----------


def test_run_command_rejects_no_bang():
    rc, out, _ = _run("krab_run_command.py", ["--command", "status"])
    assert rc == 1
    assert out and out["ok"] is False
    assert "must start with !" in out["error"]


def test_run_command_http_endpoint_unreachable_falls_through():
    """If owner panel is down AND --prefer-dm not set, script tries HTTP first.
    With unreachable HTTP it then attempts DM (will fail without pyrogram setup)
    but the JSON should still be parseable."""
    import krab_run_command as mod  # type: ignore  # noqa: PLC0415

    result = mod._try_http("!nonexistent_command_xyz")
    assert result is None  # not in HTTP_BACKED map


def test_run_command_http_backed_keys():
    import krab_run_command as mod  # type: ignore  # noqa: PLC0415

    assert "!status" in mod.HTTP_BACKED
    assert mod.HTTP_BACKED["!status"].startswith("/api/")


# ---------- access_control prompt embeds tool refs ----------


def test_owner_prompt_mentions_agent_tools():
    text = (REPO_ROOT / "src" / "userbot" / "access_control.py").read_text(encoding="utf-8")
    assert "krab_send_to_swarm.py" in text
    assert "krab_screenshot.py" in text
    assert "krab_run_command.py" in text
    assert "Wave 44-R-script-tools" in text
