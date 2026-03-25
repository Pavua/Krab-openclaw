# -*- coding: utf-8 -*-
"""Unit-тесты для truthful reload-диагностики секретов OpenClaw."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from src.core.openclaw_secrets_runtime import (
    get_openclaw_cli_runtime_status,
    reload_openclaw_secrets,
)


class _FakeProcess:
    """Минимальный async subprocess для проверки reload-контракта."""

    def __init__(self, returncode: int = 0, stdout: bytes = b"ok\n") -> None:
        self.returncode = returncode
        self._stdout = stdout

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._stdout, b""


@pytest.mark.asyncio
async def test_reload_openclaw_secrets_reports_missing_cli() -> None:
    with patch(
        "src.core.openclaw_secrets_runtime._resolve_openclaw_cli",
        return_value={"ok": False, "error": "cli_not_found", "path": "", "source": "", "checked": []},
    ):
        result = await reload_openclaw_secrets()

    assert result["ok"] is False
    assert result["exit_code"] == 127
    assert result["error"] == "cli_not_found"
    assert result["output"].startswith("secrets_reload_cli_not_found:")


@pytest.mark.asyncio
async def test_reload_openclaw_secrets_reports_non_executable_cli() -> None:
    with patch(
        "src.core.openclaw_secrets_runtime._resolve_openclaw_cli",
        return_value={
            "ok": False,
            "error": "cli_not_executable",
            "path": "/opt/homebrew/bin/openclaw",
            "source": "fallback",
            "checked": [{"path": "/opt/homebrew/bin/openclaw", "source": "fallback", "reason": "not_executable"}],
        },
    ):
        result = await reload_openclaw_secrets()

    assert result["ok"] is False
    assert result["exit_code"] == 126
    assert result["error"] == "cli_not_executable"
    assert result["cli_path"] == "/opt/homebrew/bin/openclaw"
    assert "source=fallback" in result["output"]


@pytest.mark.asyncio
async def test_reload_openclaw_secrets_uses_resolved_cli_path() -> None:
    process = _FakeProcess(returncode=0, stdout=b"reloaded\n")

    with patch(
        "src.core.openclaw_secrets_runtime._resolve_openclaw_cli",
        return_value={"ok": True, "path": "/custom/bin/openclaw", "source": "env:OPENCLAW_BIN", "checked": []},
    ):
        with patch(
            "src.core.openclaw_secrets_runtime.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=process),
        ) as create_exec:
            result = await reload_openclaw_secrets()

    assert result["ok"] is True
    assert result["cli_path"] == "/custom/bin/openclaw"
    assert result["cli_source"] == "env:OPENCLAW_BIN"
    create_exec.assert_awaited_once()
    assert create_exec.await_args.args[:3] == ("/custom/bin/openclaw", "secrets", "reload")


def test_get_openclaw_cli_runtime_status_returns_safe_failure_payload() -> None:
    with patch(
        "src.core.openclaw_secrets_runtime._resolve_openclaw_cli",
        return_value={
            "ok": False,
            "error": "cli_not_executable",
            "path": "/opt/homebrew/bin/openclaw",
            "source": "fallback",
            "checked": [{"path": "/opt/homebrew/bin/openclaw", "source": "fallback", "reason": "not_executable"}],
        },
    ):
        status = get_openclaw_cli_runtime_status()

    assert status == {
        "can_reload": False,
        "error": "cli_not_executable",
        "cli_path": "/opt/homebrew/bin/openclaw",
        "cli_source": "fallback",
        "checked": [{"path": "/opt/homebrew/bin/openclaw", "source": "fallback", "reason": "not_executable"}],
    }
