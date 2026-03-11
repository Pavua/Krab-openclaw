import asyncio
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from src.memory_engine import MemoryManager
from src.config import config

async def main():
    mm = MemoryManager(config.memory_db_path)
    await mm.init()
    mm.delete_conversation(312322764)
    print("Cleared memory for 312322764")

if __name__ == "__main__":
    asyncio.run(main())
