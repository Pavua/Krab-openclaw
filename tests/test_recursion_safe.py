
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock
from src.core.swarm import SwarmOrchestrator
from src.core.model_manager import ModelRouter

@pytest.mark.asyncio
async def test_swarm_recursion_protection():
    """
    Проверяет, что SwarmOrchestrator не входит в бесконечную рекурсию
    при вызове route_query.
    """
    # Создаем моки
    mock_tools = MagicMock()
    mock_tools.execute_named_tool = AsyncMock(return_value="Tool Result")
    
    mock_router = MagicMock(spec=ModelRouter)
    
    # Инициализируем оркестратор
    orchestrator = SwarmOrchestrator(mock_tools, mock_router)
    
    # Настраиваем роутер так, чтобы он вызывал оркестратор (имитация цикла)
    # В реальности route_query вызывает execute_tool_chain, который вызывает autonomous_decision.
    async def side_effect(prompt, **kwargs):
        if kwargs.get("skip_swarm"):
            return "Final Answer via Router"
        # Имитируем повторный вызов роя (чего мы и хотим избежать)
        return await orchestrator.autonomous_decision(prompt, **kwargs)
        
    mock_router.route_query = AsyncMock(side_effect=side_effect)
    
    # Запускаем
    query = "найди новости"
    result = await orchestrator.autonomous_decision(query)
    
    assert "Final Answer" in result
    # Проверяем, что route_query был вызван с skip_swarm=True
    # Вызовы:
    # 1. Из autonomous_decision в конце (с skip_swarm=True)
    mock_router.route_query.assert_called()
    called_with_skip = any(call.kwargs.get("skip_swarm") is True for call in mock_router.route_query.call_args_list)
    assert called_with_skip, "skip_swarm was not passed to router"
    
    print("\n✅ Тест на рекурсию пройден: бесконечный цикл разорван.")

if __name__ == "__main__":
    asyncio.run(test_swarm_recursion_protection())
