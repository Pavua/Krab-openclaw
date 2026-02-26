import asyncio
import aiohttp
import os

async def test_auth():
    api_key = os.getenv("OPENCLAW_API_KEY", "sk-nexus-bridge")
    url = "http://localhost:18789/v1/models"
    headers = {"Authorization": f"Bearer {api_key}"}
    
    print(f"Testing OpenClaw Auth with key: {api_key}")
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as resp:
            print(f"Status: {resp.status}")
            text = await resp.text()
            print(f"Response: {text[:200]}")

if __name__ == "__main__":
    asyncio.run(test_auth())
