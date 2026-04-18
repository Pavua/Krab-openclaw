# -*- coding: utf-8 -*-
"""
ChatFilterConfig — per-chat режим реакции Краба (Chado Wave 16).

Режимы:
  active        — отвечать на всё
  mention-only  — только при упоминании / командах
  muted         — не отвечать ни на что
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_VALID_MODES = frozenset({"active", "mention-only", "muted"})
_DEFAULT_PRIVATE_MODE = "active"
_DEFAULT_GROUP_MODE = "mention-only"


class ChatFilterConfig:
    """Персистентный конфиг фильтрации ответов по чатам."""

    def __init__(self, state_path: Path | None = None) -> None:
        self._path = Path(state_path) if state_path else None
        self._modes: dict[str, str] = {}
        if self._path and self._path.exists():
            self._load()

    # ── API ──────────────────────────────────────────────────────────────

    def get_mode(self, chat_id: str, *, default_if_group: str = _DEFAULT_GROUP_MODE) -> str:
        """Вернуть режим для чата (дефолт зависит от типа)."""
        return self._modes.get(str(chat_id), default_if_group)

    def set_mode(self, chat_id: str, mode: str) -> None:
        """Установить режим для чата. ValueError если mode невалиден."""
        if mode not in _VALID_MODES:
            raise ValueError(f"Invalid mode {mode!r}. Valid: {sorted(_VALID_MODES)}")
        self._modes[str(chat_id)] = mode
        self._persist()

    def should_respond(
        self,
        chat_id: str,
        *,
        has_mention: bool = False,
        is_dm: bool = False,
        default_if_group: str = _DEFAULT_GROUP_MODE,
    ) -> bool:
        """True → Краб должен ответить в этом чате на это сообщение."""
        mode = self.get_mode(str(chat_id), default_if_group=default_if_group)
        if mode == "muted":
            return False
        if mode == "active" or is_dm:
            return True
        # mention-only
        return has_mention

    # ── Persistence ──────────────────────────────────────────────────────

    def _load(self) -> None:
        try:
            raw: Any = json.loads(self._path.read_text(encoding="utf-8"))  # type: ignore[union-attr]
            if isinstance(raw, dict):
                self._modes = {k: v for k, v in raw.items() if v in _VALID_MODES}
        except (OSError, json.JSONDecodeError):
            pass

    def _persist(self) -> None:
        if self._path is None:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps(self._modes, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError:
            pass
