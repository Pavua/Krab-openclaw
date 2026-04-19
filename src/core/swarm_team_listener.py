# -*- coding: utf-8 -*-
"""
swarm_team_listener.py — message handlers для team-аккаунтов свёрма.

Регистрирует on_message на per-team Pyrogram clients, чтобы team-аккаунты
отвечали в ЛС как полноценные AI-агенты своей команды через OpenClaw gateway.

Возможности:
- Streaming ответ с прогресс-обновлением через edit_text()
- Team-branded emoji в ответах
- Доступ к tools (web_search и др.)
- Owner-only фильтр в ЛС (только owner видит AI-ответы)
- Отдельная session history per (team, chat_id)
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

import structlog
from pyrogram import filters

from .access_control import is_owner_user_id
from .swarm_team_prompts import get_team_system_prompt

if TYPE_CHECKING:
    from pyrogram import Client
    from pyrogram.types import Message

    from ..openclaw_client import OpenClawClient

logger = structlog.get_logger("krab.swarm_team_listener")

# Глобальный флаг — owner может выключить через !swarm listen off
_listeners_enabled: bool = True

# Rate limit: минимум секунд между ответами per (team, chat_id)
_COOLDOWN_SEC: float = 5.0
_last_reply: dict[str, float] = {}

# Семафор per team — один запрос к OpenClaw за раз
_team_semaphores: dict[str, asyncio.Semaphore] = {}

# Emoji-бренд для каждой команды
TEAM_EMOJI: dict[str, str] = {
    "traders": "📈",
    "coders": "💻",
    "analysts": "📊",
    "creative": "🎨",
}

# Текст-заглушка пока идёт генерация
_THINKING_TEXT = "⏳ думаю..."

# Минимальный интервал между edit_text во время стриминга (секунды)
_STREAM_EDIT_INTERVAL: float = 1.5

# Максимальная длина Telegram-сообщения
_TG_MAX_LEN: int = 4096


def is_listeners_enabled() -> bool:
    """Проверяет глобальный флаг listeners."""
    return _listeners_enabled


def set_listeners_enabled(enabled: bool) -> None:
    """Устанавливает глобальный флаг listeners."""
    global _listeners_enabled  # noqa: PLW0603
    _listeners_enabled = enabled
    logger.info("swarm_team_listeners_toggled", enabled=enabled)


def _check_cooldown(team: str, chat_id: int | str) -> bool:
    """True если cooldown прошёл и можно отвечать."""
    key = f"{team}:{chat_id}"
    now = time.monotonic()
    last = _last_reply.get(key, 0.0)
    if now - last < _COOLDOWN_SEC:
        return False
    _last_reply[key] = now
    return True


def _is_owner(user_id: int) -> bool:
    """Проверяет, является ли пользователь owner'ом через unified ACL-источник."""
    return is_owner_user_id(user_id)


def _build_header(team: str) -> str:
    """Строит header ответа с team-branded emoji."""
    emoji = TEAM_EMOJI.get(team, "🤖")
    return f"{emoji} **{team.capitalize()}**\n\n"


def _trim_response(text: str, max_len: int = _TG_MAX_LEN - 50) -> str:
    """Обрезает ответ до допустимой длины."""
    if len(text) > max_len:
        return text[: max_len - 3] + "..."
    return text


async def _stream_reply(
    team_name: str,
    client: "Client",
    message: "Message",
    openclaw: "OpenClawClient",
    text: str,
) -> None:
    """
    Вызывает OpenClaw с streaming и обновляет сообщение по мере поступления чанков.

    Алгоритм:
    1. Отправляем заглушку "думаю..."
    2. Накапливаем чанки, каждые _STREAM_EDIT_INTERVAL секунд делаем edit_text
    3. Итоговый edit_text с полным ответом + team emoji header
    """
    header = _build_header(team_name)
    session_id = f"swarm_dm_{team_name}_{message.chat.id}"
    system_prompt = get_team_system_prompt(team_name)

    # Отправляем заглушку
    try:
        sent = await message.reply(_THINKING_TEXT, quote=True)
    except Exception as exc:
        logger.warning("swarm_team_reply_send_failed", team=team_name, error=str(exc))
        return

    chunks: list[str] = []
    last_edit_time = time.monotonic()
    had_error = False

    try:
        async for chunk in openclaw.send_message_stream(
            message=text,
            chat_id=session_id,
            system_prompt=system_prompt,
            force_cloud=True,
            max_output_tokens=2048,
            # tools включены — агент может использовать web_search и т.д.
            disable_tools=False,
        ):
            chunks.append(chunk)

            # Промежуточный edit с накопленным текстом
            now = time.monotonic()
            if now - last_edit_time >= _STREAM_EDIT_INTERVAL and chunks:
                partial = _trim_response("".join(chunks))
                preview = f"{header}{partial} ✍️"
                try:
                    await sent.edit_text(preview)
                    last_edit_time = now
                except Exception:
                    pass  # edit может упасть — не критично

    except Exception as exc:
        logger.warning(
            "swarm_team_stream_error",
            team=team_name,
            chat_id=message.chat.id,
            error=str(exc),
        )
        had_error = True

    if had_error or not chunks:
        try:
            await sent.edit_text("⚠️ Не удалось получить ответ. Попробуй позже.")
        except Exception:
            pass
        return

    # Финальный edit с полным ответом
    full_response = _trim_response("".join(chunks).strip())
    final_text = f"{header}{full_response}"

    try:
        await sent.edit_text(final_text)
    except Exception as exc:
        # edit упал (напр. текст не изменился) — игнорируем
        logger.debug("swarm_team_final_edit_skipped", team=team_name, error=str(exc))

    logger.info(
        "swarm_team_replied",
        team=team_name,
        chat_id=message.chat.id,
        user_id=getattr(message.from_user, "id", 0),
        response_len=len(full_response),
        session_id=session_id,
    )


async def _handle_team_message(
    team_name: str,
    client: "Client",
    message: "Message",
    openclaw: "OpenClawClient",
) -> None:
    """Обрабатывает входящее сообщение для team-аккаунта."""
    if not _listeners_enabled:
        return

    # Игнорируем свои сообщения
    me = getattr(client, "me", None) or await client.get_me()
    if message.from_user and message.from_user.id == me.id:
        return

    chat = message.chat
    is_private = str(getattr(chat, "type", "")).lower() in ("chattype.private", "private")

    if is_private:
        # В ЛС отвечаем только owner'у — защита от случайных пользователей
        sender_id = getattr(message.from_user, "id", 0)
        if not _is_owner(sender_id):
            logger.debug(
                "swarm_team_dm_non_owner_ignored",
                team=team_name,
                sender_id=sender_id,
            )
            return
    else:
        # В группах отвечаем только на reply/mention
        is_reply_to_me = (
            message.reply_to_message
            and message.reply_to_message.from_user
            and message.reply_to_message.from_user.id == me.id
        )
        is_mention = me.username and f"@{me.username}" in (message.text or "")
        if not is_reply_to_me and not is_mention:
            return

    text = (message.text or message.caption or "").strip()
    if not text or len(text) < 2:
        return

    chat_id = message.chat.id
    if not _check_cooldown(team_name, chat_id):
        logger.debug("swarm_team_cooldown_skip", team=team_name, chat_id=chat_id)
        return

    # Семафор per team — один запрос к OpenClaw за раз
    if team_name not in _team_semaphores:
        _team_semaphores[team_name] = asyncio.Semaphore(1)

    async with _team_semaphores[team_name]:
        # Показываем typing
        try:
            await client.send_chat_action(chat_id, "typing")
        except Exception:
            pass

        await _stream_reply(team_name, client, message, openclaw, text)


def register_team_message_handler(
    team_name: str,
    client: "Client",
    openclaw: "OpenClawClient",
) -> None:
    """
    Регистрирует on_message handler на team Pyrogram client.

    Вызывается из _start_swarm_team_clients() после cl.start().
    """
    team = team_name.lower()

    @client.on_message(filters.text & ~filters.me, group=10)
    async def _on_team_message(_client: "Client", message: "Message") -> None:
        await _handle_team_message(team, _client, message, openclaw)

    logger.info("swarm_team_handler_registered", team=team, emoji=TEAM_EMOJI.get(team, "🤖"))
