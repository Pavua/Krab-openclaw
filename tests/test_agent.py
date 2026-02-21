# -*- coding: utf-8 -*-
import sys
import os
import asyncio
import unittest
import json
from unittest.mock import MagicMock, AsyncMock

# Add src to path
sys.path.append(os.getcwd())

class TestAgentExecutor(unittest.IsolatedAsyncioTestCase):
    async def test_react_loop(self):
        """Test the ReAct loop logic."""
        from src.core.agent_executor import AgentExecutor
        
        # Mocks
        router = MagicMock()
        tools = MagicMock()
        memory = MagicMock()
        
        # Mock Memory
        memory.get_summary.return_value = "Test Summary"
        
        # Mock Tools
        tools.get_tool_registry.return_value = "tool1: description"
        tools.execute_named_tool = AsyncMock(side_effect=[
            "Observation from tool1",
            "Final success"
        ])
        
        # Mock Router decisions
        router.route_query = AsyncMock(side_effect=[
            # Step 1: Decide to use tool
            json.dumps({
                "thought": "I need to use tool1",
                "action": "tool1",
                "action_input": {"q": "test"}
            }),
            # Step 2: Final answer
            json.dumps({
                "thought": "I have the info",
                "final_answer": "Task completed successfully"
            })
        ])
        
        executor = AgentExecutor(router, tools, memory)
        result = await executor.run("Help me with X", 123)
        
        self.assertEqual(result, "Task completed successfully")
        self.assertEqual(router.route_query.call_count, 2)
        self.assertEqual(tools.execute_named_tool.call_count, 1)
        print("✅ ReAct Agent Logic: OK")

if __name__ == "__main__":
    unittest.main()
