# -*- coding: utf-8 -*-
"""
Per-chat filter config — управляет behavior Krab в каждом чате.

Modes:
- "active" (default для DM): реагирует на все сообщения (как сейчас)
- "mention-only": реагирует только на @mention / "Краб" / reply
- "muted": игнорирует все сообщения (полная тишина)

Config: ~/.openclaw/krab_runtime_state/chat_filters.json
{
  "-1001234567890": {"mode": "mention-only", "updated_at": 1234567890},
  "-1009876543210": {"mode": "muted", "updated_at": 1234567800}
}

Default (absent from config):
- DM / personal chat → "active"
- Group / supergroup → "mention-only" (safe default: не спамить)

Hot-reload: при каждом get_mode проверяется mtime файла;
если изменился — правила перезагружаются без рестарта.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from structlog import get_logger

logger = get_logger(__name__)

STATE_PATH = Path("~/.openclaw/krab_runtime_state/chat_filters.json").expanduser()
VALID_MODES = {"active", "mention-only", "muted"}

# Дефолт для групп (DM всегда "active")
DEFAULT_GROUP_MODE = "mention-only"
DEFAULT_DM_MODE = "active"


@dataclass
class ChatFilterRule:
    chat_id: str
    mode: str = "active"
    updated_at: float = field(default_factory=time.time)
    note: str = ""


class ChatFilterConfig:
    def __init__(self, state_path: Path = STATE_PATH):
        self._path = state_path
        self._rules: dict[str, ChatFilterRule] = {}
        self._last_mtime: float = 0.0
        self._load()

    def _load(self) -> None:
        """Загрузить правила из JSON."""
        if not self._path.exists():
            self._last_mtime = 0.0
            return
        try:
            self._last_mtime = self._path.stat().st_mtime
            data = json.loads(self._path.read_text())
            for chat_id, cfg in data.items():
                self._rules[str(chat_id)] = ChatFilterRule(
                    chat_id=str(chat_id),
                    mode=cfg.get("mode", "active"),
                    updated_at=cfg.get("updated_at", time.time()),
                    note=cfg.get("note", ""),
                )
        except Exception as e:  # noqa: BLE001
            logger.warning("chat_filter_load_failed", error=str(e))

    def _maybe_reload(self) -> None:
        """Проверить mtime файла; перезагрузить если изменён внешне.

        Используем строгое неравенство mtime != _last_mtime — _save() всегда
        обновляет _last_mtime до точного mtime записанного файла, поэтому
        дополнительный буфер +0.05 не нужен и вызывал timing flakiness.
        """
        if not self._path.exists():
            return
        try:
            current_mtime = self._path.stat().st_mtime
            if current_mtime != self._last_mtime:
                logger.info("chat_filter_hot_reload", old_mtime=self._last_mtime, new_mtime=current_mtime)
                self._rules.clear()
                self._load()
        except Exception as e:  # noqa: BLE001
            logger.warning("hot_reload_check_failed", error=str(e))

    def reload(self) -> bool:
        """Принудительная перезагрузка с диска.

        Returns:
            True если правила изменились после перезагрузки.
        """
        old_hash = hash(tuple(sorted((k, v.mode) for k, v in self._rules.items())))
        old_count = len(self._rules)
        self._rules.clear()
        self._load()
        new_hash = hash(tuple(sorted((k, v.mode) for k, v in self._rules.items())))
        new_count = len(self._rules)
        return old_hash != new_hash or old_count != new_count

    def _save(self) -> None:
        """Сохранить правила в JSON."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            r.chat_id: {"mode": r.mode, "updated_at": r.updated_at, "note": r.note}
            for r in self._rules.values()
        }
        try:
            self._path.write_text(json.dumps(data, indent=2))
            # Обновить mtime после записи
            self._last_mtime = self._path.stat().st_mtime
        except Exception as e:  # noqa: BLE001
            logger.warning("chat_filter_save_failed", error=str(e))

    def get_mode(self, chat_id: str | int, *, is_group: bool = True, default_if_group: str | None = None) -> str:
        """Получить mode для чата.

        Args:
            chat_id: ID чата.
            is_group: True для group/supergroup, False для DM (личный чат).
            default_if_group: Алиас дефолтного режима для group-чатов (compat).

        Returns:
            Текущий mode ("active", "mention-only" или "muted").
        """
        self._maybe_reload()
        rule = self._rules.get(str(chat_id))
        if rule:
            return rule.mode
        # Если явно передан default для группы — используем его
        if is_group:
            return default_if_group if default_if_group is not None else DEFAULT_GROUP_MODE
        return DEFAULT_DM_MODE

    def set_mode(self, chat_id: str | int, mode: str, note: str = "") -> bool:
        """Установить mode для чата.

        Raises:
            ValueError: если mode не входит в VALID_MODES.
        """
        if mode not in VALID_MODES:
            raise ValueError(f"Invalid mode: {mode!r}. Valid: {sorted(VALID_MODES)}")
        cid = str(chat_id)
        self._rules[cid] = ChatFilterRule(
            chat_id=cid, mode=mode, updated_at=time.time(), note=note
        )
        self._save()
        logger.info("chat_filter_set", chat_id=cid, mode=mode)
        return True

    def reset(self, chat_id: str | int) -> bool:
        """Удалить явное правило — вернуть к дефолту.

        Returns:
            True если правило было удалено, False если его не было.
        """
        cid = str(chat_id)
        if cid in self._rules:
            del self._rules[cid]
            self._save()
            logger.info("chat_filter_reset", chat_id=cid)
            return True
        return False

    def list_rules(self, mode: Optional[str] = None) -> list[ChatFilterRule]:
        """Список всех правил, опционально отфильтрованных по mode."""
        rules = list(self._rules.values())
        if mode:
            rules = [r for r in rules if r.mode == mode]
        return sorted(rules, key=lambda r: -r.updated_at)

    def stats(self) -> dict:
        """Статистика по правилам."""
        total = len(self._rules)
        by_mode: dict[str, int] = {}
        for r in self._rules.values():
            by_mode[r.mode] = by_mode.get(r.mode, 0) + 1
        return {"total_rules": total, "by_mode": by_mode}

    def should_respond(
        self,
        chat_id: str | int,
        *,
        is_group: bool = True,
        is_mention: bool = False,
        has_mention: bool | None = None,
        is_reply: bool = False,
        is_dm: bool = False,
    ) -> bool:
        """Проверить, должен ли Краб реагировать на сообщение в чате.

        Args:
            chat_id: ID чата.
            is_group: True для group/supergroup.
            is_mention: True если сообщение содержит @mention или "Краб".
            has_mention: Алиас is_mention для обратной совместимости.
            is_reply: True если сообщение является reply на сообщение Краба.
            is_dm: True если это личный чат — форсирует ответ (compat).

        Returns:
            True если Краб должен ответить.
        """
        # DM всегда форсирует ответ (если не стоит явный muted)
        if is_dm:
            mode = self.get_mode(chat_id, is_group=False)
            return mode != "muted"
        # has_mention — legacy alias; is_mention приоритетнее если оба переданы
        effective_mention = is_mention or (has_mention is True)
        mode = self.get_mode(chat_id, is_group=is_group)
        if mode == "muted":
            return False
        if mode == "active":
            return True
        # mention-only
        return effective_mention or is_reply


# Singleton
chat_filter_config = ChatFilterConfig()
