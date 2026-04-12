# -*- coding: utf-8 -*-
"""
Тесты для src/mcp_panel_server.py — pure functions и конфигурация.
HTTP-запросы замокированы через unittest.mock; реальных сетевых соединений нет.
"""

from __future__ import annotations

import importlib
import os
from unittest.mock import MagicMock, patch

import httpx

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_response(status: int, body: dict) -> MagicMock:
    """Создаёт фейковый httpx.Response."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status
    resp.json.return_value = body
    if status >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            message=f"HTTP {status}",
            request=MagicMock(),
            response=resp,
        )
    else:
        resp.raise_for_status.return_value = None
    return resp


# ---------------------------------------------------------------------------
# Конфигурация: переменные окружения
# ---------------------------------------------------------------------------


def test_default_panel_url():
    """По умолчанию PANEL_BASE_URL = http://127.0.0.1:8080."""
    # Убираем env-переменную, если есть, и перезагружаем модуль
    env_backup = os.environ.pop("KRAB_PANEL_URL", None)
    try:
        import src.mcp_panel_server as mod

        importlib.reload(mod)
        assert mod.PANEL_BASE_URL == "http://127.0.0.1:8080"
    finally:
        if env_backup is not None:
            os.environ["KRAB_PANEL_URL"] = env_backup


def test_custom_panel_url():
    """KRAB_PANEL_URL переопределяет базовый URL."""
    with patch.dict(os.environ, {"KRAB_PANEL_URL": "http://10.0.0.1:9999"}):
        import src.mcp_panel_server as mod

        importlib.reload(mod)
        assert mod.PANEL_BASE_URL == "http://10.0.0.1:9999"


def test_default_timeout():
    """HTTP_TIMEOUT_SEC по умолчанию равен 5.0."""
    env_backup = os.environ.pop("KRAB_PANEL_TIMEOUT_SEC", None)
    try:
        import src.mcp_panel_server as mod

        importlib.reload(mod)
        assert mod.HTTP_TIMEOUT_SEC == 5.0
    finally:
        if env_backup is not None:
            os.environ["KRAB_PANEL_TIMEOUT_SEC"] = env_backup


# ---------------------------------------------------------------------------
# _get() — успешный ответ
# ---------------------------------------------------------------------------


def test_get_success():
    """_get() возвращает распарсенный JSON при 200 OK."""
    from src import mcp_panel_server as mod

    fake_resp = _make_response(200, {"status": "ok", "uptime": 42})
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get.return_value = fake_resp

    with patch("src.mcp_panel_server.httpx.Client", return_value=mock_client):
        result = mod._get("/api/health/lite")

    assert result == {"status": "ok", "uptime": 42}
    mock_client.get.assert_called_once()


# ---------------------------------------------------------------------------
# _get() — ошибочные ветки
# ---------------------------------------------------------------------------


def test_get_http_status_error():
    """_get() возвращает dict с _error='http_error' при HTTP 4xx/5xx."""
    from src import mcp_panel_server as mod

    fake_resp = _make_response(503, {})
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get.return_value = fake_resp

    with patch("src.mcp_panel_server.httpx.Client", return_value=mock_client):
        result = mod._get("/api/health")

    assert result["_error"] == "http_error"
    assert result["status_code"] == 503
    assert "url" in result


def test_get_connection_error():
    """_get() возвращает dict с _error='connection_failed' при ConnectError."""
    from src import mcp_panel_server as mod

    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get.side_effect = httpx.ConnectError("refused")

    with patch("src.mcp_panel_server.httpx.Client", return_value=mock_client):
        result = mod._get("/api/stats")

    assert result["_error"] == "connection_failed"
    assert "hint" in result


def test_get_timeout_error():
    """_get() возвращает dict с _error='connection_failed' при TimeoutException."""
    from src import mcp_panel_server as mod

    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get.side_effect = httpx.TimeoutException("timed out")

    with patch("src.mcp_panel_server.httpx.Client", return_value=mock_client):
        result = mod._get("/api/stats")

    assert result["_error"] == "connection_failed"


def test_get_unexpected_error():
    """_get() возвращает dict с _error='unexpected' при произвольном исключении."""
    from src import mcp_panel_server as mod

    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get.side_effect = ValueError("unexpected boom")

    with patch("src.mcp_panel_server.httpx.Client", return_value=mock_client):
        result = mod._get("/api/queue")

    assert result["_error"] == "unexpected"
    assert result["error_type"] == "ValueError"


# ---------------------------------------------------------------------------
# _get() — params передаются
# ---------------------------------------------------------------------------


def test_get_passes_params():
    """_get() передаёт params в httpx.Client.get()."""
    from src import mcp_panel_server as mod

    fake_resp = _make_response(200, {"items": []})
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get.return_value = fake_resp

    with patch("src.mcp_panel_server.httpx.Client", return_value=mock_client):
        mod._get("/api/inbox/items", params={"limit": 10, "kind": "cron_run"})

    _, kwargs = mock_client.get.call_args
    assert kwargs["params"] == {"limit": 10, "kind": "cron_run"}
