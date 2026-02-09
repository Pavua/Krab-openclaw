import asyncio
import logging
from src.main_bot import NexusBot

if __name__ == "__main__":
    try:
        bot = NexusBot()
        # Telethon's run_until_disconnected handles the loop, so we just start it.
        # However, start() is an async function in our class wrapper.
        bot.client.loop.run_until_complete(bot.start())
    except KeyboardInterrupt:
        print("\n👋 Nexus Bot stopping...")
    except Exception as e:
        logging.critical(f"🔥 Critical Startup Error: {e}")
