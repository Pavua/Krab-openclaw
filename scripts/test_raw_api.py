
import asyncio
import os
import aiohttp
import json
from dotenv import load_dotenv

async def test_raw_scan():
    load_dotenv(override=True)
    url = "http://192.168.0.171:1234/api/v1/models"
    print(f"Requesting {url}...")
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                print(f"Status: {resp.status}")
                payload = await resp.json(content_type=None)
                print("Payload keys:", payload.keys() if isinstance(payload, dict) else "Not a dict")
                if "data" in payload:
                    print(f"Data length: {len(payload['data'])}")
                    if len(payload['data']) > 0:
                        print("First model sample:", payload['data'][0])
                else:
                    print("Raw payload sample:", str(payload)[:500])
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(test_raw_scan())
