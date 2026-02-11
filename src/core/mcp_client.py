# -*- coding: utf-8 -*-
"""
MCP Client Manager v1.0 (Phase 10).
Управляет подключениями к Model Context Protocol серверам.
Интегрирует внешние инструменты (filesystem, memory, web-search) в экосистему Krab.
"""

import asyncio
import structlog
from typing import Dict, List, Optional, Any
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

logger = structlog.get_logger("MCPManager")

import json
import os
from contextlib import AsyncExitStack

class MCPManager:
    def __init__(self, config_path: str = "config/mcp_servers.json"):
        """
        Улучшенный менеджер MCP (v1.1).
        config_path: Путь к файлу конфигурации.
        """
        self.config_path = config_path
        self.configs = self._load_config()
        self.sessions: Dict[str, ClientSession] = {}
        self.exit_stack = AsyncExitStack()

    def _load_config(self) -> Dict:
        """Загрузка конфигурации из файла."""
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    return data.get("mcpServers", {})
            except Exception as e:
                logger.error(f"Failed to load MCP config: {e}")
        return {}

    async def connect_all(self):
        """Подключение ко всем настроенным серверам."""
        for name in self.configs:
            await self.connect_to_server(name)

    async def connect_to_server(self, name: str) -> bool:
        """Подключение к MCP-серверу и удержание сессии."""
        if name not in self.configs:
            return False

        conf = self.configs[name]
        logger.info(f"🔌 Connecting to MCP server: {name}")

        # Объединяем системное окружение с тем, что в конфиге
        env = os.environ.copy()
        if conf.get("env"):
            env.update(conf["env"])
            
        params = StdioServerParameters(
            command=conf["command"],
            args=conf.get("args", []),
            env=env
        )

        try:
            # Используем ExitStack для управления жизненным циклом
            read, write = await self.exit_stack.enter_async_context(stdio_client(params))
            session = await self.exit_stack.enter_async_context(ClientSession(read, write))
            
            await session.initialize()
            self.sessions[name] = session
            logger.info(f"✅ MCP server '{name}' ready")
            return True
        except Exception as e:
            logger.error(f"Failed to connect to MCP '{name}': {e}")
            return False

    async def call_tool(self, server_name: str, tool_name: str, arguments: Dict) -> Any:
        """Вызов инструмента на сервере."""
        session = self.sessions.get(server_name)
        if not session:
            # Пытаемся подключиться, если сессия отсутствует
            if await self.connect_to_server(server_name):
                session = self.sessions.get(server_name)
            else:
                return f"Error: Server '{server_name}' is not available."
        
        try:
            result = await session.call_tool(tool_name, arguments)
            return result
        except Exception as e:
            logger.error(f"MCP Tool Error ({server_name}/{tool_name}): {e}")
            return f"Error executing tool: {e}"

    async def shutdown(self):
        """Закрытие всех сессий и очистка стека."""
        logger.info("🔌 Shutting down all MCP sessions...")
        await self.exit_stack.aclose()
        self.sessions.clear()

# Singleton
mcp_manager = MCPManager()
