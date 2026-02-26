from dotenv import load_dotenv
import os
import asyncio
from pyrogram import Client

load_dotenv(override=True)

api_id = os.getenv("TELEGRAM_API_ID")
api_hash = os.getenv("TELEGRAM_API_HASH")
session_name = os.getenv("TELEGRAM_SESSION_NAME", "nexus_session1")

print(f"Testing session: {session_name}")
print(f"API ID: {api_id}")

app = Client(session_name, api_id=api_id, api_hash=api_hash)

async def main():
    try:
        print("Starting client...")
        await app.start()
        me = await app.get_me()
        print(f"✅ Success! Logged in as: {me.first_name} (@{me.username})")
        await app.stop()
    except Exception as e:
        print(f"❌ Failed: {e}")

if __name__ == "__main__":
    asyncio.run(main())
