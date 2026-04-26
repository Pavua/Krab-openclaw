# -*- coding: utf-8 -*-
"""
Тесты ``src.modules.web_routers.assistant_router`` — Phase 2 Wave V.

Покрытие фокусировано на factory-pattern: build_assistant_router(ctx) должен
работать stand-alone (без полного WebApp), используя helper'ы из ctx.deps.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.modules.web_routers._context import RouterContext
from src.modules.web_routers.assistant_router import build_assistant_router


def _make_client(
    *,
    capabilities: dict[str, Any] | None = None,
    max_bytes: int = 1_000_000,
    deps_overrides: dict[str, Any] | None = None,
) -> TestClient:
    captured: dict[str, Any] = {}

    def _caps() -> dict[str, Any]:
        return capabilities or {"ok": True, "mode": "web_native"}

    def _max_bytes() -> int:
        return max_bytes

    def _sanitize_name(name: str) -> str:
        return name.replace("/", "_").replace("\\", "_")

    def _build_prompt(*, file_name, content_type, raw_bytes, stored_path) -> dict[str, Any]:
        captured["build_prompt"] = {
            "file_name": file_name,
            "content_type": content_type,
            "size": len(raw_bytes),
            "stored_path": str(stored_path),
        }
        return {"kind": "text", "preview": f"got {file_name}"}

    deps: dict[str, Any] = {
        "assistant_capabilities_snapshot_helper": _caps,
        "assistant_attachment_max_bytes_helper": _max_bytes,
        "assistant_attachment_sanitize_name_helper": _sanitize_name,
        "assistant_attachment_build_prompt_helper": _build_prompt,
        "black_box": None,
    }
    if deps_overrides:
        deps.update(deps_overrides)

    ctx = RouterContext(
        deps=deps,
        project_root=Path("."),
        web_api_key_fn=lambda: None,
        assert_write_access_fn=lambda *a, **kw: None,
    )
    app = FastAPI()
    app.include_router(build_assistant_router(ctx))
    client = TestClient(app)
    client._captured = captured  # type: ignore[attr-defined]
    return client


# ---------------------------------------------------------------------------
# /api/assistant/capabilities
# ---------------------------------------------------------------------------


def test_capabilities_returns_helper_payload() -> None:
    client = _make_client(capabilities={"ok": True, "mode": "web_native", "x": 1})
    resp = client.get("/api/assistant/capabilities")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "mode": "web_native", "x": 1}


def test_capabilities_503_when_helper_missing() -> None:
    client = _make_client(deps_overrides={"assistant_capabilities_snapshot_helper": None})
    resp = client.get("/api/assistant/capabilities")
    assert resp.status_code == 503
    assert "assistant_capabilities_helper_not_configured" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# /api/assistant/attachment
# ---------------------------------------------------------------------------


def test_attachment_upload_happy_path(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    client = _make_client()
    files = {"file": ("note.txt", io.BytesIO(b"hello world"), "text/plain")}
    resp = client.post("/api/assistant/attachment", files=files)
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["attachment"]["kind"] == "text"
    # Файл реально сохранён в artifacts/web_uploads/
    uploads = tmp_path / "artifacts" / "web_uploads"
    assert uploads.exists()
    assert any("note.txt" in p.name for p in uploads.iterdir())


def test_attachment_rejects_empty_file(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    client = _make_client()
    files = {"file": ("empty.txt", io.BytesIO(b""), "text/plain")}
    resp = client.post("/api/assistant/attachment", files=files)
    assert resp.status_code == 400
    assert "empty_file" in resp.json()["detail"]


def test_attachment_rejects_too_large(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    client = _make_client(max_bytes=10)
    files = {"file": ("big.txt", io.BytesIO(b"X" * 100), "text/plain")}
    resp = client.post("/api/assistant/attachment", files=files)
    assert resp.status_code == 413
    assert "too_large" in resp.json()["detail"]


def test_attachment_rejects_whitespace_filename(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    client = _make_client()
    # filename содержит только пробелы → trigger filename_required
    files = {"file": ("   ", io.BytesIO(b"data"), "text/plain")}
    resp = client.post("/api/assistant/attachment", files=files)
    assert resp.status_code == 400
    assert "filename_required" in resp.json()["detail"]


def test_attachment_503_when_helpers_missing(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    client = _make_client(deps_overrides={"assistant_attachment_max_bytes_helper": None})
    files = {"file": ("note.txt", io.BytesIO(b"hello"), "text/plain")}
    resp = client.post("/api/assistant/attachment", files=files)
    assert resp.status_code == 503
    assert "helpers_not_configured" in resp.json()["detail"]


def test_attachment_logs_to_black_box(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    events: list[tuple[str, str]] = []

    class _BB:
        def log_event(self, name: str, detail: str) -> None:
            events.append((name, detail))

    client = _make_client(deps_overrides={"black_box": _BB()})
    files = {"file": ("note.txt", io.BytesIO(b"hello"), "text/plain")}
    resp = client.post("/api/assistant/attachment", files=files)
    assert resp.status_code == 200
    assert events and events[0][0] == "web_assistant_attachment"
    assert "name=note.txt" in events[0][1]


# ===========================================================================
# Phase 2 Part 2C (Session 27) — /api/assistant/stream
# ===========================================================================


def test_assistant_stream_empty_prompt() -> None:
    """SSE возвращает {ok:false} при пустом prompt — без entry в openclaw."""
    client = _make_client()
    resp = client.get("/api/assistant/stream", params={"prompt": "   "})
    assert resp.status_code == 200
    assert resp.json() == {"ok": False, "error": "empty prompt"}


def test_assistant_stream_sse_returns_event_stream(monkeypatch) -> None:
    """SSE endpoint возвращает text/event-stream content-type + done event."""

    class _FakeClient:
        _active_tool_calls: list = []

        async def send_message_stream(self, **kwargs):
            yield "hello "
            yield "world"

        def get_last_runtime_route(self) -> dict:
            return {"model": "gemini-3-pro", "provider": "google"}

    fake = _FakeClient()
    import src.openclaw_client as oc_mod

    monkeypatch.setattr(oc_mod, "openclaw_client", fake, raising=False)

    client = _make_client()
    with client.stream("GET", "/api/assistant/stream", params={"prompt": "hi"}) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        body = b"".join(resp.iter_bytes()).decode("utf-8", errors="replace")
    assert "event: status" in body
    assert "event: route" in body
    assert "event: message" in body
    assert "event: done" in body


def test_assistant_stream_handles_exception(monkeypatch) -> None:
    """SSE error event при исключении в openclaw."""

    class _FakeClient:
        async def send_message_stream(self, **kwargs):
            raise RuntimeError("boom-test")
            yield  # pragma: no cover (unreachable)

    import src.openclaw_client as oc_mod

    monkeypatch.setattr(oc_mod, "openclaw_client", _FakeClient(), raising=False)

    client = _make_client()
    with client.stream("GET", "/api/assistant/stream", params={"prompt": "x"}) as resp:
        body = b"".join(resp.iter_bytes()).decode("utf-8", errors="replace")
    assert "event: error" in body
    assert "boom-test" in body
