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
from src.utils.web_scout import WebScout
# ПРИМЕЧАНИЕ: SwarmOrchestrator был удалён при рефакторинге v7.0.
# Оригинал сохранён в src/archive/legacy/v6_backup/
# Вместо полноценного Swarm используем легковесную заглушку,
# которая делегирует решение напрямую через tool chain без оверхеда.

logger = structlog.get_logger("ToolHandler")


class ToolHandler:
    def __init__(self, router, rag, scout: WebScout, mcp=None):
        self.router = router
        self.rag = rag
        self.scout = scout
        self.mcp = mcp  # Инстанс MCPManager
        # Swarm будет восстановлен в Phase 10, пока используем прямой вызов
        
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
        # Прямая логика вместо Swarm: ищем ключевые слова для tool selection
        result_parts = []
        
        # Веб-поиск если запрос похож на поисковый
        search_triggers = ['поищи', 'найди', 'новости', 'что такое', 'кто такой', 'когда', 'где']
        query_lower = query.lower()
        
        if any(trigger in query_lower for trigger in search_triggers):
            try:
                search_result = await self.scout.search(query)
                if search_result:
                    result_parts.append(f"🌐 Результаты поиска:\n{search_result}")
            except Exception as e:
                logger.warning(f"Web search failed: {e}")
        
        # MCP tools если доступны
        if self.mcp:
            try:
                mcp_result = await self.mcp.auto_route(query)
                if mcp_result:
                    result_parts.append(f"🔧 MCP:\n{mcp_result}")
            except Exception as e:
                logger.debug(f"MCP auto-route не сработал: {e}")
        
        return "\n\n".join(result_parts) if result_parts else None

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
            tools.append({"name": "MCP Client", "status": "✅", "trigger": "Filesystem/Search/Memory"})
        
        return tools

    async def call_mcp_tool(self, server_name: str, tool_name: str, arguments: dict) -> Any:
        """Прямой вызов MCP инструмента."""
        if not self.mcp:
            return "❌ MCP Manager не инициализирован"
        return await self.mcp.call_tool(server_name, tool_name, arguments)
