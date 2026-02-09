import os
import logging
from dotenv import load_dotenv

# Load .env from project root (one level up from config package, or handled by main entrypoint)
load_dotenv()

class Config:
    # Telegram
    API_ID = int(os.getenv("TELEGRAM_API_ID", 0))
    API_HASH = os.getenv("TELEGRAM_API_HASH", "")
    SESSION_NAME = os.getenv("TELEGRAM_SESSION_NAME", "nexus_session")

    # AI
    OPENCLAW_API_URL = os.getenv("OPENCLAW_API_URL", "http://localhost:18789/v1/chat/completions")
    OPENCLAW_API_KEY = os.getenv("OPENCLAW_API_KEY", "sk-nexus-bridge")
    DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", "google/gemini-2.0-flash-exp")

    # Security
    ALLOWED_USERS = [u.strip() for u in os.getenv("ALLOWED_USERS", "").split(",") if u.strip()]
    MANUAL_BLOCKLIST = [u.strip() for u in os.getenv("MANUAL_BLOCKLIST", "").split(",") if u.strip()]
    
    # Behavior
    REQUIRE_WHITELIST_IN_DM = os.getenv("REQUIRE_WHITELIST_IN_DM", "False").lower() == "true"
    IGNORE_BLOCKED_USERS = os.getenv("IGNORE_BLOCKED_USERS", "True").lower() == "true"
    ENABLE_FULL_LOGGING = os.getenv("ENABLE_FULL_LOGGING", "True").lower() == "true"
    
    # Logging
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
    
    # Paths
    DB_PATH = os.path.join(os.getcwd(), "nexus_history.db")

    @classmethod
    def validate(cls):
        missing = []
        if not cls.API_ID: missing.append("TELEGRAM_API_ID")
        if not cls.API_HASH: missing.append("TELEGRAM_API_HASH")
        
        if missing:
            logging.error(f"❌ Missing critical configuration: {', '.join(missing)}")
            return False
        return True
