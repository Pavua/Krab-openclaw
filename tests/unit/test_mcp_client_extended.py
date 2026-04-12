# -*- coding: utf-8 -*-
"""
Расширенные тесты MCPClientManager:
- парсинг tool manifest (структура полей)
- call_tool_unified routing (tor_fetch, server__tool, ошибки)
- обработка ошибок и таймауты в _peekaboo_impl/_web_search_impl/_tor_fetch_impl
- нативные инструменты (peekaboo, web_search, tor_fetch)
- _format_tool_result edge cases
- ensure_server (alias brave→brave-search, unknown server)
- stop_all
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.mcp_client import MCPClientManager

# ---------------------------------------------------------------------------
# _format_tool_result
# ---------------------------------------------------------------------------


def test_format_tool_result_empty_returns_empty_string():
    """None-результат → пустая строка."""
    assert MCPClientManager._format_tool_result(None) == ""


def test_format_tool_result_no_content_attr():
    """Объект без .content → пустая строка."""
    result = object()
    assert MCPClientManager._format_tool_result(result) == ""


def test_format_tool_result_single_text_item():
    """Один TextPart → его текст."""
    part = MagicMock()
    part.text = "hello"
    result = MagicMock()
    result.content = [part]
    assert MCPClientManager._format_tool_result(result) == "hello"


def test_format_tool_result_multiple_items_joined():
    """Несколько TextPart → соединяются через двойной перенос строки."""
    parts = [MagicMock(text="A"), MagicMock(text="B"), MagicMock(text="C")]
    result = MagicMock()
    result.content = parts
    assert MCPClientManager._format_tool_result(result) == "A\n\nB\n\nC"


def test_format_tool_result_part_without_text_skipped():
    """Элемент без .text пропускается."""
    good = MagicMock(text="ok")
    bad = object()  # нет атрибута text
    result = MagicMock()
    result.content = [bad, good]
    assert MCPClientManager._format_tool_result(result) == "ok"


# ---------------------------------------------------------------------------
# get_tool_manifest — структура элементов
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_tool_manifest_entry_structure():
    """Каждая запись манифеста имеет type='function' и нужные поля."""
    import src.config as _cfg_mod

    manager = MCPClientManager()

    with patch.object(_cfg_mod.config, "TOR_ENABLED", False):
        manifest = await manager.get_tool_manifest()

    for entry in manifest:
        assert entry["type"] == "function"
        fn = entry["function"]
        assert "name" in fn
        assert "description" in fn
        assert "parameters" in fn


@pytest.mark.asyncio
async def test_get_tool_manifest_web_search_has_required_query():
    """web_search в манифесте требует поле 'query'."""
    import src.config as _cfg_mod

    manager = MCPClientManager()

    with patch.object(_cfg_mod.config, "TOR_ENABLED", False):
        manifest = await manager.get_tool_manifest()

    ws = next(e for e in manifest if e["function"]["name"] == "web_search")
    params = ws["function"]["parameters"]
    assert "query" in params["properties"]
    assert "query" in params.get("required", [])


@pytest.mark.asyncio
async def test_get_tool_manifest_session_list_tools_exception_is_ignored():
    """Исключение в session.list_tools не ломает весь манифест."""
    import src.config as _cfg_mod

    manager = MCPClientManager()
    bad_session = AsyncMock()
    bad_session.list_tools = AsyncMock(side_effect=RuntimeError("boom"))
    manager.sessions["bad_server"] = bad_session

    with patch.object(_cfg_mod.config, "TOR_ENABLED", False):
        manifest = await manager.get_tool_manifest()

    # Нативные инструменты всё равно должны присутствовать
    names = [e["function"]["name"] for e in manifest]
    assert "peekaboo" in names
    assert "web_search" in names


# ---------------------------------------------------------------------------
# call_tool_unified — tor_fetch routing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_call_tool_unified_tor_fetch_dispatches():
    """call_tool_unified('tor_fetch', ...) делегирует _tor_fetch_impl."""
    manager = MCPClientManager()
    manager._tor_fetch_impl = AsyncMock(return_value="tor content")

    result = await manager.call_tool_unified("tor_fetch", {"url": "http://example.onion"})

    assert result == "tor content"
    manager._tor_fetch_impl.assert_awaited_once_with({"url": "http://example.onion"})


@pytest.mark.asyncio
async def test_call_tool_unified_server_tool_empty_result():
    """server__tool с пустым результатом возвращает пустую строку."""
    manager = MCPClientManager()
    manager.call_tool = AsyncMock(return_value=None)

    result = await manager.call_tool_unified("srv__tool", {})
    assert result == ""


@pytest.mark.asyncio
async def test_call_tool_unified_multiple_underscores():
    """Разделение только по первым '__' — tool_name может содержать '__'."""
    manager = MCPClientManager()
    fake_result = MagicMock()
    fake_result.content = [MagicMock(text="data")]
    manager.call_tool = AsyncMock(return_value=fake_result)

    await manager.call_tool_unified("server__tool__sub", {"x": 1})

    # Проверяем: server_name="server", tool_name="tool__sub"
    call_args = manager.call_tool.await_args.args
    assert call_args[0] == "server"
    assert call_args[1] == "tool__sub"


# ---------------------------------------------------------------------------
# _peekaboo_impl
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_peekaboo_impl_success():
    """_peekaboo_impl возвращает подтверждение при HTTP 200."""
    manager = MCPClientManager()

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"path": "/tmp/screenshot.png"}

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_resp)

    with patch("httpx.AsyncClient") as mock_cls:
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        result = await manager._peekaboo_impl({"reason": "test"})

    assert "/tmp/screenshot.png" in result
    assert "✅" in result


@pytest.mark.asyncio
async def test_peekaboo_impl_http_error():
    """_peekaboo_impl возвращает ошибку при не-200 статусе."""
    manager = MCPClientManager()

    mock_resp = MagicMock()
    mock_resp.status_code = 503

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_resp)

    with patch("httpx.AsyncClient") as mock_cls:
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        result = await manager._peekaboo_impl({})

    assert "❌" in result
    assert "503" in result


@pytest.mark.asyncio
async def test_peekaboo_impl_connection_error():
    """_peekaboo_impl обрабатывает сетевые исключения."""
    manager = MCPClientManager()

    with patch("httpx.AsyncClient") as mock_cls:
        mock_cls.return_value.__aenter__ = AsyncMock(side_effect=Exception("connection refused"))
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        result = await manager._peekaboo_impl({})

    assert "❌" in result


# ---------------------------------------------------------------------------
# _web_search_impl
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_web_search_impl_empty_query():
    """_web_search_impl с пустым query возвращает ошибку без вызова search_web."""
    manager = MCPClientManager()
    manager.search_web = AsyncMock()

    result = await manager._web_search_impl({"query": "   "})

    assert "❌" in result
    manager.search_web.assert_not_awaited()


@pytest.mark.asyncio
async def test_web_search_impl_missing_query_key():
    """_web_search_impl без ключа query возвращает ошибку."""
    manager = MCPClientManager()
    manager.search_web = AsyncMock()

    result = await manager._web_search_impl({})

    assert "❌" in result
    manager.search_web.assert_not_awaited()


@pytest.mark.asyncio
async def test_web_search_impl_success():
    """_web_search_impl с валидным запросом возвращает результаты поиска."""
    manager = MCPClientManager()
    manager.search_web = AsyncMock(return_value="found: something")

    result = await manager._web_search_impl({"query": "тест"})

    assert result == "found: something"
    manager.search_web.assert_awaited_once_with("тест")


@pytest.mark.asyncio
async def test_web_search_impl_exception_returns_error():
    """_web_search_impl при исключении в search_web возвращает строку-ошибку."""
    manager = MCPClientManager()
    manager.search_web = AsyncMock(side_effect=RuntimeError("network fail"))

    result = await manager._web_search_impl({"query": "test"})

    assert "❌" in result


# ---------------------------------------------------------------------------
# _tor_fetch_impl
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tor_fetch_impl_empty_url():
    """_tor_fetch_impl без URL возвращает ошибку."""
    manager = MCPClientManager()
    result = await manager._tor_fetch_impl({"url": ""})
    assert "❌" in result


@pytest.mark.asyncio
async def test_tor_fetch_impl_success():
    """_tor_fetch_impl при успешном ответе возвращает текст."""
    manager = MCPClientManager()

    mock_fetch = AsyncMock(return_value={"ok": True, "text": "page content"})
    with patch("src.mcp_client.MCPClientManager._tor_fetch_impl", autospec=False):
        pass  # просто проверяем через прямой патч tor_bridge

    with patch("src.integrations.tor_bridge.tor_fetch", mock_fetch):
        result = await manager._tor_fetch_impl({"url": "http://test.onion"})

    assert result == "page content"


@pytest.mark.asyncio
async def test_tor_fetch_impl_truncates_long_response():
    """_tor_fetch_impl обрезает ответ до 8000 символов."""
    manager = MCPClientManager()

    long_text = "x" * 10000
    mock_fetch = AsyncMock(return_value={"ok": True, "text": long_text})

    with patch("src.integrations.tor_bridge.tor_fetch", mock_fetch):
        result = await manager._tor_fetch_impl({"url": "http://test.onion"})

    assert len(result) == 8000


@pytest.mark.asyncio
async def test_tor_fetch_impl_error_response():
    """_tor_fetch_impl при ok=False возвращает сообщение об ошибке."""
    manager = MCPClientManager()

    mock_fetch = AsyncMock(return_value={"ok": False, "error": "timeout"})
    with patch("src.integrations.tor_bridge.tor_fetch", mock_fetch):
        result = await manager._tor_fetch_impl({"url": "http://test.onion"})

    assert "timeout" in result
    assert "❌" in result


# ---------------------------------------------------------------------------
# ensure_server — alias и unknown
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ensure_server_brave_alias_resolved():
    """ensure_server('brave') использует 'brave-search' для поиска в реестре."""
    manager = MCPClientManager()
    manager.start_server = AsyncMock(return_value=True)

    with patch(
        "src.mcp_client.get_managed_mcp_servers",
        return_value={"brave-search": {}},
    ):
        with patch(
            "src.mcp_client.resolve_managed_server_launch",
            return_value={
                "command": "npx",
                "args": [],
                "env": {},
                "missing_env": [],
            },
        ):
            ok = await manager.ensure_server("brave")

    assert ok is True


@pytest.mark.asyncio
async def test_ensure_server_unknown_server_returns_false():
    """ensure_server с неизвестным сервером возвращает False без запуска."""
    manager = MCPClientManager()
    manager.start_server = AsyncMock(return_value=True)

    with patch("src.mcp_client.get_managed_mcp_servers", return_value={}):
        ok = await manager.ensure_server("nonexistent_server_xyz")

    assert ok is False
    manager.start_server.assert_not_awaited()


@pytest.mark.asyncio
async def test_ensure_server_already_running_skips_start():
    """ensure_server не запускает сервер повторно, если сессия уже существует."""
    manager = MCPClientManager()
    manager.sessions["already"] = AsyncMock()
    manager.start_server = AsyncMock(return_value=True)

    ok = await manager.ensure_server("already")

    assert ok is True
    manager.start_server.assert_not_awaited()


# ---------------------------------------------------------------------------
# stop_all
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stop_all_clears_sessions():
    """stop_all очищает словарь сессий."""
    manager = MCPClientManager()
    manager.sessions["s1"] = AsyncMock()
    manager.sessions["s2"] = AsyncMock()

    with patch.object(manager.exit_stack, "aclose", AsyncMock()):
        await manager.stop_all()

    assert len(manager.sessions) == 0


# ---------------------------------------------------------------------------
# search_web — полный fallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_web_all_servers_fail_returns_error_message():
    """Если оба сервера недоступны — возвращается сообщение об ошибке на русском."""
    manager = MCPClientManager()
    manager.ensure_server = AsyncMock(return_value=False)

    result = await manager.search_web("anything")

    assert "❌" in result
    assert "MCP" in result
