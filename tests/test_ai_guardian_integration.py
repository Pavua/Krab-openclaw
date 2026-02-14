# -*- coding: utf-8 -*-
"""
Интеграционный тест AI Guardian + Group Moderation Engine.
"""

import pytest
import asyncio
from src.core.ai_guardian_client import AIGuardianClient
from src.core.group_moderation_engine import GroupModerationEngine

@pytest.mark.asyncio
async def test_ai_guardian_message_evaluation():
    # 1. Инициализация клиента
    client = AIGuardianClient(base_url="http://localhost:8000")
    
    # Проверка связи
    is_up = await client.health_check()
    if not is_up:
        pytest.skip("AI Guardian is not running. Skipping integration test.")
    
    # 2. Инициализация движка
    engine = GroupModerationEngine(ai_guardian=client)
    
    # 3. Тест безопасного сообщения
    res1 = await engine.evaluate_message(123, "Привет, как дела?")
    assert res1["matched"] is False
    
    # 4. Тест опасного сообщения (Доксинг IP)
    # AI Guardian по heuristic ловит IP адреса
    res2 = await engine.evaluate_message(123, "Мой IP адрес 192.168.1.1")
    assert res2["matched"] is True
    assert res2["primary_rule"] == "ai_guardian"
    assert "IPv4" in res2["violations"][0]["reason"]
    
    # 5. Тест опасного сообщения (Спам)
    res3 = await engine.evaluate_message(123, "Buy crypto fast on our telegram channel")
    assert res3["matched"] is True
    assert res3["primary_rule"] == "ai_guardian"
    assert "Спам" in res3["violations"][0]["reason"] or "crypto" in res3["violations"][0]["reason"].lower()

@pytest.mark.asyncio
async def test_ai_guardian_chat_support():
    client = AIGuardianClient(base_url="http://localhost:8000")
    if not await client.health_check():
        pytest.skip("AI Guardian is not running.")
        
    # В knowledge_base.json (mock) обычно есть вопросы
    # Но даже если нет, chat_support должен вернуть fallback
    res = await client.get_chat_response("Hello")
    assert "response" in res
    assert "source" in res
