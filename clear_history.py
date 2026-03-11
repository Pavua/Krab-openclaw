import asyncio
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from src.cache_manager import history_cache

def main():
    history_cache.delete("chat_history:312322764")
    print("Deleted chat_history:312322764")

if __name__ == "__main__":
    main()
