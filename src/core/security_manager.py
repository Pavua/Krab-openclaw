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
    def __init__(self, owner_username: str, config=None):
        self.config = config
        # Нормализация username (без @)
        self.owner = (owner_username or "").replace("@", "").strip().lower()
        
        # Загружаем роли из конфига
        # Формат: config["security.roles"] = {"username": "admin", "12345": "user"}
        self.roles = {}
        if config:
            self.roles = config.get("security.roles", {})
            self.stealth_mode = config.get("security.stealth_mode", False)
        else:
            self.stealth_mode = False

        # Legacy-совместимость: часть тестов и старых обработчиков
        # обращается к отдельным коллекциям admins/blocked.
        self.admins = []
        self.blocked = []

    def get_role(self, user_identifier: str) -> str:
        """Получить роль пользователя по username или ID (строкой)."""
        ident = str(user_identifier).replace("@", "").lower().strip()
        if ident == self.owner:
            return "owner"
        return self.roles.get(ident, "guest")

    def grant_role(self, user_identifier: str, role: str) -> bool:
        """Назначить роль пользователю."""
        if not self.config:
            return False
            
        ident = str(user_identifier).replace("@", "").lower().strip()
        if ident == self.owner:
            return False # Нельзя менять роль владельца
            
        if role not in ["admin", "user", "guest", "blocked"]:
            return False
            
        self.roles[ident] = role
        self.config.set("security.roles", self.roles)
        logger.info(f"Role granted: {ident} -> {role}")
        return True

    def revoke_role(self, user_identifier: str) -> bool:
        """Сбросить роль (станет guest)."""
        if not self.config:
            return False
            
        ident = str(user_identifier).replace("@", "").lower().strip()
        if ident == self.owner:
            return False
            
        if ident in self.roles:
            del self.roles[ident]
            self.config.set("security.roles", self.roles)
            logger.info(f"Role revoked: {ident}")
            return True
        return False

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

    # Совместимость со старым API
    def get_user_role(self, username: str, user_id: int) -> str:
        """Определяет эффективную роль пользователя."""
        # Проверяем ID, затем Username
        role_by_id = self.get_role(str(user_id))
        role_by_name = self.get_role(username)
        
        # Приоритет: Owner > Blocked > Admin > User > Guest
        if "owner" in [role_by_id, role_by_name]: return "owner"
        
        # Stealth Mode check
        if self.stealth_mode: return "stealth_restricted"

        # Legacy fallback: explicit списки admins/blocked.
        username_norm = (username or "").replace("@", "").strip().lower()
        if user_id in self.blocked or username_norm in {str(item).lower() for item in self.blocked}:
            return "blocked"
        if username_norm in {str(item).replace("@", "").lower() for item in self.admins}:
            return "admin"

        if "blocked" in [role_by_id, role_by_name]: return "blocked"
        if "admin" in [role_by_id, role_by_name]: return "admin"
        if "user" in [role_by_id, role_by_name]: return "user"

        # По умолчанию считаем пользователя "user" (не guest),
        # чтобы сохранить обратную совместимость с ранними фазами.
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
        if self.config:
            self.config.set("security.stealth_mode", self.stealth_mode)
        logger.info(f"🕶️ Stealth Mode changed to: {self.stealth_mode}")
        return self.stealth_mode
