"""
Per-chat filter configuration.

Chado blueprint: каждый чат может быть в одном из трёх режимов:
  - "active"       — Краб отвечает на все сообщения
  - "mention-only" — Краб отвечает только когда упомянут / reply
  - "muted"        — Краб игнорирует все не-командные сообщения

Хранение: in-memory dict (singleton). Режим по умолчанию зависит от
типа чата — DM всегда "active", group — "mention-only" (safe default).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from .logger import get_logger

logger = get_logger(__name__)

_VALID_MODES = ("active", "mention-only", "muted")
_DEFAULT_DM_MODE = "active"
_DEFAULT_GROUP_MODE = os.environ.get("KRAB_GROUP_DEFAULT_FILTER_MODE", "mention-only")


class ChatFilterConfig:
    """Per-chat filter mode storage."""

    def __init__(self) -> None:
        self._modes: dict[str, str] = {}  # chat_id → mode
        self._persist_path: Optional[Path] = None

    def configure_persist_path(self, path: Path) -> None:
        """Настроить путь для персистентного хранения конфига."""
        self._persist_path = path
        self._load()

    def _load(self) -> None:
        if self._persist_path and self._persist_path.exists():
            try:
                data = json.loads(self._persist_path.read_text())
                if isinstance(data, dict):
                    self._modes = {
                        str(k): v
                        for k, v in data.items()
                        if v in _VALID_MODES
                    }
                    logger.debug(
                        "chat_filter_config_loaded",
                        count=len(self._modes),
                        path=str(self._persist_path),
                    )
            except Exception as exc:  # noqa: BLE001
                logger.warning("chat_filter_config_load_failed", error=str(exc))

    def _save(self) -> None:
        if self._persist_path:
            try:
                self._persist_path.parent.mkdir(parents=True, exist_ok=True)
                self._persist_path.write_text(json.dumps(self._modes, ensure_ascii=False, indent=2))
            except Exception as exc:  # noqa: BLE001
                logger.warning("chat_filter_config_save_failed", error=str(exc))

    def get_mode(
        self,
        chat_id: str,
        *,
        default_if_group: str = _DEFAULT_GROUP_MODE,
        default_if_dm: str = _DEFAULT_DM_MODE,
        is_group: Optional[bool] = None,
    ) -> str:
        """
        Вернуть режим фильтра для чата.

        Args:
            chat_id: идентификатор чата (строка)
            default_if_group: дефолт для групп (mention-only)
            default_if_dm: дефолт для DM (active)
            is_group: если True — это группа, False — DM, None — неизвестно (используем default_if_group)
        """
        chat_id = str(chat_id)
        if chat_id in self._modes:
            return self._modes[chat_id]
        if is_group is False:
            return default_if_dm
        return default_if_group

    def set_mode(self, chat_id: str, mode: str) -> None:
        """Установить режим фильтра для чата."""
        if mode not in _VALID_MODES:
            raise ValueError(f"invalid mode {mode!r}, expected one of {_VALID_MODES}")
        chat_id = str(chat_id)
        old = self._modes.get(chat_id)
        self._modes[chat_id] = mode
        logger.info("chat_filter_mode_set", chat_id=chat_id, mode=mode, prev=old)
        self._save()

    def reset(self, chat_id: str) -> None:
        """Сбросить режим фильтра к умолчанию."""
        chat_id = str(chat_id)
        self._modes.pop(chat_id, None)
        logger.info("chat_filter_mode_reset", chat_id=chat_id)
        self._save()

    def all_modes(self) -> dict[str, str]:
        """Вернуть копию всего конфига."""
        return dict(self._modes)

    def stats(self) -> dict:
        from collections import Counter
        counts = Counter(self._modes.values())
        return {
            "total_configured": len(self._modes),
            "by_mode": dict(counts),
        }


# Singleton
chat_filter_config = ChatFilterConfig()
