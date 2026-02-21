# -*- coding: utf-8 -*-
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock
from src.core.agent_manager import AgentWorkflow

@pytest.mark.asyncio
async def test_agent_workflow_basic():
    # Мокаем зависимости
    router = MagicMock()
    router.route_query = AsyncMock(side_effect=["Step 1: Plan", "Step 2: Conclusion"])
    memory = MagicMock()
    security = MagicMock()
    
    agent = AgentWorkflow(router, memory, security)
    
    result = await agent.solve_complex_task("Test prompt", 123)
    
    assert "Step 1: Plan" in result
    assert "Step 2: Conclusion" in result
    assert router.route_query.call_count == 2

@pytest.mark.asyncio
async def test_agent_workflow_prompts():
    router = MagicMock()
    router.route_query = AsyncMock(return_value="Output")
    memory = MagicMock()
    security = MagicMock()
    
    agent = AgentWorkflow(router, memory, security)
    await agent.solve_complex_task("Run test", 456)
    
    # Проверяем, что в первый раз спросили план
    args, kwargs = router.route_query.call_args_list[0]
    assert "план" in args[0].lower()
    assert kwargs['task_type'] == 'reasoning'

