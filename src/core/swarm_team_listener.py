# -*- coding: utf-8 -*-
"""
swarm_team_listener.py — message handlers для team-аккаунтов свёрма.

Регистрирует on_message на per-team Pyrogram clients, чтобы team-аккаунты
могли отвечать в ЛС и группах (при mention) через shared OpenClaw gateway.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

import structlog
from pyrogram import filters

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

    # В группах отвечаем только на reply/mention
    chat = message.chat
    is_private = str(getattr(chat, "type", "")).lower() in ("chattype.private", "private")
    if not is_private:
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
        return

    # Семафор per team — один запрос к OpenClaw за раз
    if team_name not in _team_semaphores:
        _team_semaphores[team_name] = asyncio.Semaphore(1)

    async with _team_semaphores[team_name]:
        system_prompt = get_team_system_prompt(team_name)
        session_id = f"swarm_listener_{team_name}_{chat_id}"

        try:
            # Показываем typing
            try:
                await client.send_chat_action(chat_id, "typing")
            except Exception:
                pass

            chunks: list[str] = []
            async for chunk in openclaw.send_message_stream(
                message=text,
                chat_id=session_id,
                system_prompt=system_prompt,
                force_cloud=True,
                max_output_tokens=2048,
                disable_tools=True,
            ):
                chunks.append(chunk)

            response = "".join(chunks).strip()
            if not response:
                return

            # Ограничиваем длину ответа
            if len(response) > 4000:
                response = response[:3990] + "..."

            await message.reply(response)

            logger.info(
                "swarm_team_replied",
                team=team_name,
                chat_id=chat_id,
                user_id=getattr(message.from_user, "id", 0),
                response_len=len(response),
            )

        except Exception as exc:
            logger.warning(
                "swarm_team_reply_failed",
                team=team_name,
                chat_id=chat_id,
                error=str(exc),
            )


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

    logger.info("swarm_team_handler_registered", team=team)
