
import asyncio
import os
import sys
from dotenv import load_dotenv

# Добавляем корень проекта
sys.path.append(os.path.abspath("."))

from src.core.openclaw_client import OpenClawClient

async def check_openclaw():
    load_dotenv()
    base_url = os.getenv("OPENCLAW_URL", "http://localhost:18792")
    api_key = os.getenv("OPENCLAW_TOKEN")
    
    print(f"📡 Testing OpenClaw at: {base_url}")
    client = OpenClawClient(base_url=base_url, api_key=api_key)
    
    # 1. Health check
    health = await client.health_check()
    print(f"Health: {'✅ OK' if health else '❌ Failed'}")
    
    # 2. Models
    paths = ["/v1/models", "/api/v1/models", "/api/models", "/models"]
    found_models = []
    for path in paths:
        result = await client._request_json("GET", path)
        print(f"Path {path}: status={result['status']} ok={result['ok']}")
        if result['ok'] and isinstance(result['data'], dict) and "data" in result['data']:
             print(f"✅ Found models at {path}!")
             found_models = result['data']['data']
             break
        elif result['ok'] and isinstance(result['data'], list):
             print(f"✅ Found models at {path} (list)!")
             found_models = result['data']
             break
    print(f"Found {len(found_models)} models:")
    for m in found_models[:10]:
        if isinstance(m, dict):
            print(f"  - {m.get('id')} ({m.get('owned_by')})")
        else:
            print(f"  - {m}")
            
    if not found_models:
        print("⚠️ No models returned from OpenClaw!")

if __name__ == "__main__":
    asyncio.run(check_openclaw())
