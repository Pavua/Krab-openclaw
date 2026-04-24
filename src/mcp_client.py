# -*- coding: utf-8 -*-
"""
Клиент MCP для Krab/OpenClaw.

Связи:
- использует единый реестр `src.core.mcp_registry`, чтобы runtime и LM Studio
  поднимали одинаковые MCP-сервера;
- сохраняет текущие удобные обёртки (`search_web`, `read_file`, `write_file`,
  `list_directory`), но больше не хардкодит серверы прямо в коде.
"""

import os
from contextlib import AsyncExitStack
from typing import Any, Dict, List, Optional

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

    async def start_server(
        self, name: str, command: str, args: List[str], env: Optional[Dict[str, str]] = None
    ):
        """Запускает MCP сервер и создает сессию"""
        logger.info("starting_mcp_server", name=name, command=command, args=args)

        server_params = StdioServerParameters(
            command=command, args=args, env={**os.environ, **(env or {})}
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
        if not result or not hasattr(result, "content"):
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
        result = await self.call_tool(
            "filesystem", "write_file", {"path": path, "content": content}
        )
        return "✅ Файл записан." if result else "❌ Ошибка записи."

    async def list_directory(self, path: str) -> str:
        """Список файлов через MCP"""
        if not await self.ensure_server("filesystem"):
            return "❌ Ошибка запуска файлового сервера MCP."

        result = await self.call_tool("filesystem", "list_directory", {"path": path})
        if not result or not hasattr(result, "content"):
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
                    manifest.append(
                        {
                            "type": "function",
                            "function": {
                                "name": f"{server_name}__{tool.name}",
                                "description": tool.description,
                                "parameters": tool.inputSchema,
                            },
                        }
                    )
            except Exception as e:
                logger.error("mcp_list_tools_failed", server=server_name, error=str(e))

        # Добавляем нативные инструменты Краба, если они еще не в MCP
        # peekaboo: скриншот через KrabEarAgent
        manifest.append(
            {
                "type": "function",
                "function": {
                    "name": "peekaboo",
                    "description": "Сделать скриншот экрана macOS для анализа визуального контекста.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "reason": {"type": "string", "description": "Зачем нужен скриншот"}
                        },
                    },
                },
            }
        )
        # web_search: поиск в интернете через Brave / Firecrawl
        manifest.append(
            {
                "type": "function",
                "function": {
                    "name": "web_search",
                    "description": "Поиск информации в интернете. Используй для актуальных данных: цены, новости, факты, документация.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "Поисковый запрос"}
                        },
                        "required": ["query"],
                    },
                },
            }
        )
        # tor_fetch: анонимный HTTP запрос через Tor (если включён)
        from . import config as _cfg

        if getattr(_cfg, "TOR_ENABLED", False):
            manifest.append(
                {
                    "type": "function",
                    "function": {
                        "name": "tor_fetch",
                        "description": "Анонимный HTTP GET запрос через Tor SOCKS5 proxy. Для .onion сайтов и анонимного доступа.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "url": {"type": "string", "description": "URL для запроса"},
                            },
                            "required": ["url"],
                        },
                    },
                }
            )
        # voice_assistant_tools: voice channel MCP tools (VA Phase 1.4)
        try:
            from .mcp_tools.voice_assistant_tools import VOICE_TOOL_SCHEMAS

            for schema in VOICE_TOOL_SCHEMAS:
                manifest.append(
                    {
                        "type": "function",
                        "function": {
                            "name": schema["name"],
                            "description": schema["description"],
                            "parameters": schema["inputSchema"],
                        },
                    }
                )
        except ImportError:
            pass  # voice_assistant_tools не установлены — не критично

        return manifest

    async def call_tool_unified(self, full_tool_name: str, arguments: Dict[str, Any]) -> str:
        """
        Вызывает инструмент по полному имени (server__tool) или нативному имени.
        """
        # Per-team allowlist guard: если активен swarm-контекст, проверяем что
        # tool разрешён команде. Silent strip + WARN + Prometheus метрика.
        try:
            from .core.swarm_tool_allowlist import (
                get_current_team,
                is_tool_allowed,
                record_blocked_tool,
            )

            _team = get_current_team()
            if _team and not is_tool_allowed(full_tool_name, _team):
                record_blocked_tool(_team, full_tool_name)
                logger.warning(
                    "swarm_tool_blocked",
                    team=_team,
                    tool=full_tool_name,
                )
                return f"❌ Инструмент `{full_tool_name}` недоступен команде `{_team}`."
        except Exception as _guard_exc:  # noqa: BLE001
            logger.warning("swarm_tool_guard_failed", error=str(_guard_exc))

        if full_tool_name == "peekaboo":
            return await self._peekaboo_impl(arguments)

        if full_tool_name == "web_search":
            return await self._web_search_impl(arguments)

        if full_tool_name == "tor_fetch":
            return await self._tor_fetch_impl(arguments)

        if full_tool_name.startswith("voice:"):
            return await self._voice_tool_impl(full_tool_name, arguments)

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

    async def _web_search_impl(self, arguments: Dict[str, Any]) -> str:
        """Реализация web_search через search_web (Brave/Firecrawl)."""
        query = str(arguments.get("query", "")).strip()
        if not query:
            return "❌ Пустой по��сковый запрос"
        try:
            results = await self.search_web(query)
            return results or "Ничего не найдено."
        except Exception as e:
            logger.error("web_search_tool_failed", query=query, error=repr(e))
            return f"❌ ��шибка поиска: {e}"

    async def _tor_fetch_impl(self, arguments: Dict[str, Any]) -> str:
        """Реализация tor_fetch через tor_bridge."""
        url = str(arguments.get("url", "")).strip()
        if not url:
            return "❌ URL не указан"
        try:
            from .integrations.tor_bridge import tor_fetch

            result = await tor_fetch(url, timeout=30.0)
            if result.get("ok"):
                text = str(result.get("text", ""))
                return text[:8000] if len(text) > 8000 else text
            return f"❌ Tor fetch error: {result.get('error', 'unknown')}"
        except Exception as e:
            logger.error("tor_fetch_tool_failed", url=url, error=repr(e))
            return f"❌ Ошибка tor_fetch: {e}"

    async def _voice_tool_impl(self, tool_name: str, arguments: Dict[str, Any]) -> str:
        """Делегирует вызовы voice:* инструментов в voice_assistant_tools."""
        try:
            from .mcp_tools.voice_assistant_tools import dispatch_voice_tool

            result = await dispatch_voice_tool(tool_name, arguments)
            if isinstance(result, dict):
                import json as _json

                return _json.dumps(result, ensure_ascii=False)
            return str(result)
        except Exception as exc:  # noqa: BLE001
            logger.error("voice_tool_impl_error", tool=tool_name, error=str(exc))
            return f"voice_tool_error: {exc}"

    async def health_check(self) -> dict:
        """Возвращает статус MCP relay для capability_registry._probe_status().

        Формат: {"ok": bool, "count": int, "error": str}
        - ok=True  → is_running и есть хотя бы одна активная сессия
        - ok=False → не запущен или нет активных сессий
        """
        if not self.is_running:
            return {"ok": False, "count": 0, "error": "not_started"}
        count = len(self.sessions)
        if count == 0:
            return {"ok": False, "count": 0, "error": "no_active_sessions"}
        return {"ok": True, "count": count, "error": ""}

    async def stop_all(self):
        """Остановка всех серверов"""
        await self.exit_stack.aclose()
        self.sessions.clear()
        logger.info("mcp_all_stopped")


mcp_manager = MCPClientManager()
