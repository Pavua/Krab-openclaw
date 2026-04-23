# -*- coding: utf-8 -*-
"""
sender_context.py — инъекция identity-контекста отправителя в LLM-запросы.

Защита от identity confusion: LLM видит кто именно пишет в каждом запросе
и не может перепутать гостя с владельцем (инцидент с SwMaster → «Мой Господин», 2026-04-21).

Public API:
- build_context_block(message, is_owner) -> str
  Возвращает multiline [context]...[/context] блок для prepend к system prompt.
- attach_to_system_prompt(system_prompt, context_block) -> str
  Prepend context_block в начало system_prompt.
- build_sender_context_from_message(message, self_user_id) -> str
  Удобный one-shot: из pyrogram Message → готовый context_block.
"""

from __future__ import annotations

from typing import Any


def _extract_chat_type(message: Any) -> str:
    """Извлекает тип чата из pyrogram Message."""
    chat = getattr(message, "chat", None)
    if not chat:
        return "unknown"
    chat_type = getattr(chat, "type", None)
    if chat_type is None:
        return "unknown"
    # pyrogram enums: ChatType.PRIVATE, SUPERGROUP, GROUP, CHANNEL
    type_str = str(getattr(chat_type, "value", chat_type)).lower()
    return type_str


def _extract_chat_title(message: Any) -> str:
    """Возвращает title чата или пустую строку для приватных."""
    chat = getattr(message, "chat", None)
    if not chat:
        return ""
    title = str(getattr(chat, "title", "") or "").strip()
    return title


def build_context_block(message: Any, *, is_owner: bool) -> str:
    """
    Строит [context]...[/context] блок с метаданными отправителя.

    Параметры:
    - message: pyrogram Message (или любой объект с from_user/chat атрибутами)
    - is_owner: True если отправитель является owner'ом userbot'а
    - own_user_id: user_id самого Краба (для определения krab_mentioned)
    - own_username: username Краба (для определения krab_mentioned)

    Включает:
    - sender identity (user_id, username, first_name)
    - chat_type, chat_title
    - is_owner
    - forward info (is_forwarded, original_author_*)
    - reply info (is_reply, reply_to_*)
    - mentioned_users list, krab_mentioned flag
    - [policy] блок: persona-правила обращения (override gateway/session cache)

    Возвращает multiline строку для prepend к system prompt.
    Никогда не выбрасывает исключений — при отсутствии данных возвращает безопасный fallback.
    """
    try:
        from_user = getattr(message, "from_user", None)
        sender_user_id = str(getattr(from_user, "id", "") or "").strip()
        sender_username = str(getattr(from_user, "username", "") or "").strip()
        sender_first_name = str(getattr(from_user, "first_name", "") or "").strip()

        # Форматируем username с @ если есть
        username_display = f"@{sender_username}" if sender_username else "(нет username)"

        chat_type = _extract_chat_type(message)
        chat_title = _extract_chat_title(message)
        is_owner_str = "true" if is_owner else "false"

        lines = [
            "[context]",
            f"sender_user_id: {sender_user_id or 'unknown'}",
            f"sender_username: {username_display}",
            f"sender_first_name: {sender_first_name or 'unknown'}",
            f"chat_type: {chat_type}",
        ]
        if chat_title:
            lines.append(f"chat_title: {chat_title}")
        lines.append(f"is_owner: {is_owner_str}")
        lines.append("[/context]")

        # --- Persona policy (override gateway/session cache) ---
        # Инжектируется при каждом запросе чтобы не зависеть от кэша сессии.
        # ВАЖНО: этот блок имеет приоритет над любыми инструкциями в SOUL.md/USER.md.
        lines += [
            "[policy]",
            "- Обращение по умолчанию: нейтральный тон БЕЗ личных обращений.",
            "- ЗАПРЕЩЕНО начинать ответ с «Мой Господин», «Господин», «Хозяин» или аналогов.",
            "- «Мой Господин» допустимо ТОЛЬКО если owner явно написал в этом сообщении команду обращаться так.",
            "- Стандартное начало ответа: сразу суть — «Готово», «Проверил», «Да» или без обращения.",
            "[/policy]",
        ]

        return "\n".join(lines)
    except Exception:  # noqa: BLE001
        # Безопасный fallback — никогда не ломаем LLM flow
        is_owner_str = "true" if is_owner else "false"
        return f"[context]\nis_owner: {is_owner_str}\n[/context]"


def attach_to_system_prompt(system_prompt: str, context_block: str) -> str:
    """
    Prepend context_block в начало system_prompt.

    Почему в начало, а не в конец:
    - LLM лучше учитывает ранний контекст при формировании тона ответа;
    - identity-контекст должен быть виден ДО всех инструкций по роли.

    Если context_block пустой — возвращает system_prompt без изменений.
    """
    block = str(context_block or "").strip()
    base = str(system_prompt or "").strip()
    if not block:
        return base
    if not base:
        return block
    return f"{block}\n\n{base}"


def is_owner_user_id(user_id: int | str | None, self_user_id: int | str | None) -> bool:
    """
    Проверяет является ли пользователь owner'ом по user_id (self = userbot аккаунт).

    Используется для быстрой проверки без загрузки ACL-файла.
    Для полной ACL-проверки используй resolve_access_profile из core.access_control.
    """
    if not user_id or not self_user_id:
        return False
    try:
        return int(user_id) == int(self_user_id)
    except (TypeError, ValueError):
        return False


def build_sender_context_from_message(
    message: Any,
    *,
    self_user_id: int | str | None = None,
    is_owner: bool | None = None,
) -> str:
    """
    One-shot helper: pyrogram Message → готовый [context] блок.

    Параметры:
    - message: pyrogram Message
    - self_user_id: user_id userbot-аккаунта (для определения is_owner по is_self)
    - is_owner: если передан явно — использует его (override для is_self-сообщений)

    Если is_owner не задан — определяет по is_self атрибуту message или по self_user_id.
    """
    # Определяем is_owner
    if is_owner is None:
        # is_self = сообщение отправлено самим userbot'ом
        is_self_attr = getattr(message, "outgoing", None)
        from_user = getattr(message, "from_user", None)
        from_user_id = getattr(from_user, "id", None)

        if is_self_attr is True:
            is_owner = True
        elif self_user_id is not None and from_user_id is not None:
            is_owner = is_owner_user_id(from_user_id, self_user_id)
        else:
            is_owner = False

    return build_context_block(message, is_owner=is_owner)


__all__ = [
    "attach_to_system_prompt",
    "build_context_block",
    "build_sender_context_from_message",
    "is_owner_user_id",
]
