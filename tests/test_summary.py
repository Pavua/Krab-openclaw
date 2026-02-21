# -*- coding: utf-8 -*-
import asyncio
from unittest.mock import MagicMock
from src.core.summary_manager import SummaryManager

async def test_summary():
    router = MagicMock()
    
    # We need to make it return a real coroutine result
    async def mock_route(*args, **kwargs):
        return "Краткое содержание: Диалог о крабах."
    router.route_query = mock_route
    
    memory = MagicMock()
    # 50 messages to trigger summary (threshold 40)
    memory.get_recent_context.return_value = [{"role": "user", "text": f"Msg {i}"} for i in range(50)]
    memory.get_summary.return_value = "Old summary."
    
    sm = SummaryManager(router, memory, min_messages=40)
    
    print("🚀 Running auto_summarize test...")
    result = await sm.auto_summarize(12345)
    
    if result:
        print("✅ Success: Summary triggered and processed.")
        # Check if saved
        memory.save_summary.assert_called()
        memory.clear_history.assert_called_with(12345)
    else:
        print("❌ Failed: Summary not triggered.")

if __name__ == "__main__":
    asyncio.run(test_summary())
