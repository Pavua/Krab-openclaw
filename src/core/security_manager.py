# -*- coding: utf-8 -*-
"""
Security Manager для Krab v2.5.
Реализует Anti-injection промптов и управление ролями (Owner, Admin, User).
"""

import re
import structlog

logger = structlog.get_logger("SecurityManager")

# Простые паттерны для детекции prompt injection
INJECTION_PATTERNS = [
    r"ignore all previous instructions",
    r"disregard all previous instructions",
    r"system prompt:",
    r"new instructions:",
    r"you are now a",
    r"forget everything you know",
    r"stop being",
]

class SecurityManager:
    def __init__(self, owner_username: str):
        self.owner = owner_username.replace("@", "").strip()
        self.admins = []
        self.users = []
        self.blocked = []
        self.stealth_mode = False  # Режим скрытности (Panic Button)

    def is_safe(self, text: str) -> bool:
        """Проверка текста на наличие попыток инъекции."""
        if not text:
            return True
        
        text_lower = text.lower()
        for pattern in INJECTION_PATTERNS:
            if re.search(pattern, text_lower):
                logger.warning("🚨 Potential Prompt Injection detected", pattern=pattern, text=text[:50])
                return False
        return True

    def get_user_role(self, username: str, user_id: int) -> str:
        """Определяет роль пользователя."""
        username = (username or "").replace("@", "").strip()
        
        if username == self.owner:
            return "owner"
        
        # Если включен режим Stealth, все кроме владельца считаются временно ограниченными
        if self.stealth_mode:
            return "stealth_restricted"

        if username in self.admins or user_id in self.admins:
            return "admin"
        if username in self.blocked or user_id in self.blocked:
            return "blocked"
        return "user"

    def can_execute_command(self, username: str, user_id: int, command_level: str = "user") -> bool:
        """Проверяет права на выполнение команды."""
        role = self.get_user_role(username, user_id)
        
        if role == "owner":
            return True
        if role in ["blocked", "stealth_restricted"]:
            return False
            
        if command_level == "admin":
            return role == "admin"
        if command_level == "user":
            return role in ["user", "admin"]
            
        return False

    def toggle_stealth(self) -> bool:
        """Переключает режим Stealth Mode."""
        self.stealth_mode = not self.stealth_mode
        logger.info(f"🕶️ Stealth Mode changed to: {self.stealth_mode}")
        return self.stealth_mode

