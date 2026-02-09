
import os
import asyncio
import logging
import aiohttp
import sqlite3
import time
from telethon import TelegramClient, events
from telethon.tl.functions.contacts import GetBlockedRequest

# --- 🔐 Configuration ---
API_ID = 24590537
API_HASH = "f54d817ca6c3f98bcbeb7e985d6555b2"

# OpenClaw API
# OpenClaw API
OPENCLAW_API_URL = "http://localhost:18789/v1/chat/completions"
# Default Model (Overridden by DB)
DEFAULT_MODEL = "google/gemini-2.0-flash-exp"
MODEL_ID = DEFAULT_MODEL 

# Identities
ALLOWED_USERS = []              # Whitelist for DM (if enabled)
MANUAL_BLOCKLIST = ["Mikromolekyla_11_dva_0", "Mikromolekyla_11_dva_0"] # Added request

# Behavior
REQUIRE_WHITELIST_IN_DM = False
IGNORE_BLOCKED_USERS = True
ENABLE_FULL_LOGGING = True      # Save all incoming messages to DB?

# Triggers
BOT_NAMES = ["краб", "krab", "openclaw", ".claw", "бот"]

# --- 🧠 System Prompts ---
# 1. Personal Assistant (DM)
PROMPT_DM = (
    "Ты — Краб, профессиональный и эффективный ИИ-ассистент моего создателя (По). "
    "Мы находимся в личном чате. Твоя задача — максимально точно и серьезно выполнять поручения. "
    "Используй инструменты, анализируй данные, будь краток и полезен. "
    "Если тебя просят что-то сделать — делай, а не болтай."
)

# 2. Group Companion (Public)
PROMPT_GROUP = (
    "Ты — Краб, веселый и свойский ИИ-сосед в групповом чате. "
    "Ты ведешь себя как друг, можешь шутить, используешь сленг, но остаешься полезным. "
    "Не будь душным роботом. Твои ответы должны быть живыми и естественными. "
    "Если тебя оскорбляют — можешь остроумно ответить. Если хвалят — скажи спасибо."
)

# --- Logging & DB ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("Nexus")

DB_PATH = "nexus_history.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    # Table for all messages (The "Black Box")
    c.execute('''CREATE TABLE IF NOT EXISTS messages
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  date TIMESTAMP,
                  chat_id INTEGER,
                  chat_title TEXT,
                  sender_id INTEGER,
                  sender_name TEXT,
                  username TEXT,
                  message_text TEXT,
                  reply_to_msg_id INTEGER)''')
    conn.commit()
    conn.close()

    # KV Store for settings
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS settings
                 (key TEXT PRIMARY KEY,
                  value TEXT)''')
    conn.commit()
    conn.close()

def get_setting(key, default=None):
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT value FROM settings WHERE key=?", (key,))
        row = c.fetchone()
        conn.close()
        return row[0] if row else default
    except:
        return default

def set_setting(key, value):
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"DB Error: {e}")

def log_message_to_db(event, sender, chat):
    if not ENABLE_FULL_LOGGING:
        return
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        
        chat_title = getattr(chat, 'title', 'Private') if chat else 'Unknown'
        sender_name = getattr(sender, 'first_name', '') or getattr(sender, 'title', 'Unknown')
        username = getattr(sender, 'username', '')
        
        c.execute("INSERT INTO messages (date, chat_id, chat_title, sender_id, sender_name, username, message_text, reply_to_msg_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                  (event.date, event.chat_id, chat_title, sender.id if sender else 0, sender_name, username, event.text, event.reply_to_msg_id))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"DB Log Error: {e}")

# --- Logic ---

client = TelegramClient('nexus_session', API_ID, API_HASH)
blocked_cache = set()

async def update_blocked_users():
    if not IGNORE_BLOCKED_USERS: return
    try:
        result = await client(GetBlockedRequest(offset=0, limit=100))
        for contact in result.blocked:
            blocked_cache.add(contact.peer_id.user_id)
        logger.info(f"🚫 Synced {len(blocked_cache)} blocked users from Telegram.")
    except Exception as e:
        logger.error(f"Failed to sync blocked list: {e}")

async def ask_openclaw(text, system_prompt):
    global MODEL_ID
    payload = {
        "model": MODEL_ID,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text}
        ],
        "stream": False
    }
    try:
        headers = {
            "Authorization": "Bearer sk-nexus-bridge",
            "Content-Type": "application/json"
        }
        # Increase timeout for large local models
        timeout_seconds = 180 if "lmstudio" in MODEL_ID else 60
        
        async with aiohttp.ClientSession() as session:
            async with session.post(OPENCLAW_API_URL, json=payload, headers=headers, timeout=timeout_seconds) as response:
                if response.status == 200:
                    data = await response.json()
                    return data['choices'][0]['message']['content']
                else:
                    error_text = await response.text()
                    logger.error(f"❌ OpenClaw API Error {response.status}: {error_text}")
                    
                    # Auto-fallback if local model fails
                    if "lmstudio" in MODEL_ID:
                        logger.warning(f"⚠️ Local model failed. Falling back to Gemini...")
                        # Recursive call with Gemini
                        fallback_payload = payload.copy()
                        fallback_payload["model"] = "google/gemini-2.0-flash-exp"
                        
                        async with session.post(OPENCLAW_API_URL, json=fallback_payload, headers=headers, timeout=60) as fb_response:
                             if fb_response.status == 200:
                                 data = await fb_response.json()
                                 return f"⚠️ [Fallback] {data['choices'][0]['message']['content']}"
                             else:
                                 return f"❌ Error: Both Local & Cloud Brains failed."
                    
                    return f"❌ Brain Error: {response.status}"
    except Exception as e:
        logger.error(f"Brain connection error: {e}")
        return f"❌ Connection Error: {str(e)}"

@client.on(events.NewMessage)
async def handler(event):
    sender = await event.get_sender()
    chat = await event.get_chat()
    
    # 0. Log EVERYTHING (The Black Box)
    log_message_to_db(event, sender, chat)

    if not sender: return
    
    # 1. Blocklist Check
    username = getattr(sender, 'username', '') or ''
    if username in MANUAL_BLOCKLIST or sender.id in blocked_cache:
        return 

    text = event.text.strip()
    if not text: return

    # --- COMMANDS ---
    if text.startswith("!model"):
        parts = text.split()
        if len(parts) > 1:
            alias = parts[1].lower()
            if alias in ["gemini", "google", "flash"]:
                global MODEL_ID
                MODEL_ID = "google/gemini-2.0-flash-exp"
                set_setting("current_model", MODEL_ID)
                await event.reply(f"🧠 Switched to Brain: **Gemini 2.0 Flash**")
            elif alias in ["pro", "gemini-pro", "gemini3", "deep"]:
                MODEL_ID = "google/gemini-1.5-pro-latest"
                set_setting("current_model", MODEL_ID)
                await event.reply(f"🧠 Switched to Brain: **Gemini 1.5 Pro (Reasoning)**")
            elif alias in ["local", "lmstudio", "offline"]:
                MODEL_ID = "local" # Use the generic local ID
                set_setting("current_model", MODEL_ID)
                await event.reply(f"🧠 Switched to Brain: **Local (LM Studio)**")
            else:
                 await event.reply(f"❌ Unknown model alias: `{alias}`. Try `gemini` or `local`.")
        else:
             await event.reply(f"ℹ️ Current Brain: `{MODEL_ID}`\nUse `!model <name>` to switch.")
        return

    should_answer = False
    system_instructions = ""
    
    is_private = event.is_private

    # 2. Decision Logic
    if is_private:
        # DM = Serious
        if REQUIRE_WHITELIST_IN_DM and sender.id not in ALLOWED_USERS:
            return
        should_answer = True
        system_instructions = PROMPT_DM
    else:
        # Group = Fun/Friend
        # Trigger conditions
        is_trigger_word = any(text.lower().startswith(name.lower()) for name in BOT_NAMES)
        
        is_reply_to_me = False
        if event.is_reply:
            reply = await event.get_reply_message()
            me = await client.get_me()
            if reply and reply.sender_id == me.id:
                is_reply_to_me = True
        
        if is_trigger_word or is_reply_to_me:
            should_answer = True
            system_instructions = PROMPT_GROUP
            # Clean trigger prefix
            for name in BOT_NAMES:
                if text.lower().startswith(name.lower()):
                    text = text[len(name):].strip()
                    break

    if not should_answer:
        return

    # 3. Action
    logger.info(f"⚡️ Replying to {getattr(sender, 'first_name', 'User')}: {text[:30]}...")
    async with event.client.action(event.chat_id, 'typing'):
        response = await ask_openclaw(text, system_instructions)

    if response:
        await event.reply(response)

async def main():
    print(f"\n🦞 NEXUS V2 (DATABASE EDITION) STARTED")
    init_db()
    
    # Load saved model preference
    saved_model = get_setting("current_model")
    if saved_model:
        # Auto-correct legacy IDs (missing prefix)
        if saved_model.startswith("gemini-") and not saved_model.startswith("google/"):
            saved_model = "google/" + saved_model
            set_setting("current_model", saved_model)
            print(f"🔧 Auto-corrected legacy model ID to: {saved_model}")
            
        global MODEL_ID
        MODEL_ID = saved_model
        print(f"🧠 Loaded Memory: Using Model {MODEL_ID}")
    else:
        print(f"🧠 Using Default Model: {MODEL_ID}")

    print(f"📂 Logging all messages to: {os.path.abspath(DB_PATH)}")
    
    await client.start()
    await update_blocked_users()
    
    me = await client.get_me()
    print(f"✅ Userbot: @{me.username} (ID: {me.id})")
    print("--------------------------------------------------")
    
    await client.run_until_disconnected()

if __name__ == '__main__':
    client.loop.run_until_complete(main())
