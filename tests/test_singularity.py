# -*- coding: utf-8 -*-
"""
Unit Tests for Krab v5.0 Singularity Modules.
Tests: SystemMonitor, MacAutomation, DocumentParser, SelfRefactor.
"""

import pytest
import os
from pathlib import Path
from src.utils.system_monitor import SystemMonitor
from src.utils.mac_bridge import MacAutomation
from src.utils.doc_parser import DocumentParser
from src.utils.self_refactor import SelfRefactor
from src.core.security_manager import SecurityManager

class MockRouter:
    def __init__(self):
        self.gemini_key = "test_key"
    async def route_query(self, prompt, task_type='chat', context=None):
        return "Proposal: Change code to be better. \n```python\nprint('hello')\n```"

@pytest.mark.asyncio
async def test_system_monitor_snapshot():
    """Проверка создания снимка системы."""
    snapshot = SystemMonitor.get_snapshot()
    assert snapshot.ram_percent >= 0
    assert snapshot.cpu_percent >= 0
    assert snapshot.disk_percent >= 0
    report = snapshot.format_report()
    assert "RAM" in report
    assert "CPU" in report

def test_system_monitor_process():
    """Проверка инфо о процессе."""
    info = SystemMonitor.get_process_info()
    assert info['pid'] > 0
    assert info['ram_mb'] > 0

@pytest.mark.asyncio
async def test_mac_bridge_basic():
    """Проверка базовых вызовов macOS Bridge (безопасные методы)."""
    battery = await MacAutomation.get_battery_status()
    assert isinstance(battery, str)
    wifi = await MacAutomation.get_wifi_name()
    assert isinstance(wifi, str)

@pytest.mark.asyncio
async def test_doc_parser_txt():
    """Тест парсинга текстового файла."""
    test_file = Path("artifacts/test_unit.txt")
    test_file.write_text("Hello Unit Test", encoding="utf-8")
    try:
        text, meta = await DocumentParser.parse(str(test_file))
        assert "Hello Unit Test" in text
        assert meta['extension'] == ".txt"
    finally:
        if test_file.exists():
            test_file.unlink()

@pytest.mark.asyncio
async def test_self_refactor_structure():
    """Проверка получения структуры проекта."""
    refactorer = SelfRefactor(os.getcwd())
    structure = refactorer.get_project_structure()
    assert "src/" in structure
    assert "main.py" in structure

@pytest.mark.asyncio
async def test_self_refactor_analysis():
    """Проверка генерации предложений по рефакторингу (Mock AI)."""
    refactorer = SelfRefactor(os.getcwd())
    router = MockRouter()
    # Создаем временный файл для анализа
    test_file = Path("artifacts/test_refactor.py")
    test_file.write_text("def test(): pass", encoding="utf-8")
    
    try:
        proposal = await refactorer.analyze_and_propose(router, "artifacts/test_refactor.py")
        assert "Proposal" in proposal
        assert "```python" in proposal
    finally:
        if test_file.exists():
            test_file.unlink()

def test_doc_parser_unsupported():
    """Проверка обработки неподдерживаемых форматов."""
    assert DocumentParser.is_supported("test.exe") is False
    assert DocumentParser.is_supported("test.pdf") is True

def test_security_stealth_mode():
    """Тест режима Stealth Mode в SecurityManager."""
    sec = SecurityManager(owner_username="p0lrd")
    
    # По умолчанию
    assert sec.stealth_mode is False
    assert sec.get_user_role("guest", 123) == "user"
    
    # Включаем скрытность
    sec.toggle_stealth()
    assert sec.stealth_mode is True
    assert sec.get_user_role("guest", 123) == "stealth_restricted"
    assert sec.get_user_role("p0lrd", 0) == "owner"
    assert sec.can_execute_command("guest", 123) is False
    
    # Выключаем
    sec.toggle_stealth()
    assert sec.stealth_mode is False
    assert sec.get_user_role("guest", 123) == "user"
