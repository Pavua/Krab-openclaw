# -*- coding: utf-8 -*-
"""
Клиент MCP для Krab/OpenClaw.

Связи:
- использует единый реестр `src.core.mcp_registry`, чтобы runtime и LM Studio
  поднимали одинаковые MCP-сервера;
- сохраняет текущие удобные обёртки (`search_web`, `read_file`, `write_file`,
  `list_directory`), но больше не хардкодит серверы прямо в коде.
"""

from contextlib import AsyncExitStack
import os
from typing import Optional, List, Dict, Any
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from structlog import get_logger
from .core.mcp_registry import get_managed_mcp_servers, resolve_managed_server_launch

logger = get_logger(__name__)

class MCPClientManager:
    """
    Управляет подключениями к MCP серверам.
    """
    def __init__(self):
        self.sessions: Dict[str, ClientSession] = {}
        self.exit_stack = AsyncExitStack()
        self.is_running = False

    async def start_server(self, name: str, command: str, args: List[str], env: Optional[Dict[str, str]] = None):
        """Запускает MCP сервер и создает сессию"""
        logger.info("starting_mcp_server", name=name, command=command, args=args)
        
        server_params = StdioServerParameters(
            command=command,
            args=args,
            env={**os.environ, **(env or {})}
        )
        
        # Используем асинхронный контекстный менеджер через ExitStack если нужно, 
        # но для простоты здесь сделаем прямое подключение.
        # В mcp-python SDK stdio_client возвращает контекстный менеджер.
        
        try:
            # Важно: stdio_client должен оставаться открытым.
            # Для долгоживущего клиента мы можем запустить его в отдельной задаче или хранить контекст.
            # Но SDK mcp-python накладывает ограничения на использование сессии вне контекста.
            
            # Мы будем использовать паттерн "одна команда - одна сессия" для поиска, 
            # или держать сессию открытой. Для поиска в юзерботе лучше держать открытой.
            
            transport = await self.exit_stack.enter_async_context(stdio_client(server_params))
            read, write = transport
            session = await self.exit_stack.enter_async_context(ClientSession(read, write))
            
            await session.initialize()
            self.sessions[name] = session
            logger.info("mcp_server_ready", name=name)
            return True
        except (OSError, ConnectionError, ValueError, KeyError) as e:
            logger.error("mcp_server_failed", name=name, error=str(e))
            return False

    async def call_tool(self, server_name: str, tool_name: str, arguments: Dict[str, Any]) -> Any:
        """Вызывает инструмент на указанном сервере"""
        session = self.sessions.get(server_name)
        if not session:
            logger.warning("mcp_session_not_found", server=server_name)
            return None
        
        try:
            result = await session.call_tool(tool_name, arguments)
            return result
        except (ConnectionError, ValueError, AttributeError, RuntimeError) as e:
            logger.error("mcp_tool_error", server=server_name, tool=tool_name, error=str(e))
            return None

    @staticmethod
    def _format_tool_result(result: Any) -> str:
        """Нормализует текстовый ToolResult в обычную строку."""
        if not result or not hasattr(result, "content"):
            return ""
        try:
            text_results = []
            for item in result.content:
                if hasattr(item, "text"):
                    text_results.append(item.text)
            return "\n\n".join(text_results)
        except (AttributeError, IndexError, KeyError, TypeError):
            return str(result)

    async def ensure_server(self, name: str):
        """Гарантирует, что сервер запущен"""
        if name in self.sessions:
            return True

        aliases = {
            "brave": "brave-search",
        }
        resolved_name = aliases.get(name, name)
        if resolved_name not in get_managed_mcp_servers():
            logger.warning("mcp_server_unknown", server=name, resolved=resolved_name)
            return False

        launch = resolve_managed_server_launch(resolved_name)
        missing_env = list(launch.get("missing_env", []))
        if missing_env:
            logger.warning(
                "mcp_server_missing_env",
                server=resolved_name,
                missing_env=missing_env,
            )
            return False

        return await self.start_server(
            resolved_name,
            launch["command"],
            list(launch["args"]),
            env=launch["env"],
        )

    async def search_web(self, query: str) -> str:
        """
        Веб-поиск через MCP с fallback-порядком.

        Порядок:
        1. `brave-search`
        2. `firecrawl`
        """
        search_chain = (
            ("brave-search", "brave_web_search"),
            ("firecrawl", "firecrawl_search"),
        )

        for server_name, tool_name in search_chain:
            if not await self.ensure_server(server_name):
                continue
            logger.info("executing_web_search", query=query, server=server_name, tool=tool_name)
            result = await self.call_tool(server_name, tool_name, {"query": query})
            rendered = self._format_tool_result(result)
            if rendered:
                return rendered

        return "❌ Поисковые MCP серверы недоступны: проверь BRAVE_SEARCH_API_KEY или FIRECRAWL_API_KEY."

    async def read_file(self, path: str) -> str:
        """Чтение файла через MCP"""
        if not await self.ensure_server("filesystem"):
            return "❌ Ошибка запуска файлового сервера MCP."
            
        # MCP server-filesystem использует инструмент 'read_file'
        result = await self.call_tool("filesystem", "read_file", {"path": path})
        if not result or not hasattr(result, 'content'):
            return "❌ Ошибка чтения файла."
        
        try:
            return result.content[0].text
        except (AttributeError, IndexError, KeyError, TypeError):
            return str(result)

    async def write_file(self, path: str, content: str) -> str:
        """Запись файла через MCP"""
        if not await self.ensure_server("filesystem"):
            return "❌ Ошибка запуска файлового сервера MCP."

        # Используем инструмент 'write_file' (если доступен, иначе надо проверить список инструментов)
        # Обычно это write_file или edit_file. В server-filesystem это 'write_file'.
        result = await self.call_tool("filesystem", "write_file", {"path": path, "content": content})
        return "✅ Файл записан." if result else "❌ Ошибка записи."
        
    async def list_directory(self, path: str) -> str:
        """Список файлов через MCP"""
        if not await self.ensure_server("filesystem"):
            return "❌ Ошибка запуска файлового сервера MCP."

        result = await self.call_tool("filesystem", "list_directory", {"path": path})
        if not result or not hasattr(result, 'content'):
            return "❌ Ошибка листинга."
            
        try:
            # Обычно возвращает список строк
            return result.content[0].text
        except (AttributeError, IndexError, KeyError, TypeError):
            return str(result)

    async def get_tool_manifest(self) -> List[Dict[str, Any]]:
        """
        Собирает список всех доступных инструментов от всех активных MCP сессий.
        Форматирует их в OpenAI-совместимый Tool Definition.
        """
        manifest = []
        for server_name, session in self.sessions.items():
            try:
                tools_result = await session.list_tools()
                # Обычно SDK mcp-python возвращает объект с полем .tools
                tools = getattr(tools_result, "tools", [])
                for tool in tools:
                    manifest.append({
                        "type": "function",
                        "function": {
                            "name": f"{server_name}__{tool.name}",
                            "description": tool.description,
                            "parameters": tool.inputSchema,
                        }
                    })
            except Exception as e:
                logger.error("mcp_list_tools_failed", server=server_name, error=str(e))
        
        # Добавляем нативные инструменты Краба, если они еще не в MCP
        # peekaboo: скриншот через KrabEarAgent
        manifest.append({
            "type": "function",
            "function": {
                "name": "peekaboo",
                "description": "Сделать скриншот экрана macOS для анализа визуального контекста.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "reason": {"type": "string", "description": "Зачем нужен скриншот"}
                    }
                }
            }
        })
        return manifest

    async def call_tool_unified(self, full_tool_name: str, arguments: Dict[str, Any]) -> str:
        """
        Вызывает инструмент по полному имени (server__tool) или нативному имени.
        """
        if full_tool_name == "peekaboo":
            return await self._peekaboo_impl(arguments)

        if "__" not in full_tool_name:
            return f"❌ Неизвестный формат инструмента: {full_tool_name}"
        
        server_name, tool_name = full_tool_name.split("__", 1)
        result = await self.call_tool(server_name, tool_name, arguments)
        return self._format_tool_result(result)

    async def _peekaboo_impl(self, arguments: Dict[str, Any]) -> str:
        """
        Реализация peekaboo через локальный KrabEarAgent.
        """
        import httpx
        try:
            # KrabEarAgent работает на 5005 порту (согласно предыдущей сессии)
            url = "http://127.0.0.1:5005/screenshot"
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    data = resp.json()
                    path = data.get("path", "")
                    # Мы возвращаем путь к файлу для Vision-обработки или просто подтверждение
                    return f"✅ Скриншот сделан и сохранен: {path}. Я его вижу."
                return f"❌ Ошибка KrabEarAgent: {resp.status_code}"
        except Exception as e:
            return f"❌ Ошибка peekaboo: {str(e)}"

    async def stop_all(self):
        """Остановка всех серверов"""
        await self.exit_stack.aclose()
        self.sessions.clear()
        logger.info("mcp_all_stopped")

mcp_manager = MCPClientManager()
