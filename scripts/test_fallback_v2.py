import asyncio
import os
import sys
import json
import logging

# Set up logging to avoid noise
logging.basicConfig(level=logging.ERROR)

# Import dependencies
try:
    from src.core.model_manager import ModelRouter
except ImportError:
    print("Error: Could not import ModelRouter. Ensure PYTHONPATH is set.")
    sys.exit(1)

async def main():
    config = {
        "LM_STUDIO_URL": "http://localhost:1234/v1",
        "GEMINI_API_KEY": os.getenv("GEMINI_API_KEY"),
        "GEMINI_CHAT_MODEL": "gemini-2.0-flash",
        "OPENCLAW_BASE_URL": "http://localhost:18789"
    }
    
    router = ModelRouter(config)
    
    # Force local available to false to trigger fallback
    router.is_local_available = False
    
    print("Testing routing with local unavailable (Triggering Fallback)...")
    try:
        response = await router.route_query("Привет! Кто ты?", task_type="chat")
        if response and "Error" not in response:
            print(f"✅ Fallback to Cloud works! Response: {response[:50]}...")
        else:
            print(f"❌ Fallback failed or returned error: {response}")
    except Exception as e:
        print(f"❌ Exception during routing: {e}")

if __name__ == "__main__":
    asyncio.run(main())
