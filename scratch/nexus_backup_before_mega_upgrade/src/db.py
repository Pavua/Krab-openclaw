import sqlite3
import logging
from datetime import datetime
from config.settings import Config

logger = logging.getLogger("Nexus.DB")

class DatabaseManager:
    def __init__(self):
        self.db_path = Config.DB_PATH
        self.init_db()

    def init_db(self):
        try:
            with sqlite3.connect(self.db_path) as conn:
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
                
                # KV Store for settings
                c.execute('''CREATE TABLE IF NOT EXISTS settings
                            (key TEXT PRIMARY KEY,
                            value TEXT)''')
                conn.commit()
        except Exception as e:
            logger.error(f"Failed to init DB: {e}")

    def get_setting(self, key, default=None):
        try:
            with sqlite3.connect(self.db_path) as conn:
                c = conn.cursor()
                c.execute("SELECT value FROM settings WHERE key=?", (key,))
                row = c.fetchone()
                return row[0] if row else default
        except Exception as e:
            logger.error(f"DB Read Error: {e}")
            return default

    def set_setting(self, key, value):
        try:
            with sqlite3.connect(self.db_path) as conn:
                c = conn.cursor()
                c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
                conn.commit()
        except Exception as e:
            logger.error(f"DB Write Error: {e}")

    def log_message(self, event, sender, chat):
        if not Config.ENABLE_FULL_LOGGING:
            return
        try:
            chat_title = getattr(chat, 'title', 'Private') if chat else 'Unknown'
            sender_name = getattr(sender, 'first_name', '') or getattr(sender, 'title', 'Unknown')
            username = getattr(sender, 'username', '')
            
            with sqlite3.connect(self.db_path) as conn:
                c = conn.cursor()
                c.execute("INSERT INTO messages (date, chat_id, chat_title, sender_id, sender_name, username, message_text, reply_to_msg_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                          (event.date, event.chat_id, chat_title, sender.id if sender else 0, sender_name, username, event.text, event.reply_to_msg_id))
                conn.commit()
        except Exception as e:
            logger.error(f"DB Log Error: {e}")
