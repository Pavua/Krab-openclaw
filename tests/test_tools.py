# -*- coding: utf-8 -*-
import pytest
from unittest.mock import AsyncMock, MagicMock
from src.core.tool_handler import ToolHandler

@pytest.mark.asyncio
async def test_tool_handler_selection():
    scout = MagicMock()
    scout.search = AsyncMock(return_value=[{"title": "Test", "body": "Context", "href": "url"}])
    scout.format_results = MagicMock(return_value="Formatted web")
    
    handler = ToolHandler(MagicMock(), MagicMock(), scout)
    
    # Реакция на поиск
    res = await handler.execute_tool_chain("Поищи новости про BTC")
    assert "Formatted web" in res
    scout.search.assert_called_once()

@pytest.mark.asyncio
async def test_tool_handler_rag():
    rag = MagicMock()
    rag.query = MagicMock(return_value="Secret info")
    
    handler = ToolHandler(MagicMock(), rag, MagicMock())
    
    # Реакция на RAG
    res = await handler.execute_tool_chain("Вспомни мой пароль")
    assert "Secret info" in res
    rag.query.assert_called_once()


@pytest.mark.asyncio
async def test_router_tool_integration():
    from src.core.model_manager import ModelRouter
    
    # Мокаем Gemini call
    router = ModelRouter({"GEMINI_API_KEY": "fake"})
    router._call_gemini = AsyncMock(return_value="Final Answer")
    router.check_local_health = AsyncMock() # Не проверять локалку
    
    # Настраиваем инструменты
    tools = MagicMock()
    tools.execute_tool_chain = AsyncMock(return_value="Web Data: BTC is 100k")
    router.tools = tools
    
    await router.route_query("Сколько стоит BTC?", use_rag=False)
    
    # Проверяем, что в финальный промпт попали данные из тулзы
    args, _ = router._call_gemini.call_args
    assert "BTC is 100k" in args[0]
