import asyncio
import os
import sys
from dotenv import load_dotenv

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.core.openclaw_client import OpenClawClient

async def test_openclaw_connection():
    load_dotenv()
    
    base_url = os.getenv("OPENCLAW_BASE_URL", "http://localhost:18789")
    api_key = os.getenv("OPENCLAW_API_KEY")
    
    print(f"🔌 Testing connection to {base_url}...")
    
    client = OpenClawClient(base_url=base_url, api_key=api_key)
    
    # 1. Health Check
    is_alive = await client.health_check()
    if is_alive:
        print("✅ OpenClaw Gateway is REACHABLE (Health Check OK)")
    else:
        print("❌ OpenClaw Gateway is UNREACHABLE")
        return

    # 2. Direct Tool Test
    print("\n🛠️ Testing Direct Tool Invocation (web_search)...")
    try:
        raw_results = await client.invoke_tool("web_search", {"query": "Bitcoin price", "count": 2})
        print(f"📦 Raw Tool Output: {raw_results}")
    except Exception as e:
        print(f"❌ Tool Invocation Failed: {e}")

    # 3. Agent Test
    print("\n🧠 Testing Agent Execution (Research)...")
    response = await client.execute_agent_task("Test query for connectivity check", agent_id="research_fast")
    print(f"📩 Response from Engine: {response[:100]}...")

if __name__ == "__main__":
    asyncio.run(test_openclaw_connection())
