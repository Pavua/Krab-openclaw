
import asyncio
import os
from dotenv import load_dotenv
from src.core.model_manager import ModelRouter

async def test_scans():
    load_dotenv(override=True)
    router = ModelRouter(config=os.environ)
    print("--- Scanning Local ---")
    local_models = await router.list_local_models()
    print(f"Local models: {local_models}")
    
    print("\n--- Scanning Cloud ---")
    cloud_models = await router.list_cloud_models()
    print(f"Cloud models: {cloud_models}")
    
    print(f"\nLast Cloud Error: {router.last_cloud_error}")

if __name__ == "__main__":
    asyncio.run(test_scans())
