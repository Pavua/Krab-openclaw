# -*- coding: utf-8 -*-
import pytest
import asyncio
from src.core.swarm import SwarmOrchestrator, SwarmTask

class MockTools:
    def __init__(self):
        self.scout = type('obj', (object,), {'search': self.mock_search, 'format_results': lambda x: str(x)})
        self.rag = type('obj', (object,), {'query': self.mock_rag})
        self.mcp = None

    async def mock_search(self, q):
        await asyncio.sleep(0.1)
        return f"Search result for {q}"

    def mock_rag(self, q):
        return f"RAG data for {q}"

@pytest.mark.asyncio
async def test_swarm_parallel_execution():
    tools = MockTools()
    swarm = SwarmOrchestrator(tools)
    
    tasks = [
        SwarmTask("Task1", tools.mock_search, "query1"),
        SwarmTask("Task2", tools.mock_rag, "query2")
    ]
    
    start_time = asyncio.get_event_loop().time()
    results = await swarm.execute_parallel(tasks)
    end_time = asyncio.get_event_loop().time()
    
    assert results["Task1"] == "Search result for query1"
    assert results["Task2"] == "RAG data for query2"
    # Должно быть быстрее, чем последовательно (0.1s + ~0s)
    assert end_time - start_time < 0.2

@pytest.mark.asyncio
async def test_swarm_autonomous_decision():
    tools = MockTools()
    swarm = SwarmOrchestrator(tools)
    
    # Запрос требующий и поиска и памяти
    query = "Поищи в гугле и вспомни что я говорил"
    result = await swarm.autonomous_decision(query)
    
    assert "Search result" in result
    assert "RAG data" in result
    assert "[SWARM]" in result
