
import asyncio
import os
import sys
from pathlib import Path

# Add src to path
sys.path.append(str(Path.cwd()))

from src.core.openclaw_client import OpenClawClient
from src.core.model_manager import ModelRouter
from dotenv import load_dotenv

async def main():
    load_dotenv()
    
    config = {
        "OPENCLAW_BASE_URL": os.getenv("OPENCLAW_BASE_URL", "http://localhost:18789"),
        "OPENCLAW_API_KEY": os.getenv("OPENCLAW_API_KEY", "sk-nexus-bridge"),
        "LM_STUDIO_URL": os.getenv("LM_STUDIO_URL", "http://localhost:1234/v1"),
        "GEMINI_API_KEY": os.getenv("GEMINI_API_KEY")
    }
    
    print(f"--- Diagnostics ---")
    print(f"OpenClaw URL: {config['OPENCLAW_BASE_URL']}")
    
    client = OpenClawClient(config['OPENCLAW_BASE_URL'], config['OPENCLAW_API_KEY'])
    
    try:
        models = await client.get_models()
        print(f"\nAvailable OpenClaw Models:")
        for m in models:
            if isinstance(m, dict):
                print(f" - {m.get('id')} ({m.get('owned_by')})")
            else:
                print(f" - {m}")
    except Exception as e:
        print(f"Error listing OpenClaw models: {e}")

    print(f"\nChecking LM Studio at {config['LM_STUDIO_URL']}...")
    router = ModelRouter(config)
    
    try:
        is_local = await router.check_local_health(force=True)
        print(f"LM Studio Available: {is_local}")
        if is_local:
            print(f"Engine: {router.local_engine}")
            print(f"Active Model: {router.active_local_model}")
            
            lms_models = await router.list_local_models()
            print(f"Downloaded Local Models:")
            for m in lms_models:
                print(f" - {m}")
    except Exception as e:
        print(f"Error checking LM Studio: {e}")

if __name__ == "__main__":
    asyncio.run(main())
