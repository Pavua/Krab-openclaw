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
from typing import Dict, List, Any, Optional

logger = logging.getLogger("BlackBox")

class BlackBox:
    def __init__(self, db_path="artifacts/memory/black_box.db"):
        self.db_path = db_path
        self._start_time = datetime.now()
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
                          
            # Таблица настроек групп (Phase 12.2)
            c.execute('''CREATE TABLE IF NOT EXISTS group_settings
                         (chat_id INTEGER PRIMARY KEY,
                          is_active BOOLEAN DEFAULT 1,
                          auto_moderation BOOLEAN DEFAULT 0,
                          welcome_message TEXT,
                          allowed_commands TEXT, -- JSON list
                          updated_at DATETIME DEFAULT CURRENT_TIMESTAMP)''')
            
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

    def get_active_chats_count(self, days=7):
        """Количество уникальных чатов за последние N дней."""
        try:
            conn = sqlite3.connect(self.db_path)
            c = conn.cursor()
            c.execute("SELECT COUNT(DISTINCT chat_id) FROM messages WHERE timestamp > datetime('now', ?)", (f"-{days} days",))
            count = c.fetchone()[0]
            conn.close()
            return count
        except:
            return 0

    def get_recent_messages(self, limit=10):
        """Возвращает список последних сообщений в виде словарей."""
        try:
            conn = sqlite3.connect(self.db_path)
            c = conn.cursor()
            c.execute("SELECT timestamp, username, direction, text FROM messages ORDER BY id DESC LIMIT ?", (limit,))
            rows = c.fetchall()
            conn.close()
            return [
                {"timestamp": r[0], "user": r[1], "dir": r[2], "text": r[3]}
                for r in rows
            ]
        except:
            return []

    def get_recent_events(self, limit=10):
        """Возвращает список последних системных событий."""
        try:
            conn = sqlite3.connect(self.db_path)
            c = conn.cursor()
            c.execute("SELECT timestamp, event_type, description FROM events ORDER BY id DESC LIMIT ?", (limit,))
            rows = c.fetchall()
            conn.close()
            return [
                {"timestamp": r[0], "event_type": r[1], "details": r[2]}
                for r in rows
            ]
        except:
            return []

    # --- Методы для работы с группами (Phase 12.2) ---
    
    def get_group_settings(self, chat_id: int) -> Dict[str, Any]:
        """Получить настройки конкретной группы."""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            c.execute("SELECT * FROM group_settings WHERE chat_id = ?", (chat_id,))
            row = c.fetchone()
            conn.close()
            if row:
                return dict(row)
            return {}
        except Exception as e:
            logger.error(f"Error getting group settings: {e}")
            return {}

    def set_group_setting(self, chat_id: int, key: str, value: Any):
        """Установить конкретную настройку для группы."""
        try:
            conn = sqlite3.connect(self.db_path)
            c = conn.cursor()
            # Проверяем наличие записи
            c.execute("SELECT 1 FROM group_settings WHERE chat_id = ?", (chat_id,))
            if not c.fetchone():
                c.execute("INSERT INTO group_settings (chat_id) VALUES (?)", (chat_id,))
            
            # Динамический SQL (безопасно, так как key мы контролируем в коде)
            if key in ["is_active", "auto_moderation", "welcome_message", "allowed_commands"]:
                c.execute(f"UPDATE group_settings SET {key} = ?, updated_at = CURRENT_TIMESTAMP WHERE chat_id = ?", (value, chat_id))
            
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Error setting group setting: {e}")

    def delete_user_data(self, username: str, user_id: int):
        """Полное удаление данных пользователя (Phase 12.3 GDPR)."""
        try:
            conn = sqlite3.connect(self.db_path)
            c = conn.cursor()
            c.execute("DELETE FROM messages WHERE username = ? OR sender_id = ?", (username, user_id))
            conn.commit()
            conn.close()
            logger.info(f"🗑 GDPR: Data for {username} wiped from BlackBox")
            return True
        except Exception as e:
            logger.error(f"Failed to wipe user data: {e}")
            return False

    def get_uptime(self) -> str:
        """Возвращает строку uptime с момента запуска Krab."""
        delta = datetime.now() - self._start_time
        total_seconds = int(delta.total_seconds())
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours}h {minutes}m {seconds}s"
