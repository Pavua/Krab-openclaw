"""
TelegramSendUtilsMixin — Wave 31-D.

Статические error-helpers и Pyrogram send wrappers (_safe_edit, _safe_reply_or_send_new,
_extract_message_text, _is_command_like_text) extracted from userbot_bridge.py.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pyrogram.types import Message

from ..core.logger import get_logger
from ._send_queue import telegram_send_queue as _telegram_send_queue

if TYPE_CHECKING:
    pass

logger = get_logger(__name__)


class TelegramSendUtilsMixin:
    """Wave 31-D: static error helpers + safe Telegram send wrappers."""

    # ------------------------------------------------------------------
    # Static error classifiers
    # ------------------------------------------------------------------

    @staticmethod
    def _is_message_not_modified_error(exc: Exception) -> bool:
        """Определяет типичную ошибку Telegram при повторном edit того же текста."""
        text = str(exc).upper()
        return "MESSAGE_NOT_MODIFIED" in text

    @staticmethod
    def _is_message_id_invalid_error(exc: Exception) -> bool:
        """Определяет ошибку Telegram при попытке edit невалидного message id."""
        return "MESSAGE_ID_INVALID" in str(exc).upper()

    @staticmethod
    def _is_message_author_required_error(exc: Exception) -> bool:
        """Определяет 403 MESSAGE_AUTHOR_REQUIRED при попытке edit чужого сообщения."""
        return "MESSAGE_AUTHOR_REQUIRED" in str(exc).upper()

    @staticmethod
    def _is_message_empty_error(exc: Exception) -> bool:
        """Определяет ошибку Telegram при попытке отправить/отредактировать пустой текст."""
        return "MESSAGE_EMPTY" in str(exc).upper()

    @staticmethod
    def _is_message_too_long_error(exc: Exception) -> bool:
        """Определяет ошибку Telegram при превышении лимита длины сообщения (4096 chars)."""
        return "MESSAGE_TOO_LONG" in str(exc).upper()

    # ------------------------------------------------------------------
    # Text helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_message_text(message: Message | Any) -> str:
        """Возвращает текст или подпись сообщения единым способом."""
        return str(getattr(message, "text", None) or getattr(message, "caption", None) or "")

    @staticmethod
    def _is_command_like_text(text: str) -> bool:
        """Определяет служебные команды, которые нельзя склеивать с обычным текстом."""
        normalized = str(text or "").lstrip()
        return normalized[:1] in {"!", "/", "."}

    # ------------------------------------------------------------------
    # Pyrogram send wrappers
    # ------------------------------------------------------------------

    async def _safe_edit(self, msg: Message, text: str) -> Message:
        """
        Безопасно редактирует сообщение через _telegram_send_queue (с retry).
        Возвращает актуальный Message:
        - исходный, если edit не потребовался;
        - результат edit;
        - новый message при fallback на send_message.
        """
        current_text = (getattr(msg, "text", None) or getattr(msg, "caption", None) or "").strip()
        target_text = (text or "").strip()
        # Telegram EditMessage не принимает пустой/невидимый текст.
        if not target_text:
            target_text = "…"
        if current_text == target_text:
            return msg
        chat_id: int = msg.chat.id
        _text = target_text  # захват для lambda
        try:
            edited = await _telegram_send_queue.run(chat_id, lambda: msg.edit(_text))
            return edited or msg
        except Exception as exc:  # noqa: BLE001 - фильтруем MESSAGE_NOT_MODIFIED
            if self._is_message_not_modified_error(exc):
                return msg
            if self._is_message_id_invalid_error(exc) or self._is_message_empty_error(exc):
                logger.warning("telegram_edit_fallback_send_new", error=str(exc))
                return await _telegram_send_queue.run(
                    chat_id,
                    lambda: self.client.send_message(chat_id, _text),  # type: ignore[attr-defined]
                )
            if self._is_message_author_required_error(exc):
                # Bug 4 defense-in-depth: на случай, если guard в _deliver_response_parts
                # был обойдён — отправляем как reply на исходное сообщение.
                logger.warning(
                    "telegram_edit_author_required_fallback_reply",
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
                msg_id = getattr(msg, "id", None)
                if msg_id:
                    return await _telegram_send_queue.run(
                        chat_id,
                        lambda: self.client.send_message(  # type: ignore[attr-defined]
                            chat_id, _text, reply_to_message_id=msg_id
                        ),
                    )
                return await _telegram_send_queue.run(
                    chat_id,
                    lambda: self.client.send_message(chat_id, _text),  # type: ignore[attr-defined]
                )
            if self._is_message_too_long_error(exc):
                # Текст превысил лимит Telegram (4096). Отрезаем и отправляем новым сообщением.
                logger.warning("telegram_edit_too_long_fallback_send_new", error=str(exc))
                _truncated = _text[:4000]
                return await _telegram_send_queue.run(
                    chat_id,
                    lambda: self.client.send_message(chat_id, _truncated),  # type: ignore[attr-defined]
                )
            raise

    async def _safe_reply_or_send_new(self, msg: Message, text: str) -> Message:
        """
        Безопасно отвечает на сообщение через reply с fallback на send_message.

        Это защищает private owner-path от silent-drop, когда Telegram принимает
        обычную отправку в чат, но валит именно reply на конкретный message id.
        Оба вызова идут через _telegram_send_queue (с retry при FLOOD_WAIT/timeout).
        """
        target_text = (text or "").strip() or "…"
        chat_id: int = msg.chat.id
        _text = target_text  # захват для lambda
        try:
            sent = await _telegram_send_queue.run(chat_id, lambda: msg.reply(_text))
            # Фиксируем момент ответа Краба для follow-up детектора
            from ..core.trigger_detector import last_krab_msg  # noqa: PLC0415

            last_krab_msg.record(chat_id)
            return sent or msg
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "telegram_reply_fallback_send_new",
                chat_id=str(chat_id),
                message_id=str(getattr(msg, "id", "") or ""),
                error=str(exc),
            )
            sent = await _telegram_send_queue.run(
                chat_id,
                lambda: self.client.send_message(chat_id, _text),  # type: ignore[attr-defined]
            )
            from ..core.trigger_detector import last_krab_msg  # noqa: PLC0415

            last_krab_msg.record(chat_id)
            return sent
