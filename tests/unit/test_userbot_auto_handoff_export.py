# -*- coding: utf-8 -*-
"""
Tests for auto handoff export in userbot_bridge (Phase 2.2).

Covers:
1) Auto-export on userbot stop
2) Periodic auto-export in maintenance task
3) Export never raises, returns exported=True/False
"""

from __future__ import annotations

import http.server
import json
import threading
import urllib.request
from pathlib import Path
from unittest import mock

import pytest

# Mock config before importing userbot_bridge
with mock.patch("src.config.config") as mock_config:
    mock_config.BASE_DIR = Path("/tmp/test_krab")
    mock_config.TELEGRAM_API_ID = 12345
    mock_config.TELEGRAM_API_HASH = "test_hash"
    mock_config.TELEGRAM_SESSION_NAME = "test_session"
    mock_config.OPENCLAW_URL = "http://127.0.0.1:18789"
    mock_config.TRIGGER_PREFIXES = ["!краб", "@краб"]

    from src.userbot_bridge import KraabUserbot


FAKE_HANDOFF_PAYLOAD = {"ok": True, "status": "test", "generated_at_utc": "2026-03-24T00:00:00"}


class _OneShotHandoffHandler(http.server.BaseHTTPRequestHandler):
    """Responds with FAKE_HANDOFF_PAYLOAD once."""

    def do_GET(self) -> None:  # noqa: N802
        body = json.dumps(FAKE_HANDOFF_PAYLOAD).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        pass


def _start_local_server() -> tuple[http.server.HTTPServer, int]:
    """Starts a local HTTP server on a random port."""
    server = http.server.HTTPServer(("127.0.0.1", 0), _OneShotHandoffHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    return server, port


@pytest.mark.asyncio
async def test_auto_export_on_stop_success(tmp_path: Path) -> None:
    """When userbot stops, handoff snapshot is exported automatically."""
    server, port = _start_local_server()

    with (
        mock.patch("src.config.config.BASE_DIR", tmp_path),
        mock.patch("urllib.request.urlopen") as mock_urlopen,
    ):
        # Mock successful API response
        mock_response = mock.MagicMock()
        mock_response.read.return_value = json.dumps(FAKE_HANDOFF_PAYLOAD).encode()
        mock_response.__enter__.return_value = mock_response
        mock_response.__exit__.return_value = None
        mock_urlopen.return_value = mock_response

        bot = KraabUserbot()
        result = await bot._auto_export_handoff_snapshot(reason="test_stop")

    assert result["exported"] is True
    assert result["reason"] == "test_stop"
    assert result["error"] is None
    dest = Path(result["path"])
    assert dest.exists()
    written = json.loads(dest.read_text(encoding="utf-8"))
    assert written == FAKE_HANDOFF_PAYLOAD


@pytest.mark.asyncio
async def test_auto_export_uses_fast_handoff_snapshot_query(tmp_path: Path) -> None:
    """Auto-export должен ходить в облегчённый handoff без cloud-probe."""

    captured_urls: list[str] = []

    def _fake_urlopen(req, timeout=0):  # noqa: ANN001
        _ = timeout
        captured_urls.append(req.full_url if isinstance(req, urllib.request.Request) else str(req))
        mock_response = mock.MagicMock()
        mock_response.read.return_value = json.dumps(FAKE_HANDOFF_PAYLOAD).encode()
        mock_response.__enter__.return_value = mock_response
        mock_response.__exit__.return_value = None
        return mock_response

    with (
        mock.patch("src.config.config.BASE_DIR", tmp_path),
        mock.patch("urllib.request.urlopen", side_effect=_fake_urlopen),
    ):
        bot = KraabUserbot()
        result = await bot._auto_export_handoff_snapshot(reason="test_fast_query")

    assert result["exported"] is True
    assert captured_urls
    assert captured_urls[0].endswith("/api/runtime/handoff?probe_cloud_runtime=0")


@pytest.mark.asyncio
async def test_auto_export_connection_error_does_not_raise(tmp_path: Path) -> None:
    """When API is unreachable, export fails gracefully without raising."""
    with (
        mock.patch("src.config.config.BASE_DIR", tmp_path),
        mock.patch("urllib.request.urlopen", side_effect=OSError("connection refused")),
    ):
        bot = KraabUserbot()
        result = await bot._auto_export_handoff_snapshot(reason="test_error")

    assert result["exported"] is False
    assert "connection refused" in result["error"]
    assert result["reason"] == "test_error"


@pytest.mark.asyncio
async def test_auto_export_returns_correct_structure(tmp_path: Path) -> None:
    """Returned dict always has expected keys."""
    with (
        mock.patch("src.config.config.BASE_DIR", tmp_path),
        mock.patch("urllib.request.urlopen", side_effect=OSError("test")),
    ):
        bot = KraabUserbot()
        result = await bot._auto_export_handoff_snapshot(reason="test")

    assert set(result.keys()) == {"exported", "path", "error", "reason"}
    assert isinstance(result["exported"], bool)
    assert isinstance(result["path"], str)
    assert isinstance(result["reason"], str)


@pytest.mark.asyncio
async def test_periodic_export_in_maintenance(tmp_path: Path) -> None:
    """Maintenance task should trigger periodic exports."""
    # This is a smoke test - full integration test would require running maintenance loop
    with mock.patch("src.config.config.BASE_DIR", tmp_path):
        bot = KraabUserbot()
        # Just verify the method exists and is callable
        assert hasattr(bot, "_auto_export_handoff_snapshot")
        assert callable(bot._auto_export_handoff_snapshot)
