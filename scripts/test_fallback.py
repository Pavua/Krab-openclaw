import asyncio
import os
import json
from src.core.model_manager import ModelRouter

async def test_fallback():
    config = {
        "LM_STUDIO_URL": "http://localhost:1234/v1",
        "GEMINI_API_KEY": os.getenv("GEMINI_API_KEY"),
        "GEMINI_CHAT_MODEL": "gemini-2.0-flash"
    }
    router = ModelRouter(config)
    
    # Mocking local engine unavailability or failure
    router.is_local_available = False
    
    print("Testing routing with local unavailable...")
    response = await router.route_query("Привет, как дела?", task_type="chat")
    print(f"Response: {response[:50]}...")
    
    if "Error" not in response and len(response) > 5:
        print("✅ Fallback to Cloud works!")
    else:
        print("❌ Fallback failed.")

if __name__ == "__main__":
    asyncio.run(test_fallback())
