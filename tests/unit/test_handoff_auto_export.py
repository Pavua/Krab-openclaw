# -*- coding: utf-8 -*-
"""
Tests for _auto_export_before_switch() in runtime_switch_assistant.

Covers:
1) Successful export: HTTP 200 → JSON written to artifacts/auto_handoff_*.json
2) Connection error: function does not raise, returns exported=False
3) Returned dict structure is always {"exported": bool, "path": str, "error": ...}
"""

from __future__ import annotations

import http.server
import json
import threading
from pathlib import Path
from unittest import mock

import pytest

import scripts.runtime_switch_assistant as rsa


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FAKE_HANDOFF_PAYLOAD = {"status": "ok", "phase": "test", "items": [1, 2, 3]}


class _OneShot200Handler(http.server.BaseHTTPRequestHandler):
    """Responds with FAKE_HANDOFF_PAYLOAD once, then closes."""

    def do_GET(self) -> None:  # noqa: N802
        body = json.dumps(FAKE_HANDOFF_PAYLOAD).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:  # suppress log noise in tests
        pass


def _start_local_server() -> tuple[http.server.HTTPServer, int]:
    """Starts a local HTTP server on a random port and returns (server, port)."""
    server = http.server.HTTPServer(("127.0.0.1", 0), _OneShot200Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    return server, port


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_auto_export_success(tmp_path: Path) -> None:
    """When the API responds with 200+JSON, the snapshot is written to disk."""
    server, port = _start_local_server()
    fake_url = f"http://127.0.0.1:{port}/api/runtime/handoff"

    with (
        mock.patch.object(rsa, "HANDOFF_API_URL", fake_url),
        mock.patch.object(rsa, "ARTIFACTS_DIR", tmp_path),
    ):
        result = rsa._auto_export_before_switch()

    assert result["exported"] is True
    assert result["error"] is None
    dest = Path(result["path"])
    assert dest.exists(), f"Expected file at {dest}"
    written = json.loads(dest.read_text(encoding="utf-8"))
    assert written == FAKE_HANDOFF_PAYLOAD


def test_auto_export_connection_error_does_not_raise(tmp_path: Path) -> None:
    """When Краб is not running (connection refused), the function returns silently."""
    # Port 1 is reserved and will always refuse connections
    dead_url = "http://127.0.0.1:1/api/runtime/handoff"

    with (
        mock.patch.object(rsa, "HANDOFF_API_URL", dead_url),
        mock.patch.object(rsa, "ARTIFACTS_DIR", tmp_path),
    ):
        result = rsa._auto_export_before_switch()  # must NOT raise

    assert result["exported"] is False
    assert result["error"] is not None
    assert isinstance(result["error"], str)
    assert len(result["error"]) > 0


def test_auto_export_timeout_does_not_raise(tmp_path: Path) -> None:
    """Simulate a timeout by patching urlopen to raise an OSError."""
    import urllib.error

    with (
        mock.patch.object(rsa, "ARTIFACTS_DIR", tmp_path),
        mock.patch("urllib.request.urlopen", side_effect=OSError("timed out")),
    ):
        result = rsa._auto_export_before_switch()

    assert result["exported"] is False
    assert "timed out" in result["error"]


def test_auto_export_returns_correct_dict_structure(tmp_path: Path) -> None:
    """Returned dict always has exactly the expected keys."""
    dead_url = "http://127.0.0.1:1/api/runtime/handoff"

    with (
        mock.patch.object(rsa, "HANDOFF_API_URL", dead_url),
        mock.patch.object(rsa, "ARTIFACTS_DIR", tmp_path),
    ):
        result = rsa._auto_export_before_switch()

    assert set(result.keys()) == {"exported", "path", "error"}
    assert isinstance(result["exported"], bool)
    assert isinstance(result["path"], str)


def test_auto_export_path_contains_timestamp(tmp_path: Path) -> None:
    """The output path includes the auto_handoff_ prefix and a timestamp."""
    dead_url = "http://127.0.0.1:1/api/runtime/handoff"

    with (
        mock.patch.object(rsa, "HANDOFF_API_URL", dead_url),
        mock.patch.object(rsa, "ARTIFACTS_DIR", tmp_path),
    ):
        result = rsa._auto_export_before_switch()

    filename = Path(result["path"]).name
    assert filename.startswith("auto_handoff_")
    assert filename.endswith(".json")
