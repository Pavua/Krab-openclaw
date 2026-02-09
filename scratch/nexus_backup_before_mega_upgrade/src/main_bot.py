import asyncio
import logging
from telethon import TelegramClient, events
from telethon.tl.functions.contacts import GetBlockedRequest

from config.settings import Config
from src.db import DatabaseManager
from src.ai import AIManager

# Setup Logging
logging.basicConfig(level=getattr(logging, Config.LOG_LEVEL), format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("Nexus.Bot")

class NexusBot:
    def __init__(self):
        self.db = DatabaseManager()
        self.ai = AIManager(self.db)
        self.client = TelegramClient(Config.SESSION_NAME, Config.API_ID, Config.API_HASH)
        self.blocked_cache = set()
        
        # System Prompts
        self.PROMPT_DM = (
            "Ты — Краб, профессиональный и эффективный ИИ-ассистент моего создателя (По). "
            "Мы находимся в личном чате. Твоя задача — максимально точно и серьезно выполнять поручения. "
            "Используй инструменты, анализируй данные, будь краток и полезен. "
            "Если тебя просят что-то сделать — делай, а не болтай."
        )
        self.PROMPT_GROUP = (
            "Ты — Краб, веселый и свойский ИИ-сосед в групповом чате. "
            "Ты ведешь себя как друг, можешь шутить, используешь сленг, но остаешься полезным. "
            "Не будь душным роботом. Твои ответы должны быть живыми и естественными. "
            "Если тебя оскорбляют — можешь остроумно ответить. Если хвалят — скажи спасибо."
        )
        self.BOT_NAMES = ["краб", "krab", "openclaw", ".claw", "бот"]

    async def start(self):
        logger.info("🦞 NEXUS V2 (REFACTORED) STARTING...")
        await self.client.start()
        
        me = await self.client.get_me()
        logger.info(f"✅ Userbot Active: @{me.username} (ID: {me.id})")
        logger.info(f"🧠 Current Brain: {self.ai.get_model()}")
        
        await self.sync_blocked_users()
        
        # Register handlers
        self.client.add_event_handler(self.handle_message, events.NewMessage)
        
        await self.client.run_until_disconnected()

    async def sync_blocked_users(self):
        if not Config.IGNORE_BLOCKED_USERS: 
            return
        try:
            result = await self.client(GetBlockedRequest(offset=0, limit=100))
            for contact in result.blocked:
                self.blocked_cache.add(contact.peer_id.user_id)
            logger.info(f"🚫 Synced {len(self.blocked_cache)} blocked users.")
        except Exception as e:
            logger.error(f"Failed to sync blocked list: {e}")

    async def handle_message(self, event):
        sender = await event.get_sender()
        chat = await event.get_chat()
        
        # 0. Log (Black Box)
        self.db.log_message(event, sender, chat)

        if not sender: return
        
        # 1. Blocklist/Security Check
        username = getattr(sender, 'username', '') or ''
        if username in Config.MANUAL_BLOCKLIST or sender.id in self.blocked_cache:
            return

        text = event.text.strip()
        if not text: return

        # --- COMMANDS ---
        if text.startswith("!model"):
            await self.handle_model_command(event, text)
            return

        # --- DECISION LOGIC ---
        should_answer, system_prompt = await self.decide_to_answer(event, text, sender, chat)
        
        if should_answer:
            # Clean trigger if needed
            cleaned_text = text
            for name in self.BOT_NAMES:
                if text.lower().startswith(name.lower()):
                    cleaned_text = text[len(name):].strip()
                    break
            
            async with event.client.action(event.chat_id, 'typing'):
                logger.info(f"⚡️ Replying to {getattr(sender, 'first_name', 'User')}")
                
                if event.photo:
                    logger.info("  📸 Processing Photo...")
                    path = await event.download_media()
                    response = await self.ai.ask_with_media(cleaned_text, path, system_prompt)
                    # Cleanup
                    import os
                    if path and os.path.exists(path):
                        os.remove(path)
                else:
                    response = await self.ai.ask(cleaned_text, system_prompt)
            
            if response:
                await event.reply(response)

    async def handle_model_command(self, event, text):
        parts = text.split()
        if len(parts) > 1:
            alias = parts[1].lower()
            new_model = None
            readable_name = ""
            
            if alias in ["gemini", "google", "flash"]:
                new_model = "google/gemini-2.0-flash-exp"
                readable_name = "Gemini 2.0 Flash"
            elif alias in ["pro", "gemini-pro"]:
                new_model = "google/gemini-1.5-pro-latest"
                readable_name = "Gemini 1.5 Pro"
            elif alias in ["local", "lmstudio"]:
                new_model = "local" # We can refine this to pull specific local model names
                readable_name = "Local (LM Studio)"
            else:
                await event.reply(f"❌ Unknown alias: `{alias}`. Try `gemini`, `pro`, or `local`.")
                return

            self.ai.set_model(new_model)
            await event.reply(f"🧠 Switched to Brain: **{readable_name}**")
        else:
            await event.reply(f"ℹ️ Current Brain: `{self.ai.get_model()}`")

    async def decide_to_answer(self, event, text, sender, chat):
        is_private = event.is_private
        
        if is_private:
            # DM Logic
            if Config.REQUIRE_WHITELIST_IN_DM and (getattr(sender, 'username', '') not in Config.ALLOWED_USERS):
                return False, None
            return True, self.PROMPT_DM
        else:
            # Group Logic
            is_trigger_word = any(text.lower().startswith(name.lower()) for name in self.BOT_NAMES)
            
            is_reply_to_me = False
            if event.is_reply:
                reply = await event.get_reply_message()
                me = await self.client.get_me()
                if reply and reply.sender_id == me.id:
                    is_reply_to_me = True
            
            if is_trigger_word or is_reply_to_me:
                return True, self.PROMPT_GROUP
            
        return False, None

if __name__ == "__main__":
    bot = NexusBot()
    bot.client.loop.run_until_complete(bot.start())
