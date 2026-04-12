"""
Проверки ownership-диагностики текущей macOS-учётки.

Нужны, чтобы multi-account guard не начал снова путать свой runtime с чужим
и не возвращал ложный success на живом `USER2` контуре.
"""

from __future__ import annotations

from pathlib import Path

import pytest

try:
    from scripts.check_current_account_runtime import ListenerRow, build_runtime_report
except (ImportError, ModuleNotFoundError, FileNotFoundError):
    pytest.skip("scripts.check_current_account_runtime not available", allow_module_level=True)


def test_build_runtime_report_detects_foreign_runtime() -> None:
    """Чужой listener и чужой inbox_state должны давать foreign_runtime_detected."""

    def listener_provider(port: int) -> list[ListenerRow]:
        if port == 8080:
            return [ListenerRow(command="Python", pid=101, user="USER2", port=8080)]
        if port == 18789:
            return [ListenerRow(command="openclaw-gateway", pid=202, user="USER2", port=18789)]
        return []

    def http_json(url: str) -> dict[str, object]:
        if url.endswith("/api/health/lite"):
            return {
                "ok": True,
                "status": 200,
                "json": {
                    "ok": True,
                    "inbox_summary": {
                        "state_path": "/Users/USER2/.openclaw/krab_runtime_state/inbox_state.json",
                        "operator_id": "USER2",
                    },
                },
            }
        if url.endswith("/health"):
            return {"ok": True, "status": 200, "json": {"ok": True, "status": "live"}}
        return {"ok": False, "status": None, "error": "unreachable"}

    def voice_gateway_cmds_provider() -> list[str]:
        return [
            (
                "/opt/homebrew/bin/python -m uvicorn app.main:app "
                "--app-dir /Users/USER2/Antigravity_AGENTS/Krab Voice Gateway "
                "--host 127.0.0.1 --port 8090"
            )
        ]

    report = build_runtime_report(
        current_user="pablito",
        home_dir=Path("/Users/pablito"),
        listener_provider=listener_provider,
        voice_gateway_cmds_provider=voice_gateway_cmds_provider,
        http_json=http_json,
    )

    assert report["ownership"]["foreign_runtime_detected"] is True
    assert report["ownership"]["verdict"] == "foreign_runtime_detected"
    assert report["ports"]["8080"]["owned_by_current_user"] is False
    assert report["ownership"]["inbox_state_matches_current_home"] is False
    assert report["ownership"]["voice_gateway_foreign_detected"] is True
    assert report["voice_gateway"]["owner_user"] == "USER2"


def test_build_runtime_report_accepts_current_account_runtime() -> None:
    """Свой runtime и свой inbox_state должны давать зелёный verdict."""

    def listener_provider(port: int) -> list[ListenerRow]:
        if port == 8080:
            return [ListenerRow(command="Python", pid=111, user="pablito", port=8080)]
        if port == 18789:
            return [ListenerRow(command="openclaw-gateway", pid=222, user="pablito", port=18789)]
        return []

    def http_json(url: str) -> dict[str, object]:
        if url.endswith("/api/health/lite"):
            return {
                "ok": True,
                "status": 200,
                "json": {
                    "ok": True,
                    "inbox_summary": {
                        "state_path": "/Users/pablito/.openclaw/krab_runtime_state/inbox_state.json",
                        "operator_id": "pablito",
                    },
                },
            }
        if url.endswith("/health"):
            return {"ok": True, "status": 200, "json": {"ok": True, "status": "live"}}
        return {"ok": False, "status": None, "error": "unreachable"}

    def voice_gateway_cmds_provider() -> list[str]:
        return [
            (
                "/opt/homebrew/bin/python -m uvicorn app.main:app "
                "--app-dir /Users/pablito/Antigravity_AGENTS/Krab Voice Gateway "
                "--host 127.0.0.1 --port 8090"
            )
        ]

    report = build_runtime_report(
        current_user="pablito",
        home_dir=Path("/Users/pablito"),
        listener_provider=listener_provider,
        voice_gateway_cmds_provider=voice_gateway_cmds_provider,
        http_json=http_json,
    )

    assert report["ownership"]["foreign_runtime_detected"] is False
    assert report["ownership"]["verdict"] == "current_account_runtime_active"
    assert report["ports"]["8080"]["owned_by_current_user"] is True
    assert report["ownership"]["inbox_state_matches_current_home"] is True
    assert report["ownership"]["voice_gateway_foreign_detected"] is False
    assert report["voice_gateway"]["owner_user"] == "pablito"
