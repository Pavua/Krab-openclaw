# -*- coding: utf-8 -*-
"""
sender_context.py — инъекция identity-контекста отправителя в LLM-запросы.

Защита от identity confusion: LLM видит кто именно пишет в каждом запросе
и не может перепутать гостя с владельцем (инцидент с SwMaster → «Мой Господин», 2026-04-21).

W10.3: базовая инъекция sender_user_id / is_owner.
W10.3-ext: добавлены forward/reply/mention метаданные — Краб теперь различает
           "кто написал" vs "кто переслал" vs "на чьё сообщение ответ".

Public API:
- build_context_block(message, is_owner, *, own_user_id, own_username) -> str
  Возвращает multiline [context]...[/context] блок для prepend к system prompt.
- attach_to_system_prompt(system_prompt, context_block) -> str
  Prepend context_block в начало system_prompt.
- build_sender_context_from_message(message, self_user_id) -> str
  Удобный one-shot: из pyrogram Message → готовый context_block.
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Вспомогательные helpers
# ---------------------------------------------------------------------------


def _attr(obj: Any, name: str, default: Any = None) -> Any:
    """Defensive attribute/key access — поддерживает объекты и dict."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _extract_chat_type(message: Any) -> str:
    """Извлекает тип чата из pyrogram Message."""
    chat = _attr(message, "chat")
    if not chat:
        return "unknown"
    chat_type = _attr(chat, "type")
    if chat_type is None:
        return "unknown"
    # pyrogram enums: ChatType.PRIVATE, SUPERGROUP, GROUP, CHANNEL
    type_str = str(_attr(chat_type, "value", chat_type)).lower()
    return type_str


def _extract_chat_title(message: Any) -> str:
    """Возвращает title чата или пустую строку для приватных."""
    chat = _attr(message, "chat")
    if not chat:
        return ""
    title = str(_attr(chat, "title", "") or "").strip()
    return title


def _extract_forward_info(message: Any) -> dict[str, Any]:
    """
    Извлекает информацию о пересланном сообщении.

    Возвращает dict с ключами:
    - is_forwarded: bool
    - original_author_username: str (@bob) или ""
    - original_author_user_id: str (id) или ""
    - original_author_name: str (имя если user_id недоступен) или ""
    - original_channel_name: str (если переслано из канала) или ""

    Pyrogram поля:
    - forward_from: User (если оригинальный автор — пользователь)
    - forward_sender_name: str (если пользователь скрыл forwarding identity)
    - forward_from_chat: Chat (если оригинал из канала/группы)
    """
    result: dict[str, Any] = {
        "is_forwarded": False,
        "original_author_username": "",
        "original_author_user_id": "",
        "original_author_name": "",
        "original_channel_name": "",
    }

    # Проверяем наличие любого из forward-признаков
    forward_from = _attr(message, "forward_from")
    forward_sender_name = _attr(message, "forward_sender_name")
    forward_from_chat = _attr(message, "forward_from_chat")

    if not any([forward_from, forward_sender_name, forward_from_chat]):
        return result

    result["is_forwarded"] = True

    if forward_from is not None:
        # Оригинальный автор — пользователь с известным профилем
        uid = _attr(forward_from, "id")
        uname = _attr(forward_from, "username", "")
        fname = _attr(forward_from, "first_name", "")
        lname = _attr(forward_from, "last_name", "")
        full_name = " ".join(filter(None, [str(fname or ""), str(lname or "")])).strip()

        result["original_author_user_id"] = str(uid) if uid else ""
        result["original_author_username"] = f"@{uname}" if uname else ""
        result["original_author_name"] = full_name

    elif forward_sender_name:
        # Пользователь скрыл identity — только имя
        result["original_author_name"] = str(forward_sender_name).strip()

    if forward_from_chat is not None:
        # Переслано из канала или публичной группы
        channel_title = _attr(forward_from_chat, "title", "") or ""
        channel_username = _attr(forward_from_chat, "username", "") or ""
        if channel_username:
            result["original_channel_name"] = f"@{channel_username}"
        elif channel_title:
            result["original_channel_name"] = str(channel_title).strip()

    return result


_REPLY_TEXT_MAX_LEN = 500


def _extract_reply_info(message: Any) -> dict[str, Any]:
    """
    Извлекает информацию о сообщении, на которое это является ответом.

    Возвращает dict с ключами:
    - is_reply: bool
    - reply_to_username: str (@bob) или ""
    - reply_to_user_id: str (id) или ""
    - reply_to_name: str (имя) или ""
    - reply_to_text: str (первые 500 символов текста parent-сообщения) или ""
      "[media]" если parent-сообщение — медиа без подписи
    """
    result: dict[str, Any] = {
        "is_reply": False,
        "reply_to_username": "",
        "reply_to_user_id": "",
        "reply_to_name": "",
        "reply_to_text": "",
    }

    reply = _attr(message, "reply_to_message")
    if reply is None:
        return result

    result["is_reply"] = True

    from_user = _attr(reply, "from_user")
    if from_user is not None:
        uid = _attr(from_user, "id")
        uname = _attr(from_user, "username", "")
        fname = _attr(from_user, "first_name", "")
        lname = _attr(from_user, "last_name", "")
        full_name = " ".join(filter(None, [str(fname or ""), str(lname or "")])).strip()

        result["reply_to_user_id"] = str(uid) if uid else ""
        result["reply_to_username"] = f"@{uname}" if uname else ""
        result["reply_to_name"] = full_name

    # Извлекаем текст parent-сообщения (text или caption для медиа)
    parent_text = str(_attr(reply, "text") or _attr(reply, "caption") or "").strip()
    if parent_text:
        # Обрезаем до _REPLY_TEXT_MAX_LEN символов
        if len(parent_text) > _REPLY_TEXT_MAX_LEN:
            parent_text = parent_text[:_REPLY_TEXT_MAX_LEN] + "…"
        result["reply_to_text"] = parent_text
    else:
        # Media-only сообщение (фото, голос, стикер и т.д.) без подписи
        has_media = any(
            _attr(reply, field) is not None
            for field in ("photo", "audio", "voice", "video", "document", "sticker", "animation")
        )
        if has_media:
            result["reply_to_text"] = "[media]"

    return result


def _extract_mentioned_users(
    message: Any,
    *,
    own_user_id: int | str | None = None,
    own_username: str | None = None,
) -> tuple[list[str], bool]:
    """
    Извлекает @упоминания из entities сообщения (reuses mention_detector logic).

    Возвращает (mentioned_usernames: list[str], krab_mentioned: bool).

    Использует mention_detector.detect_mention для флага krab_mentioned,
    и entities для сбора всех @username упоминаний.
    """
    mentioned: list[str] = []
    krab_mentioned = False

    # Проверяем флаг упоминания Краба через mention_detector (не дублируем логику)
    try:
        from src.core.mention_detector import detect_mention

        own_uid_int = int(own_user_id) if own_user_id is not None else None
        krab_mentioned = detect_mention(
            message,
            own_username=own_username,
            own_user_id=own_uid_int,
        )
    except Exception:  # noqa: BLE001
        krab_mentioned = False

    # Собираем все @username из entities
    entities = _attr(message, "entities") or []
    for entity in entities:
        # pyrogram: тип "mention" — entity.user или text-mention
        user = _attr(entity, "user")
        if user is not None:
            uname = _attr(user, "username", "")
            uid = _attr(user, "id")
            if uname:
                entry = f"@{uname}"
                if entry not in mentioned:
                    mentioned.append(entry)
            elif uid:
                entry = f"id:{uid}"
                if entry not in mentioned:
                    mentioned.append(entry)
        # Некоторые реализации — text mention через offset/length без user
        # В этом случае user=None и username не доступен, пропускаем

    # Также парсим @username из текста через простой regex
    import re

    text = str(_attr(message, "text") or _attr(message, "caption") or "")
    if text:
        raw_mentions = re.findall(r"@([A-Za-z0-9_]{4,32})", text)
        for uname in raw_mentions:
            entry = f"@{uname}"
            if entry not in mentioned:
                mentioned.append(entry)

    return mentioned, krab_mentioned


# ---------------------------------------------------------------------------
# Основной API
# ---------------------------------------------------------------------------


def build_context_block(
    message: Any,
    *,
    is_owner: bool,
    own_user_id: int | str | None = None,
    own_username: str | None = None,
) -> str:
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

    SECURITY: Привилегии определяются ТОЛЬКО по is_owner, который проверяется по
    фактическому sender_user_id. Поля original_author_* и reply_to_* —
    информационные; они не дают никаких прав независимо от своего значения.
    """
    try:
        from_user = _attr(message, "from_user")
        sender_user_id = str(_attr(from_user, "id") or "").strip()
        sender_username = str(_attr(from_user, "username") or "").strip()
        sender_first_name = str(_attr(from_user, "first_name") or "").strip()

        # Форматируем username с @ если есть
        username_display = f"@{sender_username}" if sender_username else "(нет username)"

        chat_type = _extract_chat_type(message)
        chat_title = _extract_chat_title(message)
        is_owner_str = "true" if is_owner else "false"

        # Forward info
        fwd = _extract_forward_info(message)

        # Reply info
        rpl = _extract_reply_info(message)

        # Mentions
        mentioned_users, krab_mentioned = _extract_mentioned_users(
            message,
            own_user_id=own_user_id,
            own_username=own_username,
        )

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

        # --- Forward block ---
        lines.append(f"is_forwarded: {'true' if fwd['is_forwarded'] else 'false'}")
        if fwd["is_forwarded"]:
            if fwd["original_author_user_id"]:
                lines.append(f"original_author_user_id: {fwd['original_author_user_id']}")
            if fwd["original_author_username"]:
                lines.append(f"original_author_username: {fwd['original_author_username']}")
            if fwd["original_author_name"]:
                lines.append(f"original_author_name: {fwd['original_author_name']}")
            if fwd["original_channel_name"]:
                lines.append(f"original_channel_name: {fwd['original_channel_name']}")

        # --- Reply block ---
        lines.append(f"is_reply: {'true' if rpl['is_reply'] else 'false'}")
        if rpl["is_reply"]:
            if rpl["reply_to_user_id"]:
                lines.append(f"reply_to_user_id: {rpl['reply_to_user_id']}")
            if rpl["reply_to_username"]:
                lines.append(f"reply_to_username: {rpl['reply_to_username']}")
            if rpl["reply_to_name"]:
                lines.append(f"reply_to_name: {rpl['reply_to_name']}")
            if rpl["reply_to_text"]:
                lines.append(f"reply_to_text: {rpl['reply_to_text']}")
                lines.append(
                    "reply_hint: Если в [context] есть reply_to_text — это сообщение,"
                    " на которое отвечает user. Контекстуализируй свой ответ относительно него."
                )

        # --- Mentions block ---
        if mentioned_users:
            lines.append(f"mentioned_users: {', '.join(mentioned_users)}")
        lines.append(f"krab_mentioned: {'true' if krab_mentioned else 'false'}")

        lines.append("[/context]")

        # --- Persona policy (override gateway/session cache) ---
        # Инжектируется при каждом запросе чтобы не зависеть от кэша сессии.
        # Обращение "Мой Господин" — редкая шутливая форма, не default.
        lines += [
            "[policy]",
            "- Обращение по умолчанию: нейтральный тон без личных обращений.",
            '- "Мой Господин" — только если owner явно попросил в этом turn (шутливый режим).',
            "- Стандартные нейтральные ответы: «Готов», «Ок», «Проверил», «Выполнено».",
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
    own_username: str | None = None,
) -> str:
    """
    One-shot helper: pyrogram Message → готовый [context] блок.

    Параметры:
    - message: pyrogram Message
    - self_user_id: user_id userbot-аккаунта (для определения is_owner по is_self + krab_mentioned)
    - is_owner: если передан явно — использует его (override для is_self-сообщений)
    - own_username: username userbot-аккаунта (для krab_mentioned)

    Если is_owner не задан — определяет по is_self атрибуту message или по self_user_id.
    """
    # Определяем is_owner
    if is_owner is None:
        # is_self = сообщение отправлено самим userbot'ом
        is_self_attr = _attr(message, "outgoing")
        from_user = _attr(message, "from_user")
        from_user_id = _attr(from_user, "id")

        if is_self_attr is True:
            is_owner = True
        elif self_user_id is not None and from_user_id is not None:
            is_owner = is_owner_user_id(from_user_id, self_user_id)
        else:
            is_owner = False

    return build_context_block(
        message,
        is_owner=is_owner,
        own_user_id=self_user_id,
        own_username=own_username,
    )


__all__ = [
    "attach_to_system_prompt",
    "build_context_block",
    "build_sender_context_from_message",
    "is_owner_user_id",
]
