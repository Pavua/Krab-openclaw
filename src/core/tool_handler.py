# -*- coding: utf-8 -*-
"""
Tool Handler v2.0 (Phase 8).
Интерфейс для использования внешних инструментов.

Доступные инструменты:
- Web Search (WebScout) — поиск в интернете
- RAG Memory — долгосрочная память
- Shell — выполнение команд (Owner only)
- macOS Bridge — управление macOS (Owner only)
- Document Parser — анализ файлов (PDF, DOCX, etc.)
- System Monitor — мониторинг ресурсов

Связь: Вызывается из model_manager.py (route_query) для автоматического
обогащения запросов данными из инструментов.
"""

import structlog
import json
from typing import Any
# from src.utils.web_scout import WebScout # Deprecated
from src.core.swarm import SwarmOrchestrator

logger = structlog.get_logger("ToolHandler")


class ToolHandler:
    def __init__(self, router, rag, openclaw_client, mcp=None, browser_agent=None, crypto_intel=None, reminder_manager=None):
        self.router = router
        self.rag = rag
        self.openclaw = openclaw_client
        self.mcp = mcp  # Инстанс MCPManager
        self.browser_agent = browser_agent
        self.crypto_intel = crypto_intel
        self.reminder_manager = reminder_manager
        self.swarm = SwarmOrchestrator(self, router)  # Система Роя (Phase 10)
        
        # Ленивая инициализация опциональных модулей
        self._mac_bridge = None
        self._doc_parser = None
        self._system_monitor = None

    @property
    def mac_bridge(self):
        """Ленивая загрузка macOS Bridge."""
        if self._mac_bridge is None:
            try:
                from src.utils.mac_bridge import MacAutomation
                self._mac_bridge = MacAutomation
                logger.info("🍎 macOS Bridge загружен")
            except ImportError:
                logger.warning("macOS Bridge недоступен")
        return self._mac_bridge

    @property
    def doc_parser(self):
        """Ленивая загрузка Document Parser."""
        if self._doc_parser is None:
            try:
                from src.utils.doc_parser import DocumentParser
                self._doc_parser = DocumentParser
                logger.info("📄 Document Parser загружен")
            except ImportError:
                logger.warning("Document Parser недоступен")
        return self._doc_parser

    @property
    def system_monitor(self):
        """Ленивая загрузка System Monitor."""
        if self._system_monitor is None:
            try:
                from src.utils.system_monitor import SystemMonitor
                self._system_monitor = SystemMonitor
                logger.info("🖥️ System Monitor загружен")
            except ImportError:
                logger.warning("System Monitor недоступен")
        return self._system_monitor

    async def execute_tool_chain(self, query: str) -> str:
        """
        AI-driven Tool Selection (Phase 10):
        Использует SwarmOrchestrator для параллельного выполнения задач.
        """
        return await self.swarm.autonomous_decision(query)

    async def run_shell(self, command: str) -> str:
        """Выполнение системных команд (Owner only)."""
        import asyncio
        
        logger.info("Decision: Executing shell command", command=command)
        
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            # Таймаут 30 секунд для безопасности
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
            except asyncio.TimeoutError:
                proc.kill()
                return "⏰ Таймаут: команда выполнялась дольше 30 секунд и была прервана."
            
            result = stdout.decode().strip()
            error = stderr.decode().strip()
            
            output = ""
            if result:
                output += f"Output:\n{result}\n"
            if error:
                output += f"Error:\n{error}\n"
            
            # Ограничиваем вывод (Telegram макс 4096 символов)
            if len(output) > 3500:
                output = output[:3500] + "\n... [вывод обрезан]"
                
            return output or "Команда выполнена успешно (нет вывода)."
            
        except Exception as e:
            logger.error("Shell execution error", error=str(e))
            return f"Ошибка выполнения: {e}"

    async def run_mac_intent(self, intent: str, params: dict = None) -> str:
        """
        Выполнение macOS-команды через MacAutomation Bridge.
        Пример: intent="notification", params={"title": "Test", "message": "Hello"}
        """
        if not self.mac_bridge:
            return "❌ macOS Bridge недоступен"
        
        return await self.mac_bridge.execute_intent(intent, params)

    async def parse_document(self, file_path: str) -> tuple:
        """
        Парсинг документа через DocumentParser.
        Возвращает (текст, метаданные).
        """
        if not self.doc_parser:
            return "❌ Document Parser недоступен", {}
        
        return await self.doc_parser.parse(file_path)

    def get_tool_registry(self) -> str:
        """Возвращает текстовое описание инструментов для LLM."""
        registry = [
            "1. web_search(query: str) - Поиск свежей информации в интернете.",
            "2. rag_search(query: str) - Поиск в твоей долгосрочной памяти (RAG).",
            "3. shell_exec(command: str) - Выполнение команд в терминале macOS.",
            "4. mac_automation(intent: str, params: dict) - Управление macOS (уведомления, запуск приложений).",
            "5. system_info() - Получение данных о загрузке CPU/RAM."
        ]
        if self.mcp:
            registry.append("6. mcp_term(server: str, tool: str, args: dict) - Вызов инструментов из MCP-серверов.")
            
        if self.browser_agent:
            registry.append("7. browse(url: str) - Прочитать содержимое веб-страницы.")
            registry.append("8. screenshot(url: str) - Сделать скриншот веб-страницы.")

        if self.crypto_intel:
            registry.append("9. crypto_price(symbol: str) - Узнать цену криптовалюты (btc, eth, sol).")
        
        if self.reminder_manager:
            registry.append("10. add_reminder(text: str, time: str) - Поставить напоминание. Время можно указывать фразой 'через 5 минут' или 'в 10:00'.")
            registry.append("11. list_reminders() - Показать список активных напоминаний.")

        return "\n".join(registry)

    async def execute_named_tool(self, name: str, **kwargs) -> str:
        """Единая точка входа для исполнения инструментов по имени."""
        logger.info(f"🛠️ Executing tool: {name}", args=kwargs)
        try:
            if name == "web_search":
                # res = await self.scout.search(kwargs.get("query", ""))
                # return self.scout.format_results(res)
                # Use OpenClaw
                response = await self.openclaw.invoke_tool("web_search", {
                    "query": kwargs.get("query", ""),
                    "count": 5
                })
                # Format logic similar to other places, or just dump string
                # For basic tool execution, we might return raw string or simple text
                results = response.get("details", {}).get("results", [])
                
                # Fallback parse
                if not results and "content" in response:
                    try:
                        import json
                        text = response["content"][0]["text"]
                        results = json.loads(text).get("results", [])
                    except: pass

                if not results: return "❌ No results found via OpenClaw."
                
                start_text = "🔎 **OpenClaw Search Results:**\n"
                for i, r in enumerate(results, 1):
                    if isinstance(r, dict):
                        start_text += f"{i}. [{r.get('title')}]({r.get('url')})\n"
                    else:
                        start_text += f"{i}. {r}\n"
                return start_text
            elif name == "rag_search":
                return self.rag.query(kwargs.get("query", ""))
            elif name == "shell_exec":
                return await self.run_shell(kwargs.get("command", ""))
            elif name == "mac_automation":
                return await self.run_mac_intent(kwargs.get("intent", ""), kwargs.get("params", {}))
            elif name == "system_info":
                return str(self.system_monitor.get_snapshot().to_dict()) if self.system_monitor else "Monitor offline"
            elif name == "mcp_tool":
                res = await self.call_mcp_tool(kwargs.get("server", ""), kwargs.get("tool", ""), kwargs.get("args", {}))
                return str(res)
            elif name == "browse":
                if not self.browser_agent: return "❌ Browser Agent не подключен"
                res = await self.browser_agent.browse(kwargs.get("url", ""))
                if "error" in res: return f"❌ Ошибка браузера: {res['error']}"
                return f"📄 Title: {res['title']}\nURL: {res['url']}\nContent:\n{res['content']}"
            elif name == "screenshot":
                if not self.browser_agent: return "❌ Browser Agent не подключен"
                path = await self.browser_agent.screenshot_only(kwargs.get("url", ""))
                return f"📸 Скриншот сохранен: {path}"
            elif name == "crypto_price":
                if not self.crypto_intel: return "❌ Crypto module not loaded"
                symbol = kwargs.get("symbol", "bitcoin").lower()
                data = await self.crypto_intel.get_price(symbol)
                if "error" in data: return f"❌ Error: {data['error']}"
                price = data.get("usd", 0)
                change = data.get("usd_24h_change", 0)
                return f"💰 {symbol.upper()}: ${price:,.2f} ({change:+.2f}%)"
            elif name == "add_reminder":
                if not self.reminder_manager: return "❌ Reminder module not loaded"
                import dateparser
                from datetime import datetime
                time_str = kwargs.get("time", "")
                text = kwargs.get("text", "Без названия")
                parsed_time = dateparser.parse(time_str, settings={'PREFER_DATES_FROM': 'future'})
                if not parsed_time: return "❌ Не удалось распознать время напоминания."
                # В ReAct у нас нет прямого доступа к chat_id в execute_named_tool (он передается в run),
                # но мы можем добавить его в kwargs при вызове в AgentExecutor
                chat_id = kwargs.get("chat_id", 0)
                if not chat_id: return "❌ Не указан chat_id для напоминания."
                rid = self.reminder_manager.add_reminder(chat_id, text, parsed_time)
                return f"✅ Напоминание установлено на {parsed_time.strftime('%Y-%m-%d %H:%M:%S')} (ID: {rid})"
            elif name == "list_reminders":
                if not self.reminder_manager: return "❌ Reminder module not loaded"
                chat_id = kwargs.get("chat_id", 0)
                reminders = self.reminder_manager.get_list(chat_id)
                if not reminders: return "📝 Список напоминаний пуст."
                res = "🗓️ Активные напоминания:\n"
                for r in reminders:
                    res += f"- {r['due_time']}: {r['text']} (ID: {r['id']})\n"
                return res
            else:
                return f"❌ Tool '{name}' not found."
        except Exception as e:
            logger.error(f"Tool execution failed: {name}", error=str(e))
            return f"❌ Error: {e}"

    def get_available_tools(self) -> list:
        """Список доступных инструментов для !help и диагностики."""
        tools = [
            {"name": "Web Search", "status": "✅", "trigger": "поищи/найди/новости"},
            {"name": "RAG Memory", "status": "✅", "trigger": "вспомни/память/архив"},
            {"name": "Shell", "status": "✅", "trigger": "!sh (Owner only)"},
        ]
        
        if self.mac_bridge:
            tools.append({"name": "macOS Bridge", "status": "✅", "trigger": "!mac"})
        else:
            tools.append({"name": "macOS Bridge", "status": "⚠️", "trigger": "модуль не загружен"})
        
        if self.doc_parser:
            tools.append({"name": "Document Parser", "status": "✅", "trigger": "отправь документ"})
        
        if self.system_monitor:
            tools.append({"name": "System Monitor", "status": "✅", "trigger": "!sysinfo"})
        
        if self.mcp:
            tools.append({"name": "MCP Client", "status": "✅", "trigger": "Filesystem/GitHub"})
            
        if self.crypto_intel:
             tools.append({"name": "Crypto Intel", "status": "✅", "trigger": "!crypto"})
        
        return tools

    async def call_mcp_tool(self, server_name: str, tool_name: str, arguments: dict) -> Any:
        """Прямой вызов MCP инструмента."""
        if not self.mcp:
            return "❌ MCP Manager не инициализирован"
        return await self.mcp.call_tool(server_name, tool_name, arguments)
