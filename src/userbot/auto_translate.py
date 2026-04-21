# -*- coding: utf-8 -*-
"""
auto_translate.py — mixin для per-chat автоперевода входящих сообщений.

Реализует toggle-список чатов с включённым автопереводом (`!translate auto`).
Список персистируется в ~/.openclaw/krab_runtime_state/auto_translate_chats.json.

Автоперевод — независим от translator-сессии (голосовой переводчик).
Направление: auto_detect_direction из language_detect (ru→en, en→ru, es→ru).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


class AutoTranslateMixin:
    """Mixin: per-chat автоперевод входящих текстовых сообщений."""

    # Кэш в памяти — set[str] chat_id
    _auto_translate_chats: set[str]

    def _auto_translate_state_path(self) -> Path:
        """Путь к файлу персистентного состояния автоперевода."""
        runtime_dir = Path(
            os.environ.get("KRAB_RUNTIME_STATE_DIR")
            or str(Path.home() / ".openclaw" / "krab_runtime_state")
        ).expanduser()
        return runtime_dir / "auto_translate_chats.json"

    def _load_auto_translate_chats(self) -> set[str]:
        """Загружает список чатов из файла (при старте)."""
        path = self._auto_translate_state_path()
        try:
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    return {str(c).strip() for c in data if str(c).strip()}
        except Exception:
            pass
        return set()

    def _save_auto_translate_chats(self) -> None:
        """Сохраняет список чатов на диск."""
        path = self._auto_translate_state_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(sorted(self._auto_translate_chats), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass  # деградируем тихо — in-memory состояние сохраняется

    def _ensure_auto_translate_chats_loaded(self) -> None:
        """Ленивая инициализация множества (вызывается при первом обращении)."""
        if not hasattr(self, "_auto_translate_chats"):
            self._auto_translate_chats = self._load_auto_translate_chats()

    def is_auto_translate_enabled(self, chat_id: Any) -> bool:
        """Проверяет, включён ли автоперевод для данного чата."""
        self._ensure_auto_translate_chats_loaded()
        return str(chat_id).strip() in self._auto_translate_chats

    def add_auto_translate_chat(self, chat_id: Any) -> None:
        """Включает автоперевод для чата (идемпотентно)."""
        self._ensure_auto_translate_chats_loaded()
        self._auto_translate_chats.add(str(chat_id).strip())
        self._save_auto_translate_chats()

    def remove_auto_translate_chat(self, chat_id: Any) -> None:
        """Выключает автоперевод для чата (идемпотентно)."""
        self._ensure_auto_translate_chats_loaded()
        self._auto_translate_chats.discard(str(chat_id).strip())
        self._save_auto_translate_chats()

    def get_auto_translate_chats(self) -> list[str]:
        """Возвращает копию списка чатов с автопереводом."""
        self._ensure_auto_translate_chats_loaded()
        return sorted(self._auto_translate_chats)

    async def _handle_auto_translate_message(
        self,
        message: Any,
        text: str,
        chat_id: str,
    ) -> bool:
        """
        Переводит входящее сообщение если автоперевод включён в чате.

        Возвращает True если перевод выполнен (сообщение обработано),
        False — если автоперевод не активен или перевод не нужен.
        """
        if not self.is_auto_translate_enabled(chat_id):
            return False
        if not text or len(text.strip()) < 3:
            return False

        try:
            from ..core.language_detect import auto_detect_direction, detect_language
            from ..core.translator_engine import translate_text
            from ..openclaw_client import openclaw_client as _oc

            detected = detect_language(text)
            if not detected:
                return False  # не удалось определить язык — пропускаем

            src_lang, tgt_lang = auto_detect_direction(detected)
            if src_lang == tgt_lang:
                return False  # переводить нечего

            result = await translate_text(
                text,
                src_lang,
                tgt_lang,
                openclaw_client=_oc,
                chat_id=f"auto_translate_{chat_id}",
            )
            if not result.translated:
                return False

            reply_text = f"🔄 {src_lang}→{tgt_lang} ({result.latency_ms}ms)\n_{result.translated}_"
            await self._safe_reply_or_send_new(message, reply_text)
            return True

        except Exception:
            # Тихая деградация — не ломаем обработку входящих
            return False
