# -*- coding: utf-8 -*-
"""
Unit tests для src/mcp_hammerspoon_server.py.

Мокаем HammerspoonBridge чтобы тесты не требуют запущенного Hammerspoon.
Проверяем поведение каждого MCP-tool: успешный путь и error-path.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest  # noqa: I001

import src.mcp_hammerspoon_server as _mod
from src.integrations.hammerspoon_bridge import HammerspoonBridgeError

# --- Fixtures ----------------------------------------------------------------


@pytest.fixture()
def mock_bridge(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Подменяет _bridge в модуле мок-объектом. Возвращает мок."""
    bridge = MagicMock()
    monkeypatch.setattr(_mod, "_bridge", bridge)
    return bridge


# --- hs_is_available ---------------------------------------------------------


def test_hs_is_available_true(mock_bridge: MagicMock) -> None:
    """is_available возвращает True когда Hammerspoon слушает порт."""
    mock_bridge.is_available.return_value = True
    result = _mod.hs_is_available()
    assert result == {"available": True}


def test_hs_is_available_false(mock_bridge: MagicMock) -> None:
    """is_available возвращает False когда Hammerspoon не запущен."""
    mock_bridge.is_available.return_value = False
    result = _mod.hs_is_available()
    assert result == {"available": False}


# --- hs_status ---------------------------------------------------------------


def test_hs_status_ok(mock_bridge: MagicMock) -> None:
    """hs_status возвращает данные статуса при успешном ответе."""
    payload = {"version": "0.9.100", "screens": 2}
    mock_bridge.status = AsyncMock(return_value=payload)
    result = _mod.hs_status()
    assert result == payload


def test_hs_status_bridge_error(mock_bridge: MagicMock) -> None:
    """hs_status возвращает _error dict при HammerspoonBridgeError."""
    mock_bridge.status = AsyncMock(side_effect=HammerspoonBridgeError("connection_failed: timeout"))
    result = _mod.hs_status()
    assert result["_error"] == "HammerspoonBridgeError"
    assert "connection_failed" in result["message"]
    assert "hint" in result


# --- hs_list_windows ---------------------------------------------------------


def test_hs_list_windows_ok(mock_bridge: MagicMock) -> None:
    """hs_list_windows возвращает список окон в обёртке."""
    windows = [{"id": 1, "title": "Terminal", "app": "Terminal"}]
    mock_bridge.list_windows = AsyncMock(return_value=windows)
    result = _mod.hs_list_windows()
    assert result == {"windows": windows}
    # публичный API возвращает копию списка
    assert result["windows"] is not windows


def test_hs_list_windows_empty(mock_bridge: MagicMock) -> None:
    """hs_list_windows возвращает пустой список если нет окон."""
    mock_bridge.list_windows = AsyncMock(return_value=[])
    result = _mod.hs_list_windows()
    assert result == {"windows": []}


def test_hs_list_windows_error(mock_bridge: MagicMock) -> None:
    """hs_list_windows возвращает _error при ошибке bridge."""
    mock_bridge.list_windows = AsyncMock(side_effect=HammerspoonBridgeError("invalid_json"))
    result = _mod.hs_list_windows()
    assert "_error" in result
    assert result["_error"] == "HammerspoonBridgeError"


# --- hs_focus_app ------------------------------------------------------------


def test_hs_focus_app_ok(mock_bridge: MagicMock) -> None:
    """hs_focus_app передаёт имя приложения в bridge.focus и возвращает результат."""
    mock_bridge.focus = AsyncMock(return_value={"ok": True, "app": "Safari"})
    result = _mod.hs_focus_app("Safari")
    mock_bridge.focus.assert_called_once_with("Safari")
    assert result["ok"] is True


def test_hs_focus_app_error(mock_bridge: MagicMock) -> None:
    """hs_focus_app возвращает _error dict при ошибке."""
    mock_bridge.focus = AsyncMock(side_effect=HammerspoonBridgeError("app_not_found"))
    result = _mod.hs_focus_app("NonExistentApp")
    assert result["_error"] == "HammerspoonBridgeError"
    assert "app_not_found" in result["message"]


# --- hs_move_window ----------------------------------------------------------


def test_hs_move_window_ok(mock_bridge: MagicMock) -> None:
    """hs_move_window передаёт все параметры в bridge.move."""
    mock_bridge.move = AsyncMock(return_value={"ok": True})
    result = _mod.hs_move_window(app="Terminal", x=0.0, y=0.0, w=0.5, h=1.0)
    mock_bridge.move.assert_called_once_with(app="Terminal", x=0.0, y=0.0, w=0.5, h=1.0)
    assert result["ok"] is True


def test_hs_move_window_error(mock_bridge: MagicMock) -> None:
    """hs_move_window возвращает _error при ошибке bridge."""
    mock_bridge.move = AsyncMock(side_effect=HammerspoonBridgeError("connection_failed"))
    result = _mod.hs_move_window(app="Terminal", x=0, y=0, w=1, h=1)
    assert "_error" in result


# --- hs_tile -----------------------------------------------------------------


def test_hs_tile_ok(mock_bridge: MagicMock) -> None:
    """hs_tile передаёт preset и app в bridge.tile."""
    mock_bridge.tile = AsyncMock(return_value={"ok": True, "preset": "left"})
    result = _mod.hs_tile(preset="left", app="Finder")
    mock_bridge.tile.assert_called_once_with(preset="left", app="Finder")
    assert result["ok"] is True


def test_hs_tile_default_app(mock_bridge: MagicMock) -> None:
    """hs_tile с пустым app (frontmost window) должен работать корректно."""
    mock_bridge.tile = AsyncMock(return_value={"ok": True})
    result = _mod.hs_tile(preset="full")
    mock_bridge.tile.assert_called_once_with(preset="full", app="")
    assert result["ok"] is True


def test_hs_tile_error(mock_bridge: MagicMock) -> None:
    """hs_tile возвращает _error при неизвестном preset или ошибке bridge."""
    mock_bridge.tile = AsyncMock(side_effect=HammerspoonBridgeError("unknown_preset"))
    result = _mod.hs_tile(preset="diagonal")
    assert result["_error"] == "HammerspoonBridgeError"
    assert "unknown_preset" in result["message"]
    assert "hint" in result
