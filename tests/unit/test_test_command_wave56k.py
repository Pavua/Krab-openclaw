# -*- coding: utf-8 -*-
"""Wave 56-K: unit tests for !test command — on-demand pytest subset runner.

Tests:
  1. test_runs_e2e_smoke_by_default          — no args → e2e_smoke_wave53c path
  2. test_full_runs_ci_subset                — "full" arg → tests/unit/ path
  3. test_pattern_filter                     — arbitrary arg → -k pattern
  4. test_subprocess_timeout_enforced        — hanging process killed after timeout
  5. test_owner_only_rejects                 — non-owner receives 403 error
  6. test_summary_parsed_correctly           — pytest output lines parsed to counts
  7. test_handles_pytest_failure_gracefully  — non-zero exit code reported
  8. test_max_args_validated                 — 30+ token args rejected

Hard constraints:
- Pure unit: mock subprocess everywhere, no live pytest invocations.
- asyncio_mode = "auto" from pyproject.toml.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_message(text: str = "!test") -> MagicMock:
    """Stub Pyrogram Message."""
    msg = MagicMock()
    msg.text = text
    msg.reply = AsyncMock()
    msg.from_user = MagicMock()
    msg.from_user.id = 999_111_001
    return msg


def _make_bot(*, is_owner: bool = True) -> MagicMock:
    """Stub KraabUserbot with _get_access_profile."""
    from src.core.access_control import AccessLevel

    bot = MagicMock()
    profile = MagicMock()
    profile.level = AccessLevel.OWNER if is_owner else AccessLevel.GUEST
    bot._get_access_profile = MagicMock(return_value=profile)
    return bot


def _make_proc(
    returncode: int = 0,
    stdout: bytes = b"",
    stderr: bytes = b"",
    *,
    hang: bool = False,
) -> AsyncMock:
    """Stub asyncio.Process returned by create_subprocess_exec."""
    proc = AsyncMock()
    proc.returncode = returncode
    proc.kill = MagicMock()

    if hang:
        async def _hang_communicate():
            await asyncio.sleep(9999)  # never returns in tests (wait_for cuts it)

        proc.communicate = _hang_communicate
    else:
        proc.communicate = AsyncMock(return_value=(stdout, stderr))

    return proc


# ---------------------------------------------------------------------------
# Helper: import handler + helper from observability_commands
# ---------------------------------------------------------------------------


def _import_handler():
    from src.handlers.commands.observability_commands import (
        _run_pytest_subset,
        handle_test,
    )
    return handle_test, _run_pytest_subset


# ---------------------------------------------------------------------------
# 1. Default (no args) → e2e smoke path
# ---------------------------------------------------------------------------


async def test_runs_e2e_smoke_by_default() -> None:
    """!test with no args invokes pytest on e2e_smoke_wave53c.py."""
    handle_test, _run_pytest_subset = _import_handler()

    bot = _make_bot()
    msg = _make_message("!test")

    captured_args: list[list[str]] = []

    async def _fake_run(pytest_args: list[str], *, timeout: int = 60) -> dict:
        captured_args.append(pytest_args)
        return {
            "ok": True,
            "duration_sec": 0.42,
            "passed": 9,
            "failed": 0,
            "errors": 0,
            "summary_text": "9 passed in 0.42s",
            "_stdout_tail": "9 passed in 0.42s",
        }

    with patch(
        "src.handlers.commands.observability_commands._run_pytest_subset",
        side_effect=_fake_run,
    ):
        await handle_test(bot, msg)

    assert captured_args, "Should have called _run_pytest_subset"
    args = captured_args[0]
    assert any("test_e2e_smoke_wave53c" in a for a in args), (
        f"Expected e2e smoke file in args, got: {args}"
    )
    # Check reply was sent
    msg.reply.assert_called()
    last_reply = msg.reply.call_args_list[-1][0][0]
    assert "Test Run" in last_reply
    assert "9" in last_reply


# ---------------------------------------------------------------------------
# 2. "full" arg → unit CI subset
# ---------------------------------------------------------------------------


async def test_full_runs_ci_subset() -> None:
    """!test full invokes pytest on tests/unit/."""
    handle_test, _ = _import_handler()

    bot = _make_bot()
    msg = _make_message("!test full")

    captured_args: list[list[str]] = []

    async def _fake_run(pytest_args: list[str], *, timeout: int = 60) -> dict:
        captured_args.append(pytest_args)
        return {
            "ok": True,
            "duration_sec": 12.5,
            "passed": 350,
            "failed": 0,
            "errors": 0,
            "summary_text": "350 passed in 12.50s",
            "_stdout_tail": "350 passed in 12.50s",
        }

    with patch(
        "src.handlers.commands.observability_commands._run_pytest_subset",
        side_effect=_fake_run,
    ):
        await handle_test(bot, msg)

    assert captured_args
    args = captured_args[0]
    assert any("tests/unit" in a or "unit" in a for a in args), (
        f"Expected tests/unit path in args, got: {args}"
    )
    last_reply = msg.reply.call_args_list[-1][0][0]
    assert "unit" in last_reply.lower()


# ---------------------------------------------------------------------------
# 3. Pattern filter → -k
# ---------------------------------------------------------------------------


async def test_pattern_filter() -> None:
    """!test memory_engine invokes pytest -k memory_engine."""
    handle_test, _ = _import_handler()

    bot = _make_bot()
    msg = _make_message("!test memory_engine")

    captured_args: list[list[str]] = []

    async def _fake_run(pytest_args: list[str], *, timeout: int = 60) -> dict:
        captured_args.append(pytest_args)
        return {
            "ok": True,
            "duration_sec": 1.1,
            "passed": 7,
            "failed": 0,
            "errors": 0,
            "summary_text": "7 passed in 1.1s",
            "_stdout_tail": "7 passed in 1.1s",
        }

    with patch(
        "src.handlers.commands.observability_commands._run_pytest_subset",
        side_effect=_fake_run,
    ):
        await handle_test(bot, msg)

    assert captured_args
    args = captured_args[0]
    assert "-k" in args, f"-k flag missing from args: {args}"
    k_idx = args.index("-k")
    assert "memory_engine" in args[k_idx + 1], f"Pattern not passed after -k: {args}"


# ---------------------------------------------------------------------------
# 4. Subprocess timeout enforced
# ---------------------------------------------------------------------------


async def test_subprocess_timeout_enforced() -> None:
    """Hanging pytest process is killed and returns timeout result."""
    _, _run_pytest_subset = _import_handler()

    hang_proc = _make_proc(hang=True)

    with (
        patch(
            "src.handlers.commands.observability_commands._resolve_pytest_bin",
            return_value=["pytest"],
        ),
        patch(
            "asyncio.create_subprocess_exec",
            return_value=hang_proc,
        ),
        patch(
            "src.handlers.commands.observability_commands.clean_subprocess_env",
            return_value={},
        ) if False else patch(  # clean_subprocess_env is imported locally
            "src.core.subprocess_env.clean_subprocess_env",
            return_value={},
        ),
    ):
        result = await _run_pytest_subset(["tests/unit/"], timeout=1)

    assert result["ok"] is False
    assert "TIMEOUT" in result["summary_text"] or "timeout" in result["summary_text"].lower()
    hang_proc.kill.assert_called_once()


# ---------------------------------------------------------------------------
# 5. Owner-only gate rejects non-owner
# ---------------------------------------------------------------------------


async def test_owner_only_rejects() -> None:
    """Non-owner user receives UserInputError / 🔒 message."""
    from src.core.exceptions import UserInputError

    handle_test, _ = _import_handler()

    bot = _make_bot(is_owner=False)
    msg = _make_message("!test")

    with pytest.raises(UserInputError) as exc_info:
        await handle_test(bot, msg)

    assert "🔒" in str(exc_info.value.user_message) or "owner" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# 6. Summary parsed correctly from pytest output
# ---------------------------------------------------------------------------


async def test_summary_parsed_correctly() -> None:
    """Mock pytest stdout → counts extracted correctly."""
    _, _run_pytest_subset = _import_handler()

    stdout_text = (
        "tests/unit/test_foo.py ........F.  [100%]\n"
        "\n"
        "FAILED tests/unit/test_foo.py::test_bar — assert False\n"
        "9 passed, 1 failed in 3.21s\n"
    ).encode()

    ok_proc = _make_proc(returncode=1, stdout=stdout_text, stderr=b"")

    with (
        patch(
            "src.handlers.commands.observability_commands._resolve_pytest_bin",
            return_value=["pytest"],
        ),
        patch("asyncio.create_subprocess_exec", return_value=ok_proc),
        patch("src.core.subprocess_env.clean_subprocess_env", return_value={}),
    ):
        result = await _run_pytest_subset(["tests/unit/"], timeout=30)

    assert result["passed"] == 9, f"Expected 9 passed, got {result['passed']}"
    assert result["failed"] == 1, f"Expected 1 failed, got {result['failed']}"
    assert result["errors"] == 0
    assert result["ok"] is False  # returncode=1 + failed>0


# ---------------------------------------------------------------------------
# 7. Non-zero exit code reported gracefully
# ---------------------------------------------------------------------------


async def test_handles_pytest_failure_gracefully() -> None:
    """Non-zero exit code from pytest → ok=False, error not raised."""
    _, _run_pytest_subset = _import_handler()

    stdout_text = b"2 failed, 5 passed in 2.00s\n"
    fail_proc = _make_proc(returncode=1, stdout=stdout_text, stderr=b"")

    with (
        patch(
            "src.handlers.commands.observability_commands._resolve_pytest_bin",
            return_value=["pytest"],
        ),
        patch("asyncio.create_subprocess_exec", return_value=fail_proc),
        patch("src.core.subprocess_env.clean_subprocess_env", return_value={}),
    ):
        result = await _run_pytest_subset(["tests/unit/"], timeout=30)

    assert result["ok"] is False
    assert result["failed"] == 2
    assert result["passed"] == 5
    # No exception raised — result dict returned cleanly
    assert "summary_text" in result


# ---------------------------------------------------------------------------
# 8. Too many args rejected
# ---------------------------------------------------------------------------


async def test_max_args_validated() -> None:
    """!test with 30+ space-separated tokens raises UserInputError."""
    from src.core.exceptions import UserInputError

    handle_test, _ = _import_handler()

    bot = _make_bot()
    long_arg = " ".join(f"token{i}" for i in range(35))  # 35 tokens
    msg = _make_message(f"!test {long_arg}")

    with pytest.raises(UserInputError) as exc_info:
        await handle_test(bot, msg)

    assert "аргумент" in str(exc_info.value.user_message).lower() or "args" in str(
        exc_info.value.user_message
    ).lower()


# ---------------------------------------------------------------------------
# 9. _resolve_pytest_bin: prefers project venv, fallbacks to sys.executable
# ---------------------------------------------------------------------------


def test_resolve_pytest_bin_prefers_venv(tmp_path: pytest.TempPathFactory) -> None:
    """_resolve_pytest_bin returns venv binary when it exists."""
    from src.handlers.commands.observability_commands import (
        _resolve_pytest_bin,
    )

    # Patch candidates list to use a real tmp file
    fake_bin = tmp_path / "pytest"
    fake_bin.touch()
    fake_bin.chmod(0o755)

    with patch(
        "src.handlers.commands.observability_commands._PYTEST_CANDIDATES",
        [fake_bin],
    ):
        result = _resolve_pytest_bin()

    assert result == [str(fake_bin)], f"Expected [{fake_bin}], got {result}"


def test_resolve_pytest_bin_fallback_to_sys_executable() -> None:
    """_resolve_pytest_bin falls back to sys.executable -m pytest when no venv found."""
    import sys

    from src.handlers.commands.observability_commands import _resolve_pytest_bin

    with patch(
        "src.handlers.commands.observability_commands._PYTEST_CANDIDATES",
        [],  # no candidates
    ):
        result = _resolve_pytest_bin()

    assert result[0] == sys.executable
    assert "-m" in result
    assert "pytest" in result
