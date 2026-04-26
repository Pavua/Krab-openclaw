# -*- coding: utf-8 -*-
"""
Unit-тесты для runtime_status_router (Phase 2 Wave D, Session 25).

Покрывают 4 stateless GET endpoints:
- /api/silence/status
- /api/notify/status
- /api/message_batcher/stats
- /api/chat_windows/stats (включая graceful error path)
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.modules.web_routers.runtime_status_router import router


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_silence_status_ok(client: TestClient) -> None:
    fake_mgr = MagicMock()
    fake_mgr.status.return_value = {"enabled": False, "until": None, "reason": ""}
    with patch("src.core.silence_mode.silence_manager", fake_mgr):
        resp = client.get("/api/silence/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["enabled"] is False
    assert body["until"] is None
    fake_mgr.status.assert_called_once()


def test_silence_status_enabled(client: TestClient) -> None:
    fake_mgr = MagicMock()
    fake_mgr.status.return_value = {"enabled": True, "until": 999.0, "reason": "test"}
    with patch("src.core.silence_mode.silence_manager", fake_mgr):
        resp = client.get("/api/silence/status")
    body = resp.json()
    assert body["ok"] is True
    assert body["enabled"] is True
    assert body["until"] == 999.0
    assert body["reason"] == "test"


def test_notify_status_default_true(client: TestClient) -> None:
    fake_config = SimpleNamespace(TOOL_NARRATION_ENABLED=True)
    with patch("src.config.config", fake_config):
        resp = client.get("/api/notify/status")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "enabled": True}


def test_notify_status_disabled(client: TestClient) -> None:
    fake_config = SimpleNamespace(TOOL_NARRATION_ENABLED=False)
    with patch("src.config.config", fake_config):
        resp = client.get("/api/notify/status")
    assert resp.json() == {"ok": True, "enabled": False}


def test_notify_status_missing_attr_defaults_true(client: TestClient) -> None:
    """Если у config нет атрибута — getattr default=True."""
    fake_config = SimpleNamespace()  # no TOOL_NARRATION_ENABLED
    with patch("src.config.config", fake_config):
        resp = client.get("/api/notify/status")
    assert resp.json() == {"ok": True, "enabled": True}


def test_message_batcher_stats(client: TestClient) -> None:
    fake_batcher = MagicMock()
    fake_batcher.stats.return_value = {"queue_depth": 3, "chats": 2, "flushes_total": 17}
    with patch("src.core.message_batcher.message_batcher", fake_batcher):
        resp = client.get("/api/message_batcher/stats")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["queue_depth"] == 3
    assert body["chats"] == 2
    assert body["flushes_total"] == 17
    fake_batcher.stats.assert_called_once()


def test_chat_windows_stats_ok(client: TestClient) -> None:
    fake_mgr = MagicMock()
    fake_mgr.stats.return_value = {"windows": 5, "evictions": 2}
    with patch("src.core.chat_window_manager.chat_window_manager", fake_mgr):
        resp = client.get("/api/chat_windows/stats")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["windows"] == 5
    assert body["evictions"] == 2


def test_chat_windows_stats_graceful_error(client: TestClient) -> None:
    """Если singleton падает — endpoint возвращает {ok: False, error: ...} без 500."""
    fake_mgr = MagicMock()
    fake_mgr.stats.side_effect = RuntimeError("boom")
    with patch("src.core.chat_window_manager.chat_window_manager", fake_mgr):
        resp = client.get("/api/chat_windows/stats")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert "boom" in body["error"]
