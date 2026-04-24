"""Shared loader для MCP tool-тестов.

MCP-сервер живёт в mcp-servers/telegram/server.py. Чтобы тесты не стартовали
настоящий TelegramBridge (нужен session-файл), мы стаббим модуль telegram_bridge
перед импортом server.py.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_SERVER_PATH = _PROJECT_ROOT / "mcp-servers" / "telegram" / "server.py"


@pytest.fixture(scope="session")
def mcp_server():
    """Импортирует mcp-servers/telegram/server.py с застабленным TelegramBridge."""
    # Стаб telegram_bridge — чтобы не поднимать Pyrogram
    if "telegram_bridge" not in sys.modules:
        stub = types.ModuleType("telegram_bridge")

        class _DummyBridge:
            async def start(self):
                return None

            async def stop(self):
                return None

        stub.TelegramBridge = _DummyBridge
        sys.modules["telegram_bridge"] = stub

    # Путь к mcp-servers/telegram нужен для внутренних импортов сервера
    server_dir = str(_SERVER_PATH.parent)
    if server_dir not in sys.path:
        sys.path.insert(0, server_dir)
    if str(_PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(_PROJECT_ROOT))

    spec = importlib.util.spec_from_file_location("krab_mcp_server_under_test", _SERVER_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
