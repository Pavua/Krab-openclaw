
import logging
from pyrogram import Client, filters
from dotenv import load_dotenv
import os
import asyncio

logging.basicConfig(level=logging.INFO)

load_dotenv(override=True)

api_id = os.getenv("TELEGRAM_API_ID")
api_hash = os.getenv("TELEGRAM_API_HASH")
session_name = os.getenv("TELEGRAM_SESSION_NAME", "nexus_session1")

print(f"DEBUG: Session={session_name}")

app = Client(session_name, api_id=api_id, api_hash=api_hash, workdir=".")

@app.on_message(filters.all)
async def hello(client, message):
    print(f"MSG: {message.text}")
    try:
        if message.command and message.command[0] == "ping":
            await message.reply_text("PONG!")
            print("Replied PONG")
    except:
        pass

async def main():
    print("Starting...")
    await app.start()
    print("Idle...")
    await asyncio.Event().wait()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
