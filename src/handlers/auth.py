# -*- coding: utf-8 -*-
"""
Auth Utilities — Общие утилиты авторизации для всех обработчиков.

Зачем: В main.py паттерн проверки прав дублировался 15+ раз.
Теперь каждый handler вызывает одну из этих функций.
"""

import os
from pyrogram.types import Message


def get_owner() -> str:
    """Возвращает username владельца (без @)."""
    return os.getenv("OWNER_USERNAME", "").replace("@", "").strip()


def get_superusers() -> list[str]:
    """Возвращает список суперпользователей (без @, username/ID)."""
    raw = os.getenv("SUPERUSERS", "").split(",")
    users = [u.strip().replace("@", "") for u in raw if u.strip()]
    owner = get_owner()
    if owner and owner not in users:
        users.append(owner)
    return users


def get_allowed_users() -> list[str]:
    """Возвращает список разрешённых пользователей (включая владельца)."""
    raw = os.getenv("ALLOWED_USERS", "").split(",")
    users = [u.strip() for u in raw if u.strip()]
    owner = get_owner()
    if owner and owner not in users:
        users.append(owner)
    return users


def is_authorized(message: Message) -> bool:
    """
    Проверяет, авторизован ли отправитель сообщения.
    Разрешает: owner, allowed_users (по username или ID), is_self.
    """
    if not message.from_user:
        return False
    
    # Свои сообщения всегда разрешены
    if message.from_user.is_self:
        return True

    sender = message.from_user.username or ""
    sender_id = str(message.from_user.id)
    allowed = get_allowed_users()
    superusers = get_superusers()

    return (
        sender in allowed
        or sender_id in allowed
        or sender.replace("@", "") in superusers
        or sender_id in superusers
    )


def is_owner(message: Message) -> bool:
    """
    Проверяет, является ли отправитель владельцем.
    Используется для опасных команд (!exec, !sh, !panic).
    """
    if not message.from_user:
        return False
    
    if message.from_user.is_self:
        return True

    sender = message.from_user.username or ""
    return sender == get_owner()


def is_superuser(message: Message) -> bool:
    """
    Проверяет, является ли отправитель владельцем или SUPERUSER.
    Используется для расширенного админ-контроля.
    """
    if not message.from_user:
        return False
    if message.from_user.is_self:
        return True
    if is_owner(message):
        return True

    sender = (message.from_user.username or "").replace("@", "")
    sender_id = str(message.from_user.id)
    superusers = get_superusers()
    return sender in superusers or sender_id in superusers


def get_msg_method(message: Message):
    """
    Определяет метод ответа: edit_text (свои сообщения) или reply_text (чужие).
    Используется для «перезаписи» своего сообщения vs ответа другому.
    """
    if message.from_user and message.from_user.is_self:
        return message.edit_text
    return message.reply_text
