"""
ReactionDispatchMixin — Wave 31-D.

Отправка реакций и обработка reaction-updated событий extracted from userbot_bridge.py.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pyrogram.types import Message

from ..config import config
from ..core.logger import get_logger

if TYPE_CHECKING:
    pass

logger = get_logger(__name__)


class ReactionDispatchMixin:
    """Wave 31-D: reaction send/handle + monitor alert extracted from bridge."""

    async def _send_message_reaction(self, message: Message, emoji: str) -> None:
        """
        Ставит реакцию на сообщение через pyrofork send_reaction.

        Молча игнорирует ошибки — не все чаты/типы сообщений поддерживают реакции
        (каналы без реакций, анонимные группы, старые клиенты и т.д.).
        Не ставит реакцию если TELEGRAM_REACTIONS_ENABLED=False.
        """
        if not bool(getattr(config, "TELEGRAM_REACTIONS_ENABLED", True)):
            return
        chat_id_int = int(getattr(getattr(message, "chat", None), "id", 0) or 0)
        message_id_int = int(getattr(message, "id", 0) or 0)
        if not chat_id_int or not message_id_int:
            return
        try:
            await self.client.send_reaction(  # type: ignore[attr-defined]
                chat_id=chat_id_int,
                message_id=message_id_int,
                emoji=emoji,
            )
        except Exception:  # noqa: BLE001
            pass  # реакции — best-effort, не прерываем основной flow

    async def _handle_message_reaction_updated(self, reaction_update: Any) -> None:
        """
        Обрабатывает обновление реакции пользователя на сообщение.

        Логирует реакции как feedback и передаёт в ReactionEngine для накопления статистики.
        Полезно: 👍/❤️ = пользователь доволен ответом, 👎 = недоволен.
        """
        try:
            # Извлекаем поля из MessageReactionUpdated
            chat = getattr(reaction_update, "chat", None)
            from_user = getattr(reaction_update, "from_user", None)
            message_id = int(getattr(reaction_update, "id", 0) or 0)
            chat_id = int(getattr(chat, "id", 0) or 0) if chat else 0
            user_id = int(getattr(from_user, "id", 0) or 0) if from_user else None

            if not chat_id or not message_id:
                return

            # Список Reaction объектов
            new_reactions = list(getattr(reaction_update, "new_reaction", None) or [])
            old_reactions = list(getattr(reaction_update, "old_reaction", None) or [])

            def _extract_emojis(reactions: list) -> list[str]:
                """Извлекает emoji-строки из объектов Reaction."""
                result = []
                for r in reactions:
                    emoji = getattr(r, "emoji", None) or getattr(r, "emoticon", None)
                    if emoji:
                        result.append(str(emoji))
                return result

            new_emojis = _extract_emojis(new_reactions)
            old_emojis = _extract_emojis(old_reactions)

            # Добавленные реакции (не было в old, появились в new)
            added = [e for e in new_emojis if e not in old_emojis]
            removed = [e for e in old_emojis if e not in new_emojis]

            if not added and not removed:
                return

            logger.info(
                "reaction_updated",
                chat_id=chat_id,
                message_id=message_id,
                user_id=user_id,
                added=added,
                removed=removed,
            )

            # Передаём в ReactionEngine для накопления feedback
            try:
                from ..core.reaction_engine import reaction_engine  # noqa: PLC0415

                reaction_engine.record_reaction(
                    chat_id=chat_id,
                    message_id=message_id,
                    user_id=user_id,
                    new_emojis=new_emojis,
                    old_emojis=old_emojis,
                )
            except Exception as eng_exc:  # noqa: BLE001
                logger.warning("reaction_engine_record_failed", error=str(eng_exc))

        except Exception:  # noqa: BLE001
            logger.exception("handle_message_reaction_updated_error")

    async def _send_monitor_alert(self, message: Message, matched_keyword: str) -> None:
        """Отправляет alert owner'у в Saved Messages при совпадении keyword в мониторимом чате."""
        try:
            if not self.me:  # type: ignore[attr-defined]
                return
            # Информация об отправителе
            sender = message.from_user
            sender_name = (
                (
                    getattr(sender, "username", None)
                    or getattr(sender, "first_name", None)
                    or str(getattr(sender, "id", "?"))
                )
                if sender
                else "Unknown"
            )
            # Название чата
            chat_title = (
                getattr(message.chat, "title", None)
                or getattr(message.chat, "first_name", None)
                or str(message.chat.id)
            )
            # Текст сообщения (обрезаем длинные)
            msg_text = (message.text or "").strip()
            if len(msg_text) > 800:
                msg_text = msg_text[:797] + "..."
            alert = (
                f"\U0001f514 **Monitor Alert**\n"
                f"Chat: {chat_title} (`{message.chat.id}`)\n"
                f"From: @{sender_name}\n"
                f"Keyword: `{matched_keyword}`\n"
                f"─────\n"
                f"{msg_text}"
            )
            await self.client.send_message(self._owner_notify_target, alert)  # type: ignore[attr-defined]
            logger.info(
                "monitor_alert_sent",
                chat_id=str(message.chat.id),
                keyword=matched_keyword,
                sender=sender_name,
            )
        except Exception as exc:
            logger.warning("monitor_alert_error", error=str(exc))
