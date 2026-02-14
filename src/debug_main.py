
import logging
import os
import asyncio
from dotenv import load_dotenv
from pyrogram import Client

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load env forced
load_dotenv(override=True)

api_id = os.getenv("TELEGRAM_API_ID")
api_hash = os.getenv("TELEGRAM_API_HASH")
session_name = os.getenv("TELEGRAM_SESSION_NAME", "nexus_session1")

logger.info(f"DEBUG: Session={session_name}, API_ID={api_id}")

# Initialize Client
app = Client(session_name, api_id=api_id, api_hash=api_hash, workdir=".")

async def main():
    try:
        logger.info("Attempting to start app...")
        await app.start()
        me = await app.get_me()
        logger.info(f"✅ SUCCESS: Logged in as {me.first_name} (@{me.username})")
        await app.stop()
    except Exception as e:
        logger.error(f"❌ FAIL: {e}", exc_info=True)

if __name__ == "__main__":
    asyncio.run(main())
