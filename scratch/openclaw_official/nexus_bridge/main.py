
import asyncio
import logging
import os
import aiohttp
from pyrogram import Client, filters
from pyrogram.types import Message
from dotenv import load_dotenv
from loguru import logger

# --- Configuration ---
load_dotenv()

# User Credentials (from previous context)
API_ID = os.getenv("API_ID", "24590537")
API_HASH = os.getenv("API_HASH", "f54d817ca6c3f98bcbeb7e985d6555b2")
BOT_NAME = "Krab"

# OpenClaw Gateway
GATEWAY_URL = os.getenv("OPENCLAW_URL", "http://localhost:18789/v1/chat/completions")
DEFAULT_MODEL = "google/gemini-2.0-flash-exp" # Default brain

# Permissions
ALLOWED_CHATS = [] # Whitelist IDs
MY_USERNAME = "p0lrd" # Admin

# Setup Logging
logger.add("nexus_bridge.log", rotation="1 MB")

# Initialize Client
app = Client("nexus_session", api_id=API_ID, api_hash=API_HASH)

async def ask_openclaw_brain(text: str, context: str = "") -> str:
    """
    Sends the text to OpenClaw Node.js Gateway.
    """
    payload = {
        "model": DEFAULT_MODEL, 
        "messages": [
            {"role": "system", "content": "You are 'Krab', a helpful AI assistant connected to a Telegram Userbot. Be concise, helpful, and human-like."},
            {"role": "user", "content": f"{context}\n\nUser says: {text}"}
        ],
        "stream": False
    }
    
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {os.getenv('OPENCLAW_GATEWAY_TOKEN', 'sk-nexus-bridge')}"
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(GATEWAY_URL, json=payload, headers=headers, timeout=60) as response:
                if response.status == 200:
                    data = await response.json()
                    return data['choices'][0]['message']['content']
                else:
                    err = await response.text()
                    logger.error(f"OpenClaw Gateway Error: {response.status} - {err}")
                    return f"⚠️ Brain Error: {response.status}"
    except Exception as e:
        logger.error(f"Connection Failed: {e}")
        return f"⚠️ Connection Error: Is OpenClaw running? ({e})"

@app.on_message(filters.me & filters.command("claw", prefixes="."))
async def handle_self_command(client, message: Message):
    """
    Explicit command from ME: .claw <query>
    """
    query = message.text.split(" ", 1)[1] if len(message.text.split()) > 1 else ""
    if not query:
        await message.edit("❌ Usage: `.claw <question>`")
        return

    await message.edit(f"🧠 Thinking: *{query}*...")
    response = await ask_openclaw_brain(query)
    await message.edit(f"**Q:** {query}\n\n**A:** {response}")

@app.on_message(filters.mentioned)
async def handle_mentions(client, message: Message):
    """
    Responds when tagged in group or private.
    """
    # Simply check if mentioned
    logger.info(f"Mentioned in {message.chat.title or 'Private'}")
    
    # Typing indicator
    await client.send_chat_action(message.chat.id, "typing")
    
    # Process
    text = message.text
    response = await ask_openclaw_brain(text)
    
    # Reply
    await message.reply(response)

@app.on_message(filters.private & ~filters.me)
async def handle_dm(client, message: Message):
    """
    Handle DMs (White-list logic).
    """
    # For now, just log DMs, don't auto-reply unless whitelisted or if it's the Admin
    # Since we are testing, let's reply to defined whitelist or Admin
    
    sender = message.from_user
    if sender and sender.username == MY_USERNAME:
        # It's me from another account? Or standard logic.
        # Actually Pyrogram filters.me handles "my own messages".
        pass 

    # For testing: Auto-reply to everything in DM if it looks like a question?
    # Let's keep it safe: Only reply if specifically triggered or if it's a known user.
    # Implementing "Ghost Mode": Read but don't reply by default.
    return 

async def main():
    logger.info("🦀 Nexus-OpenClaw Bridge Starting...")
    async with app:
        me = await app.get_me()
        logger.info(f"Connected as: {me.first_name} (@{me.username})")
        logger.info("Listening for commands...")
        await asyncio.Event().wait() # Keep running

if __name__ == "__main__":
    app.run(main())
