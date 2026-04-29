# -*- coding: utf-8 -*-
"""
trusted_guests.py — per-chat allowlist для доверенных гостей.

Поведение по умолчанию (W10.1): non-owner в группе → forward-only, LLM skip.
С этим модулем: если user_id IN trusted_guests[chat_id] → пропускать к LLM в любом случае.

Config: ~/.openclaw/krab_runtime_state/trusted_guests.json
Shape:
  {
    "chat_id_str": {
      "user_ids": [int, ...],
      "usernames": ["@handle", ...]   # fallback если user_id неизвестен
    }
  }

Public API:
  is_trusted(chat_id, user_id, username) -> bool
  add_trusted(chat_id, user_id, username)  -> None
  remove_trusted(chat_id, user_id)         -> None
  list_trusted(chat_id)                    -> list[dict]
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

_DEFAULT_STATE_DIR = Path.home() / ".openclaw" / "krab_runtime_state"
_FILENAME = "trusted_guests.json"

# Defaults: @dodik_ggt разрешена в обеих основных группах.
# user_id резолвится при первом !trust add — пока держим только username.
_DEFAULT_CONFIG: dict[str, Any] = {
    "-1001804661353": {  # YMB FAMILY FOREVER
        "user_ids": [],
        "usernames": ["@dodik_ggt"],
    },
    "-1001587432709": {  # How2AI
        "user_ids": [],
        "usernames": ["@dodik_ggt"],
    },
}


class TrustedGuestsStore:
    """
    Потокобезопасное хранилище trusted-allowlist с persist в JSON.

    Матчинг:
      1. user_id в user_ids (точно)
      2. username (case-insensitive, без @) в usernames (fallback когда user_id=0)
    """

    def __init__(self, state_dir: Path | None = None) -> None:
        self._path = (state_dir or _DEFAULT_STATE_DIR) / _FILENAME
        self._lock = threading.Lock()
        self._data: dict[str, Any] = {}
        self._load()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self) -> None:
        """Загружает JSON; если файл отсутствует — инициализирует дефолтами."""
        try:
            if self._path.exists():
                raw = self._path.read_text(encoding="utf-8")
                self._data = json.loads(raw)
                logger.debug("trusted_guests_loaded", path=str(self._path), chats=len(self._data))
            else:
                self._data = dict(_DEFAULT_CONFIG)
                self._save_locked()
                logger.info(
                    "trusted_guests_initialized_defaults",
                    path=str(self._path),
                    chats=len(self._data),
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("trusted_guests_load_failed", error=str(exc))
            self._data = dict(_DEFAULT_CONFIG)

    def _save_locked(self) -> None:
        """Сохраняет (вызывать внутри _lock)."""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(self._data, ensure_ascii=False, indent=2))
        except Exception as exc:  # noqa: BLE001
            logger.warning("trusted_guests_save_failed", error=str(exc))

    def _save(self) -> None:
        with self._lock:
            self._save_locked()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_username(username: str | None) -> str:
        """Нормализует username: убирает @, lower."""
        if not username:
            return ""
        return str(username).lstrip("@").strip().lower()

    @staticmethod
    def _chat_key(chat_id: int) -> str:
        return str(chat_id)

    def _get_entry(self, chat_key: str) -> dict[str, Any]:
        """Возвращает mutable запись чата (создаёт если нет)."""
        if chat_key not in self._data:
            self._data[chat_key] = {"user_ids": [], "usernames": []}
        entry = self._data[chat_key]
        if "user_ids" not in entry:
            entry["user_ids"] = []
        if "usernames" not in entry:
            entry["usernames"] = []
        return entry

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_trusted(self, chat_id: int, user_id: int, username: str | None = None) -> bool:
        """
        Проверяет, является ли пользователь доверенным в данном чате.

        - Сначала проверяется user_id (точное совпадение, user_id > 0).
        - Затем username (case-insensitive, без @).
        """
        chat_key = self._chat_key(chat_id)
        with self._lock:
            entry = self._data.get(chat_key)
        if not entry:
            return False

        # Матч по user_id
        if user_id and user_id > 0:
            if user_id in (entry.get("user_ids") or []):
                return True

        # Матч по username (fallback)
        norm = self._normalize_username(username)
        if norm:
            stored = [self._normalize_username(u) for u in (entry.get("usernames") or [])]
            if norm in stored:
                return True

        return False

    def add_trusted(
        self,
        chat_id: int,
        user_id: int,
        username: str | None = None,
    ) -> None:
        """
        Добавляет пользователя в trusted list чата.

        user_id=0 допустим — тогда только по username.
        """
        chat_key = self._chat_key(chat_id)
        with self._lock:
            entry = self._get_entry(chat_key)

            if user_id and user_id > 0 and user_id not in entry["user_ids"]:
                entry["user_ids"].append(user_id)

            norm_uname = self._normalize_username(username)
            if norm_uname:
                # Храним с @
                canonical = f"@{norm_uname}"
                normalized_stored = [self._normalize_username(u) for u in entry["usernames"]]
                if norm_uname not in normalized_stored:
                    entry["usernames"].append(canonical)

            self._save_locked()

        logger.info(
            "trusted_guest_added",
            chat_id=chat_id,
            user_id=user_id,
            username=username,
        )

    def remove_trusted(self, chat_id: int, user_id: int, username: str | None = None) -> None:
        """
        Удаляет пользователя из trusted list.

        Удаляет по user_id И по username (если передан).
        """
        chat_key = self._chat_key(chat_id)
        with self._lock:
            entry = self._data.get(chat_key)
            if not entry:
                return

            if user_id and user_id > 0 and user_id in entry.get("user_ids", []):
                entry["user_ids"].remove(user_id)

            norm = self._normalize_username(username)
            if norm:
                entry["usernames"] = [
                    u for u in entry.get("usernames", []) if self._normalize_username(u) != norm
                ]

            self._save_locked()

        logger.info(
            "trusted_guest_removed",
            chat_id=chat_id,
            user_id=user_id,
            username=username,
        )

    def list_trusted(self, chat_id: int) -> list[dict[str, Any]]:
        """
        Возвращает список доверенных пользователей для чата.

        Каждый элемент: {"user_id": int | None, "username": str | None}
        """
        chat_key = self._chat_key(chat_id)
        with self._lock:
            entry = self._data.get(chat_key)
        if not entry:
            return []

        result: list[dict[str, Any]] = []
        seen_ids: set[int] = set()

        for uid in entry.get("user_ids", []):
            seen_ids.add(uid)
            result.append({"user_id": uid, "username": None})

        # Добавляем username-only записи
        uid_set_from_usernames: set[str] = set()
        for uname in entry.get("usernames", []):
            norm = self._normalize_username(uname)
            if norm not in uid_set_from_usernames:
                uid_set_from_usernames.add(norm)
                result.append({"user_id": None, "username": f"@{norm}"})

        return result

    def all_chats(self) -> dict[str, Any]:
        """Возвращает копию всего состояния (для !trust list --all или API)."""
        with self._lock:
            return dict(self._data)


# Синглтон
trusted_guests = TrustedGuestsStore()

__all__ = [
    "TrustedGuestsStore",
    "trusted_guests",
]
