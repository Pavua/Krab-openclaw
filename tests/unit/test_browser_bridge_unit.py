# -*- coding: utf-8 -*-
"""
Юнит-тесты для src/integrations/browser_bridge.py.

Покрывают чистую логику без реального Chrome/CDP:
- вспомогательные методы (normalize, is_stale, fetch_sync-stub)
- _devtools_active_port_candidates (пути)
- _read_devtools_ws_endpoint (парсинг файлов)
- _should_prefer_raw_cdp (кеш)
- _resolve_ws_endpoint (порядок fallback)
- RawCDPConnection.call (JSON round-trip)
- health_check (форматирование ответа при таймауте/ошибке)
- screenshot_base64 (base64 обёртка)
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.integrations.browser_bridge import BrowserBridge, RawCDPConnection

# ---------------------------------------------------------------------------
# RawCDPConnection — call
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_raw_cdp_call_increments_id_and_returns_result():
    """call() должен отправить JSON с id и вернуть result."""
    ws = MagicMock()
    ws.send = AsyncMock()
    # Ответ с совпадающим id
    ws.recv = AsyncMock(return_value=json.dumps({"id": 1, "result": {"ok": True}}))

    conn = RawCDPConnection(ws, timeout_sec=5.0)
    result = await conn.call("Page.enable")

    assert result == {"ok": True}
    sent = json.loads(ws.send.call_args[0][0])
    assert sent["method"] == "Page.enable"
    assert sent["id"] == 1


@pytest.mark.asyncio
async def test_raw_cdp_call_skips_unrelated_messages():
    """call() должен игнорировать сообщения с чужим id."""
    ws = MagicMock()
    ws.send = AsyncMock()
    ws.recv = AsyncMock(
        side_effect=[
            # Первое сообщение — другой id, должно быть пропущено
            json.dumps({"id": 99, "result": {"wrong": True}}),
            # Второе — наш id
            json.dumps({"id": 1, "result": {"correct": True}}),
        ]
    )

    conn = RawCDPConnection(ws, timeout_sec=5.0)
    result = await conn.call("Runtime.enable")
    assert result == {"correct": True}


@pytest.mark.asyncio
async def test_raw_cdp_call_raises_on_error():
    """call() должен поднять RuntimeError при CDP error-ответе."""
    ws = MagicMock()
    ws.send = AsyncMock()
    ws.recv = AsyncMock(
        return_value=json.dumps({"id": 1, "error": {"code": -32602, "message": "bad params"}})
    )

    conn = RawCDPConnection(ws, timeout_sec=5.0)
    with pytest.raises(RuntimeError, match="Page.enable"):
        await conn.call("Page.enable")


@pytest.mark.asyncio
async def test_raw_cdp_call_passes_session_id():
    """call() должен добавить sessionId в payload если передан."""
    ws = MagicMock()
    ws.send = AsyncMock()
    ws.recv = AsyncMock(return_value=json.dumps({"id": 1, "result": {}}))

    conn = RawCDPConnection(ws, timeout_sec=5.0)
    await conn.call("Page.enable", session_id="sess-abc")

    sent = json.loads(ws.send.call_args[0][0])
    assert sent.get("sessionId") == "sess-abc"


@pytest.mark.asyncio
async def test_raw_cdp_close_delegates_to_websocket():
    """close() должен вызвать ws.close()."""
    ws = MagicMock()
    ws.close = AsyncMock()
    conn = RawCDPConnection(ws)
    await conn.close()
    ws.close.assert_called_once()


# ---------------------------------------------------------------------------
# BrowserBridge._normalize_page_targets
# ---------------------------------------------------------------------------


def test_normalize_page_targets_filters_non_page():
    """Должен оставить только targets с type='page'."""
    payload = {
        "targetInfos": [
            {"type": "page", "targetId": "p1", "title": "Google", "url": "https://google.com"},
            {"type": "service_worker", "targetId": "sw1", "title": "", "url": ""},
            {"type": "page", "targetId": "p2", "title": "GitHub", "url": "https://github.com"},
        ]
    }
    result = BrowserBridge._normalize_page_targets(payload)
    assert len(result) == 2
    assert all(t["type"] == "page" for t in result)


def test_normalize_page_targets_empty_payload():
    """Пустой словарь → пустой список."""
    assert BrowserBridge._normalize_page_targets({}) == []


def test_normalize_page_targets_non_dict_input():
    """None и не-dict → пустой список."""
    assert BrowserBridge._normalize_page_targets(None) == []  # type: ignore[arg-type]
    assert BrowserBridge._normalize_page_targets([]) == []  # type: ignore[arg-type]


def test_normalize_page_targets_skips_non_dict_items():
    """Элементы не-dict должны быть пропущены."""
    payload = {"targetInfos": [None, "bad", {"type": "page", "targetId": "x"}]}
    result = BrowserBridge._normalize_page_targets(payload)
    assert len(result) == 1
    assert result[0]["targetId"] == "x"


# ---------------------------------------------------------------------------
# BrowserBridge._is_stale_ws_error
# ---------------------------------------------------------------------------


def test_is_stale_ws_error_detects_404():
    """Ошибка с '404' в repr → stale."""
    assert BrowserBridge._is_stale_ws_error(Exception("HTTP 404 Not Found"))


def test_is_stale_ws_error_detects_invalid_status():
    """InvalidStatus в repr → stale."""
    exc = type("InvalidStatus", (Exception,), {})("bad status 404")
    assert BrowserBridge._is_stale_ws_error(exc)


def test_is_stale_ws_error_connection_refused():
    """ConnectionRefusedError — не stale."""
    assert not BrowserBridge._is_stale_ws_error(ConnectionRefusedError("refused"))


def test_is_stale_ws_error_timeout():
    """TimeoutError — не stale."""
    assert not BrowserBridge._is_stale_ws_error(asyncio.TimeoutError())


# ---------------------------------------------------------------------------
# BrowserBridge._devtools_active_port_candidates
# ---------------------------------------------------------------------------


def test_devtools_active_port_candidates_include_home(tmp_path, monkeypatch):
    """Кандидаты должны включать пути в HOME."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("KRAB_OPERATOR_HOME", raising=False)

    bridge = BrowserBridge()
    candidates = bridge._devtools_active_port_candidates()

    # Все кандидаты — Path-объекты
    assert all(isinstance(c, Path) for c in candidates)

    # Должен быть путь через debug-profile
    names = [str(c) for c in candidates]
    assert any("chrome-debug-profile" in n for n in names)


def test_devtools_active_port_candidates_krab_operator_home(tmp_path, monkeypatch):
    """KRAB_OPERATOR_HOME добавляет свои кандидаты первыми."""
    monkeypatch.setenv("KRAB_OPERATOR_HOME", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    bridge = BrowserBridge()
    candidates = bridge._devtools_active_port_candidates()
    names = [str(c) for c in candidates]

    # Первые кандидаты — из KRAB_OPERATOR_HOME
    assert str(tmp_path) in names[0]


def test_devtools_active_port_candidates_no_duplicates(tmp_path, monkeypatch):
    """Если KRAB_OPERATOR_HOME == HOME — нет дублирующихся Path-объектов."""
    monkeypatch.setenv("KRAB_OPERATOR_HOME", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))

    bridge = BrowserBridge()
    candidates = bridge._devtools_active_port_candidates()
    # Путей к debug-profile должно быть ровно 2 (не 4)
    debug_paths = [c for c in candidates if "chrome-debug-profile" in str(c)]
    assert len(debug_paths) == 2


# ---------------------------------------------------------------------------
# BrowserBridge._read_devtools_ws_endpoint
# ---------------------------------------------------------------------------


def test_read_devtools_ws_endpoint_valid_file(tmp_path, monkeypatch):
    """Читает корректный DevToolsActivePort и возвращает ws:// URL."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("KRAB_OPERATOR_HOME", raising=False)

    port_file = tmp_path / ".openclaw" / "chrome-debug-profile" / "DevToolsActivePort"
    port_file.parent.mkdir(parents=True)
    port_file.write_text("9222\n/devtools/browser/abc-123\n", encoding="utf-8")

    bridge = BrowserBridge()
    ws = bridge._read_devtools_ws_endpoint()
    assert ws == "ws://127.0.0.1:9222/devtools/browser/abc-123"


def test_read_devtools_ws_endpoint_invalid_format(tmp_path, monkeypatch):
    """Файл с некорректным содержимым → None."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("KRAB_OPERATOR_HOME", raising=False)

    port_file = tmp_path / ".openclaw" / "chrome-debug-profile" / "DevToolsActivePort"
    port_file.parent.mkdir(parents=True)
    # Порт не число
    port_file.write_text("not-a-port\n/devtools/browser/abc\n", encoding="utf-8")

    bridge = BrowserBridge()
    assert bridge._read_devtools_ws_endpoint() is None


def test_read_devtools_ws_endpoint_wrong_path(tmp_path, monkeypatch):
    """Файл с путём не начинающимся на /devtools/browser/ → None."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("KRAB_OPERATOR_HOME", raising=False)

    port_file = tmp_path / ".openclaw" / "chrome-debug-profile" / "DevToolsActivePort"
    port_file.parent.mkdir(parents=True)
    port_file.write_text("9222\n/wrong/path/abc\n", encoding="utf-8")

    bridge = BrowserBridge()
    assert bridge._read_devtools_ws_endpoint() is None


def test_read_devtools_ws_endpoint_no_files(tmp_path, monkeypatch):
    """Файлы не существуют → None."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("KRAB_OPERATOR_HOME", raising=False)

    bridge = BrowserBridge()
    assert bridge._read_devtools_ws_endpoint() is None


# ---------------------------------------------------------------------------
# BrowserBridge._should_prefer_raw_cdp
# ---------------------------------------------------------------------------


def test_should_prefer_raw_cdp_uses_cache():
    """Если _cached_ws_endpoint установлен — сразу возвращает (True, url)."""
    bridge = BrowserBridge()
    bridge._cached_ws_endpoint = "ws://127.0.0.1:9222/devtools/browser/cached"

    prefer, ws = bridge._should_prefer_raw_cdp()
    assert prefer is True
    assert ws == "ws://127.0.0.1:9222/devtools/browser/cached"


def test_should_prefer_raw_cdp_no_cache_no_file(tmp_path, monkeypatch):
    """Без кеша и без файла → (False, None)."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("KRAB_OPERATOR_HOME", raising=False)

    bridge = BrowserBridge()
    prefer, ws = bridge._should_prefer_raw_cdp()
    assert prefer is False
    assert ws is None


# ---------------------------------------------------------------------------
# BrowserBridge._resolve_ws_endpoint (async)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_ws_endpoint_returns_cache_immediately():
    """_resolve_ws_endpoint должен вернуть кеш без IO."""
    bridge = BrowserBridge()
    bridge._cached_ws_endpoint = "ws://cached"

    ws = await bridge._resolve_ws_endpoint()
    assert ws == "ws://cached"


@pytest.mark.asyncio
async def test_resolve_ws_endpoint_fallback_to_http(monkeypatch, tmp_path):
    """Если файла нет — должен вызвать HTTP fallback и закешировать результат."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("KRAB_OPERATOR_HOME", raising=False)

    bridge = BrowserBridge()

    # Подменяем HTTP-метод
    async def fake_http():
        return "ws://127.0.0.1:9222/devtools/browser/http-resolved"

    bridge._read_ws_from_json_version_async = fake_http  # type: ignore[method-assign]

    ws = await bridge._resolve_ws_endpoint()
    assert ws == "ws://127.0.0.1:9222/devtools/browser/http-resolved"
    assert bridge._cached_ws_endpoint == ws


# ---------------------------------------------------------------------------
# BrowserBridge._fetch_ws_from_json_version_sync (static)
# ---------------------------------------------------------------------------


def test_fetch_ws_from_json_version_sync_returns_none_on_error():
    """При недоступном хосте должен вернуть None, не поднимать исключение."""
    result = BrowserBridge._fetch_ws_from_json_version_sync("http://127.0.0.1:19999/json/version")
    assert result is None


# ---------------------------------------------------------------------------
# BrowserBridge.health_check — форматирование ответа
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_check_timeout_returns_structured_response():
    """health_check при TimeoutError должен вернуть структурированный dict."""
    bridge = BrowserBridge()

    async def slow_attach():
        await asyncio.sleep(100)
        return True

    bridge.is_attached = slow_attach  # type: ignore[method-assign]

    result = await bridge.health_check(timeout_sec=0.01)
    assert result["ok"] is False
    assert result["blocked"] is False
    assert "timeout" in result["error"]
    assert result["tab_count"] == 0
    assert result["cdp_url"] == BrowserBridge.CDP_URL


@pytest.mark.asyncio
async def test_health_check_not_attached_returns_blocked():
    """health_check когда is_attached=False → blocked=True."""
    bridge = BrowserBridge()
    bridge.is_attached = AsyncMock(return_value=False)  # type: ignore[method-assign]

    result = await bridge.health_check(timeout_sec=4.0)
    assert result["ok"] is False
    assert result["blocked"] is True
    assert result["error"] == "CDP not reachable"


@pytest.mark.asyncio
async def test_health_check_attached_returns_ok():
    """health_check при успешном подключении → ok=True."""
    bridge = BrowserBridge()
    bridge.is_attached = AsyncMock(return_value=True)  # type: ignore[method-assign]
    bridge.list_tabs = AsyncMock(return_value=[{"id": 0, "url": "https://x.com", "title": "X"}])  # type: ignore[method-assign]

    result = await bridge.health_check(timeout_sec=4.0)
    assert result["ok"] is True
    assert result["blocked"] is False
    assert result["tab_count"] == 1
    assert result["error"] == ""


@pytest.mark.asyncio
async def test_health_check_exception_returns_error():
    """health_check при Exception → ok=False, blocked=False, repr в error."""
    bridge = BrowserBridge()
    bridge.is_attached = AsyncMock(side_effect=RuntimeError("boom"))  # type: ignore[method-assign]

    result = await bridge.health_check(timeout_sec=4.0)
    assert result["ok"] is False
    assert result["blocked"] is False
    assert "boom" in result["error"]


# ---------------------------------------------------------------------------
# BrowserBridge.screenshot_base64
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_screenshot_base64_encodes_bytes():
    """screenshot_base64 должен возвращать корректный base64-encoded PNG."""
    import base64

    bridge = BrowserBridge()
    fake_bytes = b"\x89PNG\r\n\x1a\n"
    bridge.screenshot = AsyncMock(return_value=fake_bytes)  # type: ignore[method-assign]

    result = await bridge.screenshot_base64()
    assert result == base64.b64encode(fake_bytes).decode()


@pytest.mark.asyncio
async def test_screenshot_base64_returns_none_when_no_screenshot():
    """screenshot_base64 должен вернуть None если screenshot() вернул None."""
    bridge = BrowserBridge()
    bridge.screenshot = AsyncMock(return_value=None)  # type: ignore[method-assign]

    result = await bridge.screenshot_base64()
    assert result is None


# ---------------------------------------------------------------------------
# BrowserBridge.CDP_URL
# ---------------------------------------------------------------------------


def test_cdp_url_default():
    """CDP_URL должен указывать на localhost:9222."""
    assert BrowserBridge.CDP_URL == "http://127.0.0.1:9222"
