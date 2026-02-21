# -*- coding: utf-8 -*-
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from src.core.model_manager import ModelRouter

@pytest.mark.asyncio
async def test_full_chain_intelligence():
    """Тест полной цепочки: Промпт -> Инструмент -> AI Ответ"""
    router = ModelRouter({"GEMINI_API_KEY": "fake"})
    router.check_local_health = AsyncMock()
    router._call_gemini = AsyncMock(return_value="The results are in.")
    
    # Мок инструментов
    tools = MagicMock()
    tools.execute_tool_chain = AsyncMock(return_value="Search result: found nothing")
    router.tools = tools
    
    # Вызов
    res = await router.route_query("Найди что-нибудь")
    
    assert res == "The results are in."
    # Проверяем, что инструмент был спрошен
    tools.execute_tool_chain.assert_called_once()
    # Проверяем, что AI получил контекст поиска
    args, _ = router._call_gemini.call_args
    assert "found nothing" in args[0]

