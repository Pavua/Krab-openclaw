# -*- coding: utf-8 -*-
"""
Black Box Recorder (SQLite Persistence)
Сохраняет все входящие и исходящие сообщения для будущего обучения и аудита.
По мотивам Nexus V2 Database Edition.
"""

import sqlite3
import os
import logging
from datetime import datetime

logger = logging.getLogger("BlackBox")

class BlackBox:
    def __init__(self, db_path="artifacts/memory/black_box.db"):
        self.db_path = db_path
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_db()

    def _init_db(self):
        """Инициализация таблиц базы данных."""
        try:
            conn = sqlite3.connect(self.db_path)
            c = conn.cursor()
            # Таблица сообщений (Черный ящик)
            c.execute('''CREATE TABLE IF NOT EXISTS messages
                         (id INTEGER PRIMARY KEY AUTOINCREMENT,
                          timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                          chat_id INTEGER,
                          chat_title TEXT,
                          sender_id INTEGER,
                          sender_name TEXT,
                          username TEXT,
                          direction TEXT, -- INCOMING / OUTGOING
                          text TEXT,
                          reply_to_id INTEGER,
                          model_used TEXT)''')
            
            # Таблица системных событий
            c.execute('''CREATE TABLE IF NOT EXISTS events
                         (id INTEGER PRIMARY KEY AUTOINCREMENT,
                          timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                          event_type TEXT,
                          description TEXT)''')
            
            conn.commit()
            conn.close()
            logger.info(f"📁 Black Box DB ready at {self.db_path}")
        except Exception as e:
            logger.error(f"❌ Failed to init Black Box DB: {e}")

    def log_message(self, chat_id, chat_title, sender_id, sender_name, username, direction, text, reply_to_id=None, model_used=None):
        """Запись сообщения в БД."""
        try:
            conn = sqlite3.connect(self.db_path)
            c = conn.cursor()
            c.execute("""INSERT INTO messages 
                         (chat_id, chat_title, sender_id, sender_name, username, direction, text, reply_to_id, model_used)
                         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                      (chat_id, chat_title, sender_id, sender_name, username, direction, text, reply_to_id, model_used))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"❌ Failed to log message: {e}")

    def log_event(self, event_type, description):
        """Запись системного события."""
        try:
            conn = sqlite3.connect(self.db_path)
            c = conn.cursor()
            c.execute("INSERT INTO events (event_type, description) VALUES (?, ?)", (event_type, description))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"❌ Failed to log event: {e}")

    def get_stats(self):
        """Получение статистики БД."""
        try:
            conn = sqlite3.connect(self.db_path)
            c = conn.cursor()
            c.execute("SELECT COUNT(*) FROM messages")
            count = c.fetchone()[0]
            c.execute("SELECT COUNT(*) FROM messages WHERE direction='INCOMING'")
            incoming = c.fetchone()[0]
            c.execute("SELECT COUNT(*) FROM messages WHERE direction='OUTGOING'")
            outgoing = c.fetchone()[0]
            conn.close()
            return {
                "total": count,
                "incoming": incoming,
                "outgoing": outgoing,
                "path": self.db_path
            }
        except:
            return {"total": 0, "path": self.db_path}
