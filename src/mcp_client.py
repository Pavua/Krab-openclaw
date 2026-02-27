
import asyncio
import os
import json
from contextlib import AsyncExitStack
from typing import Optional, List, Dict, Any
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from structlog import get_logger
from .config import config

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

    async def ensure_server(self, name: str):
        """Гарантирует, что сервер запущен"""
        if name in self.sessions:
            return True
            
        if name == "brave":
            server_script = os.path.join(config.BASE_DIR, "mcp-servers/node_modules/@modelcontextprotocol/server-brave-search/dist/index.js")
            env = {"BRAVE_API_KEY": config.BRAVE_SEARCH_API_KEY}
            return await self.start_server("brave", "node", [server_script], env=env)
            
        elif name == "filesystem":
            server_script = os.path.join(config.BASE_DIR, "mcp-servers/node_modules/@modelcontextprotocol/server-filesystem/dist/index.js")
            # Разрешаем доступ только к папке проекта
            allowed_dir = str(config.BASE_DIR)
            return await self.start_server("filesystem", "node", [server_script, allowed_dir])
            
        return False

    async def search_web(self, query: str) -> str:
        """Удобная обертка для поиска через Brave Search MCP"""
        if not await self.ensure_server("brave"):
            return "❌ Ошибка запуска поискового сервера MCP."

        # Вызываем инструмент brave_web_search
        # (Название инструмента может отличаться, проверяем в документации MCP сервера)
        logger.info("executing_web_search", query=query)
        result = await self.call_tool("brave", "brave_web_search", {"query": query})
        
        if not result or not hasattr(result, 'content'):
            return "❌ Ничего не найдено."
            
        # Форматируем результат (текстовый контент)
        try:
            # MCP ToolResult возвращает контент как список MessageContent
            text_results = []
            for item in result.content:
                if hasattr(item, 'text'):
                    text_results.append(item.text)
            
            return "\n\n".join(text_results)
        except (AttributeError, IndexError, KeyError, TypeError):
            return str(result)

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

    async def stop_all(self):
        """Остановка всех серверов"""
        await self.exit_stack.aclose()
        self.sessions.clear()
        logger.info("mcp_all_stopped")

mcp_manager = MCPClientManager()
