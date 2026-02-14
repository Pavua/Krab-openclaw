# -*- coding: utf-8 -*-
"""
Telegram Control Handler.

Добавляет команды усиленного контроля Telegram:
1) !chatid — быстрый просмотр идентификатора чата.
2) !summaryx <X> [target] [--focus "..."] — саммари последних X сообщений
   выбранного чата через Telegram API.
"""

from __future__ import annotations

import os
import shlex
import time
import uuid
from typing import Any

import structlog
from pyrogram import enums, filters, Client
from pyrogram.errors import (
    ChannelPrivate,
    PeerIdInvalid,
    UsernameInvalid,
    UsernameNotOccupied,
)
from pyrogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from .auth import is_superuser
from src.core.telegram_chat_resolver import TelegramChatResolver
from src.core.telegram_summary_service import SummaryRequest, TelegramSummaryService

logger = structlog.get_logger(__name__)


class TelegramControlHandler:
    """Обработчик команд управления Telegram (summary, chatid)."""

    def __init__(self, deps: dict):
        self.safe_handler = deps["safe_handler"]
        self.resolver: TelegramChatResolver = deps.get(
            "telegram_chat_resolver"
        ) or TelegramChatResolver(deps["black_box"])
        self.summary_service: TelegramSummaryService = deps.get(
            "telegram_summary_service"
        ) or TelegramSummaryService(deps["router"])
        self.picker_state: dict[str, dict[str, Any]] = {}

    def _is_target_allowed(self, chat_id: int) -> bool:
        raw = os.getenv("SUMMARYX_ALLOWED_CHATS", "").strip()
        if not raw:
            return True
        parts = {p.strip() for p in raw.split(",") if p.strip()}
        if "*" in parts:
            return True
        return str(chat_id) in parts

    def _parse_summary_args(self, message_text: str) -> tuple[int, str, str]:
        """Парсит `!summaryx` аргументы: X, target, focus."""
        argv = shlex.split(message_text)
        if len(argv) < 2:
            raise ValueError("Формат: `!summaryx <20-2000> [chat_id] [--focus topic]`")
        try:
            limit = int(argv[1])
        except ValueError as exc:
            raise ValueError("X должен быть числом.") from exc

        target = ""
        focus = ""
        i = 2
        while i < len(argv):
            token = argv[i]
            if token == "--focus":
                focus = " ".join(argv[i + 1 :]).strip()
                break
            if not target:
                target = token
            i += 1
        return limit, target, focus

    async def _run_summary(
        self,
        client: Client,
        message: Message,
        target_chat_id: int,
        target_title: str,
        limit: int,
        focus: str,
    ):
        """Общий контур генерации summary."""
        if not self._is_target_allowed(target_chat_id):
            await message.reply_text("⛔ Этот чат недоступен (not allowed).")
            return

        # Валидация прав доступа через get_chat (быстрый чек)
        try:
            await client.get_chat(target_chat_id)
        except (ChannelPrivate, PeerIdInvalid):
            await message.reply_text(
                f"❌ Чат `{target_title}` недоступен (Private/Invalid).\n"
                "Бот должен быть участником или админом."
            )
            return
        except Exception as exc:
            logger.warning("get_chat failed check", error=str(exc))
            # Пробуем продолжить, вдруг get_chat_history сработает

        req = SummaryRequest(
            chat_id=target_chat_id, limit=limit, focus=focus, language="ru"
        )
        notification = await message.reply_text(
            f"⏳ Анализ {req.limit} сообщений `{target_title}`..."
        )

        try:
            summary = await self.summary_service.summarize(
                client=client,
                req=req,
                chat_title=target_title,
            )
            
            # Если вернулась строка ошибки (начинается с ❌)
            if summary.startswith("❌"):
                await notification.edit_text(summary)
                return

            focus_line = f"\n🎯 Фокус: `{focus}`\n" if focus else ""
            await notification.edit_text(
                f"✅ **Summary** ({target_title})\n"
                f"{focus_line}\n"
                f"{summary}"
            )
        except Exception as exc:
            logger.error("summaryx failed", error=str(exc), chat_id=target_chat_id)
            await notification.edit_text(f"❌ Ошибка: {exc}")

    async def chatid_command(self, client: Client, message: Message):
        """Показывает технический ID и тип текущего чата."""
        if not is_superuser(message):
            return
        chat = message.chat
        title = chat.title or chat.first_name or chat.username or "N/A"
        chat_type = (
            chat.type.name.lower() if hasattr(chat.type, "name") else str(chat.type)
        )
        await message.reply_text(f"`{chat.id}` | {chat_type} | {title}")

    async def summaryx_command(self, client: Client, message: Message):
        """Summary последних X сообщений выбранного чата."""
        if not is_superuser(message):
            return

        try:
            limit, raw_target, focus = self._parse_summary_args(message.text or "")
            limit = self.summary_service.clamp_limit(limit)
        except ValueError as exc:
            await message.reply_text(f"⚠️ {exc}")
            return

        # Если target не указан:
        if not raw_target:
            # В группах резюмируем текущий чат
            if message.chat.type != enums.ChatType.PRIVATE:
                target_title = message.chat.title or str(message.chat.id)
                await self._run_summary(
                    client, message, message.chat.id, target_title, limit, focus
                )
                return

            # В ЛС показываем picker недавних чатов
            recent = self.resolver.get_recent_chats(
                limit=self.resolver.max_picker_items
            )
            if not recent:
                await message.reply_text(
                    "⚠️ Нет недавних чатов.\n"
                    "Используй `!summaryx 100 @username` или `!summaryx 100 -100...`"
                )
                return

            token = uuid.uuid4().hex[:8]
            self.picker_state[token] = {
                "user_id": message.from_user.id if message.from_user else 0,
                "limit": limit,
                "focus": focus,
                "ts": int(time.time()),
            }

            buttons = []
            for item in recent:
                chat_id = int(item["chat_id"])
                # Trim title nicely
                title = str(item["title"])
                if len(title) > 25:
                    title = title[:22] + "..."
                
                buttons.append(
                    [
                        InlineKeyboardButton(
                            text=f"{title} | {chat_id}",
                            callback_data=f"sxp:{token}:{chat_id}",
                        )
                    ]
                )
            
            # Add Cancel button
            buttons.append(
                [
                    InlineKeyboardButton(
                        text="❌ Отмена",
                        callback_data=f"sxp:{token}:cancel"
                    )
                ]
            )

            await message.reply_text(
                f"Выбери чат (последние {limit} msg):",
                reply_markup=InlineKeyboardMarkup(buttons),
            )
            return

        # Если target указан явно
        try:
            target = await self.resolver.resolve(client, raw_target)
        except (UsernameInvalid, UsernameNotOccupied):
            await message.reply_text(f"❌ Чат `{raw_target}` не существует.")
            return
        except ValueError as exc:
            await message.reply_text(f"⚠️ {exc}")
            return
        except Exception as exc:
            await message.reply_text(f"❌ Target Error: {exc}")
            return

        await self._run_summary(
            client, message, target.chat_id, target.title, limit, focus
        )

    async def summary_picker_callback(self, client: Client, callback_query: CallbackQuery):
        """Callback для выбора target-чата из picker-а."""
        parts = (callback_query.data or "").split(":")
        if len(parts) != 3:
            await callback_query.answer("Invalid callback.", show_alert=True)
            return

        _, token, action = parts
        
        # Handle Cancel
        if action == "cancel":
            self.picker_state.pop(token, None)
            await callback_query.message.delete()
            return

        state = self.picker_state.get(token)
        if not state:
            await callback_query.answer("Picker устарел.", show_alert=True)
            return

        user_id = callback_query.from_user.id if callback_query.from_user else 0
        if user_id != state.get("user_id"):
            await callback_query.answer("Чужой picker!", show_alert=True)
            return

        try:
            chat_id = int(action)
        except ValueError:
            await callback_query.answer("Invalid chat_id.", show_alert=True)
            return

        # Clean up state once selected
        self.picker_state.pop(token, None)
        
        # Update original message to show selection
        await callback_query.message.edit_text(f"Выбран чат `{chat_id}`. Запуск...")

        # Run summary
        await self._run_summary(
            client=client,
            message=callback_query.message,
            target_chat_id=chat_id,
            target_title=str(chat_id), # We don't have title easily here, resolve via API inside _run if needed or pass from picker
            limit=int(state.get("limit", 120)),
            focus=str(state.get("focus", "")),
        )


def register_handlers(app, deps: dict):
    """Регистрирует Telegram-control команды и callback-обработчики."""
    handler = TelegramControlHandler(deps)

    safe = deps["safe_handler"]
 
    app.on_message(filters.command("chatid", prefixes="!"))(safe(handler.chatid_command))
    app.on_message(filters.command("summaryx", prefixes="!"))(safe(handler.summaryx_command))
    app.on_callback_query(filters.regex(r"^sxp:[a-f0-9]{8}:.+$"))(safe(handler.summary_picker_callback))
