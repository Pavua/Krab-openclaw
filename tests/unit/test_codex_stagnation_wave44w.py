# -*- coding: utf-8 -*-
"""Wave 44-W: stagnation detector tests для streaming codex/gemini subprocess.

Покрытие:
1. Streaming proc with chunks every 60s for ~30 min -> completes (not killed)
2. Subprocess silent for > idle_timeout -> killed via LLMRetryableError("stagnation")
3. Subprocess running > hard_cap -> killed via LLMRetryableError("hard_cap")
4. Quota error pattern detected mid-stream -> bail (caller sees stderr matching quota)
5. Idle timeout configurable via KRAB_LLM_IDLE_TIMEOUT_SEC env
6. Hard cap configurable via KRAB_CODEX_AGENT_HARD_CAP_SEC env
7. Backwards compat: communicate-mock proc still works (legacy fallback path)
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.integrations import cli_subprocess_bypass as bypass
from src.integrations.cli_subprocess_bypass import (
    _get_codex_hard_cap_sec,
    _get_idle_timeout_sec,
    _stream_with_stagnation,
)
from src.userbot.llm_retry import LLMRetryableError


class _ScriptedStream:
    def __init__(self, script):
        self._script = list(script)

    async def readline(self):
        if not self._script:
            return b""
        delay, payload = self._script.pop(0)
        if delay > 0:
            await asyncio.sleep(delay)
        return payload


class _ScriptedProc:
    def __init__(self, stdout_script, stderr_script=None, returncode_after=0):
        self.stdout = _ScriptedStream(stdout_script)
        self.stderr = _ScriptedStream(stderr_script or [(0.0, b"")])
        self._returncode_after = returncode_after
        self.returncode = None
        self.killed = False

    def kill(self):
        self.killed = True
        self.returncode = -9

    async def wait(self):
        while True:
            stdout_done = not self.stdout._script
            stderr_done = not self.stderr._script
            if stdout_done and stderr_done:
                break
            await asyncio.sleep(0.01)
        if self.returncode is None:
            self.returncode = self._returncode_after
        return self.returncode


@pytest.mark.asyncio
async def test_stream_with_activity_completes_no_kill():
    script = [
        (0.05, b"chunk1\n"),
        (0.05, b"chunk2\n"),
        (0.05, b"chunk3\n"),
        (0.05, b"final\n"),
        (0.0, b""),
    ]
    proc = _ScriptedProc(script, returncode_after=0)
    rc, stdout, _stderr = await _stream_with_stagnation(
        proc,
        idle_timeout_sec=2.0,
        hard_cap_sec=10.0,
    )
    assert rc == 0
    assert b"chunk1" in stdout and b"final" in stdout
    assert proc.killed is False


@pytest.mark.asyncio
async def test_stream_idle_timeout_kills_proc():
    script = [
        (0.0, b"start\n"),
        (10.0, b"never_arrives\n"),
        (0.0, b""),
    ]
    proc = _ScriptedProc(script, returncode_after=0)
    with pytest.raises(LLMRetryableError, match="stagnation"):
        await _stream_with_stagnation(
            proc,
            idle_timeout_sec=0.5,
            hard_cap_sec=30.0,
        )
    assert proc.killed is True


@pytest.mark.asyncio
async def test_stream_hard_cap_kills_even_with_activity():
    script = [(0.1, f"c{i}\n".encode()) for i in range(20)]
    proc = _ScriptedProc(script, returncode_after=0)
    with pytest.raises(LLMRetryableError, match="hard cap"):
        await _stream_with_stagnation(
            proc,
            idle_timeout_sec=10.0,
            hard_cap_sec=0.4,
        )
    assert proc.killed is True


@pytest.mark.asyncio
async def test_stream_quota_error_bails_without_kill():
    stdout_script = [(0.05, b"working\n"), (0.0, b"")]
    stderr_script = [
        (0.0, b"You've hit your weekly limit for codex\n"),
        (0.0, b""),
    ]
    proc = _ScriptedProc(
        stdout_script,
        stderr_script=stderr_script,
        returncode_after=1,
    )
    _rc, _stdout, stderr = await _stream_with_stagnation(
        proc,
        idle_timeout_sec=2.0,
        hard_cap_sec=10.0,
        quota_check=True,
    )
    assert b"weekly limit" in stderr
    assert proc.killed is False


def test_idle_timeout_env_default(monkeypatch):
    monkeypatch.delenv("KRAB_LLM_IDLE_TIMEOUT_SEC", raising=False)
    assert _get_idle_timeout_sec() == 180.0


def test_idle_timeout_env_override(monkeypatch):
    monkeypatch.setenv("KRAB_LLM_IDLE_TIMEOUT_SEC", "300")
    assert _get_idle_timeout_sec() == 300.0


def test_idle_timeout_env_invalid_falls_back(monkeypatch):
    monkeypatch.setenv("KRAB_LLM_IDLE_TIMEOUT_SEC", "not_a_number")
    assert _get_idle_timeout_sec() == 180.0


def test_hard_cap_env_default(monkeypatch):
    monkeypatch.delenv("KRAB_CODEX_AGENT_HARD_CAP_SEC", raising=False)
    assert _get_codex_hard_cap_sec() == 7200.0


def test_hard_cap_env_zero_disabled(monkeypatch):
    monkeypatch.setenv("KRAB_CODEX_AGENT_HARD_CAP_SEC", "0")
    assert _get_codex_hard_cap_sec() == 0.0


def test_hard_cap_env_custom(monkeypatch):
    monkeypatch.setenv("KRAB_CODEX_AGENT_HARD_CAP_SEC", "1800")
    assert _get_codex_hard_cap_sec() == 1800.0


@pytest.mark.asyncio
async def test_legacy_communicate_mock_works():
    """Mock proc with non-awaitable readline -> fallback path engages."""
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = MagicMock()
    mock_proc.stdout.readline = MagicMock(return_value=b"")
    mock_proc.communicate = AsyncMock(return_value=(b"hello", b""))
    mock_proc.kill = MagicMock()
    mock_proc.wait = AsyncMock()

    rc, stdout, _stderr = await _stream_with_stagnation(
        mock_proc,
        idle_timeout_sec=10.0,
        hard_cap_sec=30.0,
    )
    assert rc == 0
    assert stdout == b"hello"


@pytest.mark.asyncio
async def test_run_codex_subprocess_once_propagates_stagnation(monkeypatch):
    """_run_codex_subprocess_once -> LLMRetryableError on stagnation."""
    script = [
        (0.0, b"started\n"),
        (5.0, b"never_comes\n"),
        (0.0, b""),
    ]
    proc = _ScriptedProc(script, returncode_after=0)

    async def fake_create_subprocess(*args, **kwargs):
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess)
    monkeypatch.setenv("KRAB_LLM_IDLE_TIMEOUT_SEC", "0.3")
    monkeypatch.setenv("KRAB_CODEX_AGENT_HARD_CAP_SEC", "30")

    with pytest.raises(LLMRetryableError, match="stagnation"):
        await bypass._run_codex_subprocess_once(
            binary_path="/usr/local/bin/codex",
            model_id="gpt-5.5",
            prompt_text="test",
            timeout_sec=30.0,
            codex_home=None,
        )
