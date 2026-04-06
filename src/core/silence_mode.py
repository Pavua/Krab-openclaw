# -*- coding: utf-8 -*-
"""
SilenceManager — управление режимом тишины Краба.

Per-chat и глобальный mute. In-memory state (не переживает рестарт).
Owner автоматически активирует тишину когда сам отвечает в чате.
"""

from __future__ import annotations

import time
from typing import Any

from .logger import get_logger

logger = get_logger(__name__)

# Дефолты (могут переопределяться через config)
_DEFAULT_CHAT_MUTE_MINUTES = 30
_DEFAULT_GLOBAL_MUTE_MINUTES = 60
_DEFAULT_OWNER_AUTO_SILENCE_MINUTES = 5


class SilenceManager:
    """Per-chat и глобальный mute для userbot."""

    def __init__(self) -> None:
        self._chat_mutes: dict[str, float] = {}  # chat_id → expiry monotonic ts
        self._global_until: float | None = None

    # ── Per-chat ──────────────────────────────────────────────

    def mute_chat(self, chat_id: str, minutes: int = _DEFAULT_CHAT_MUTE_MINUTES) -> float:
        """Заглушить чат на N минут. Возвращает expiry timestamp."""
        expiry = time.monotonic() + minutes * 60
        self._chat_mutes[chat_id] = expiry
        logger.info("silence_chat_muted", chat_id=chat_id, minutes=minutes)
        return expiry

    def unmute_chat(self, chat_id: str) -> bool:
        """Снять mute с чата. Возвращает True если mute был активен."""
        was_muted = chat_id in self._chat_mutes
        self._chat_mutes.pop(chat_id, None)
        if was_muted:
            logger.info("silence_chat_unmuted", chat_id=chat_id)
        return was_muted

    def is_chat_muted(self, chat_id: str) -> bool:
        """Проверяет активен ли mute для конкретного чата (без учёта глобального)."""
        expiry = self._chat_mutes.get(chat_id)
        if expiry is None:
            return False
        if time.monotonic() >= expiry:
            self._chat_mutes.pop(chat_id, None)
            return False
        return True

    def chat_mute_remaining_sec(self, chat_id: str) -> float:
        """Оставшееся время mute чата в секундах (0 если не muted)."""
        expiry = self._chat_mutes.get(chat_id)
        if expiry is None:
            return 0.0
        remaining = expiry - time.monotonic()
        if remaining <= 0:
            self._chat_mutes.pop(chat_id, None)
            return 0.0
        return remaining

    # ── Global ────────────────────────────────────────────────

    def mute_global(self, minutes: int = _DEFAULT_GLOBAL_MUTE_MINUTES) -> float:
        """Глобальный mute на N минут."""
        self._global_until = time.monotonic() + minutes * 60
        logger.info("silence_global_muted", minutes=minutes)
        return self._global_until

    def unmute_global(self) -> bool:
        """Снять глобальный mute."""
        was_muted = self._global_until is not None and time.monotonic() < self._global_until
        self._global_until = None
        if was_muted:
            logger.info("silence_global_unmuted")
        return was_muted

    def is_global_muted(self) -> bool:
        if self._global_until is None:
            return False
        if time.monotonic() >= self._global_until:
            self._global_until = None
            return False
        return True

    def global_mute_remaining_sec(self) -> float:
        if self._global_until is None:
            return 0.0
        remaining = self._global_until - time.monotonic()
        if remaining <= 0:
            self._global_until = None
            return 0.0
        return remaining

    # ── Composite ─────────────────────────────────────────────

    def is_silenced(self, chat_id: str) -> bool:
        """Проверяет тишину: per-chat ИЛИ глобальный."""
        return self.is_chat_muted(chat_id) or self.is_global_muted()

    def auto_silence_owner_typing(
        self,
        chat_id: str,
        minutes: int = _DEFAULT_OWNER_AUTO_SILENCE_MINUTES,
    ) -> None:
        """Авто-тишина: owner пишет в чат → Краб молчит N минут."""
        # Не перезаписываем более длинный ручной mute
        current_remaining = self.chat_mute_remaining_sec(chat_id)
        auto_sec = minutes * 60
        if current_remaining >= auto_sec:
            return
        self._chat_mutes[chat_id] = time.monotonic() + auto_sec
        logger.info(
            "silence_auto_owner_typing",
            chat_id=chat_id,
            minutes=minutes,
        )

    # ── Status ────────────────────────────────────────────────

    def status(self) -> dict[str, Any]:
        """Текущее состояние для !тишина статус."""
        now = time.monotonic()
        # Чистим expired
        expired_chats = [cid for cid, exp in self._chat_mutes.items() if now >= exp]
        for cid in expired_chats:
            self._chat_mutes.pop(cid, None)

        active_chats: dict[str, float] = {}
        for cid, exp in self._chat_mutes.items():
            active_chats[cid] = round((exp - now) / 60, 1)

        global_remaining = self.global_mute_remaining_sec()
        return {
            "global_muted": self.is_global_muted(),
            "global_remaining_min": round(global_remaining / 60, 1) if global_remaining > 0 else 0,
            "muted_chats": active_chats,  # {chat_id: remaining_minutes}
            "total_muted": len(active_chats) + (1 if self.is_global_muted() else 0),
        }

    def format_status(self) -> str:
        """Форматированный статус для Telegram."""
        st = self.status()
        lines = ["🤫 **Режим тишины**\n"]
        if st["global_muted"]:
            lines.append(f"🌐 Глобально: **{st['global_remaining_min']} мин** осталось")
        else:
            lines.append("🌐 Глобально: выключено")
        if st["muted_chats"]:
            lines.append(f"\n💬 Заглушённые чаты ({len(st['muted_chats'])}):")
            for cid, remaining in st["muted_chats"].items():
                lines.append(f"  `{cid}` — {remaining} мин")
        else:
            lines.append("💬 Заглушённых чатов нет")
        return "\n".join(lines)


# Singleton
silence_manager = SilenceManager()
