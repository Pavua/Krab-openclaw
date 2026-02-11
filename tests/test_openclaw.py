import asyncio
import os
import sys
from dotenv import load_dotenv

# Добавляем корень проекта в sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.core.model_manager import ModelRouter
from src.core.config_manager import ConfigManager

async def test_openclaw():
    print("🚀 Starting OpenClaw Integration Test...")
    
    # 1. Load Config (Env vars)
    load_dotenv()
    
    # ModelRouter expects a dict-like object with env vars (API keys, URLs)
    config = os.environ.copy()
    
    print("\n[Configuration Check]")
    openclaw_url = config.get("OPENCLAW_URL")
    openclaw_token = config.get("OPENCLAW_TOKEN")
    
    if openclaw_url:
        print(f"✅ OPENCLAW_URL found: {openclaw_url}")
    else:
        print("❌ OPENCLAW_URL NOT FOUND in .env!")
        
    if openclaw_token:
        print(f"✅ OPENCLAW_TOKEN found (len={len(openclaw_token)})")
    else:
        print("❌ OPENCLAW_TOKEN NOT FOUND in .env!")

    # 2. Init Router
    print("\n[Initializing ModelRouter]")
    router = ModelRouter(config)
    
    # 3. Health Check
    print("\n[Checking Health]")
    is_alive = await router.check_openclaw_health(force=True)
    print(f"OpenClaw Status: {'✅ ONLINE' if is_alive else '❌ OFFLINE'}")
    
    if not is_alive:
        print("⚠️ Skipping call test because OpenClaw is offline.")
        sys.exit(1)

    # 4. Test Call
    print("\n[Testing Call]")
    response = await router.route_query(
        prompt="Hello OpenClaw! Who are you?",
        task_type="chat",
        use_rag=False
    )
    
    print(f"\n🤖 Response:\n{response}")

if __name__ == "__main__":
    asyncio.run(test_openclaw())
