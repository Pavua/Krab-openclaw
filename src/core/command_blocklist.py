# -*- coding: utf-8 -*-
"""Per-chat command blocklist — Краб молча игнорирует команды в чатах, где они заняты другим ботом.

Config: ~/.openclaw/krab_runtime_state/command_blocklist.json
Формат: {"chat_id_str": ["status", "start", ...], "*": ["globally_blocked"]}

Заметка: команды хранятся без префикса `!` для единообразия.
Поддерживаемые вызовы:
  - is_blocked(chat_id, command) -> bool
  - add_block(chat_id, command)
  - remove_block(chat_id, command)
  - list_blocks(chat_id | None) -> dict | list

Default: How2AI (-1001587432709) → "status" (там другой бот реагирует на !status).
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Union

from .logger import get_logger

logger = get_logger(__name__)

_STATE_DIR = Path.home() / ".openclaw" / "krab_runtime_state"
_BLOCKLIST_FILE = _STATE_DIR / "command_blocklist.json"

# Глобальный wildcard-ключ
_GLOBAL_KEY = "*"

# Конфигурация по умолчанию
_DEFAULT_CONFIG: dict[str, list[str]] = {
    "-1001587432709": ["status"],  # How2AI — другой бот использует !status
}


class CommandBlocklist:
    """Thread-safe per-chat command blocklist с персистентным JSON-хранилищем."""

    def __init__(
        self,
        state_dir: Path | None = None,
        blocklist_file: Path | None = None,
    ) -> None:
        self._state_dir = state_dir or _STATE_DIR
        self._blocklist_file = blocklist_file or _BLOCKLIST_FILE
        self._lock = threading.Lock()
        self._data: dict[str, list[str]] = {}
        self._load()

    # ------------------------------------------------------------------
    # Персистентность
    # ------------------------------------------------------------------

    def _load(self) -> None:
        """Загрузить с диска; при отсутствии файла — записать defaults.

        Важно: defaults применяются как baseline — если для конкретного чата
        нет записи в файле, она берётся из _DEFAULT_CONFIG. Это гарантирует,
        что даже при пустом {} файле How2AI-блок сохраняется.
        """
        loaded: dict[str, list[str]] = {}
        if self._blocklist_file.exists():
            try:
                raw = self._blocklist_file.read_text(encoding="utf-8")
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    loaded = {k: list(v) for k, v in parsed.items()}
                    logger.debug("command_blocklist_loaded", entries=len(loaded))
            except Exception as exc:  # noqa: BLE001
                logger.warning("command_blocklist_load_error", error=str(exc))

        # Мёржим defaults: для ключей, которых нет в сохранённом файле,
        # берём значения из _DEFAULT_CONFIG. Уже существующие ключи — не трогаем.
        merged = {k: list(v) for k, v in _DEFAULT_CONFIG.items()}
        merged.update(loaded)  # loaded перекрывает defaults только для своих ключей
        self._data = merged

        # Если файл отсутствовал или не содержал defaults — персистируем
        needs_persist = not self._blocklist_file.exists()
        if not needs_persist:
            for key, cmds in _DEFAULT_CONFIG.items():
                if key not in loaded:
                    needs_persist = True
                    break
        if needs_persist:
            self._persist()

    def _persist(self) -> None:
        """Записать текущее состояние на диск."""
        try:
            self._state_dir.mkdir(parents=True, exist_ok=True)
            tmp = self._blocklist_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self._blocklist_file)
        except Exception as exc:  # noqa: BLE001
            logger.error("command_blocklist_persist_error", error=str(exc))

    # ------------------------------------------------------------------
    # Публичный API
    # ------------------------------------------------------------------

    def is_blocked(self, chat_id: int, command: str) -> bool:
        """Проверить, заблокирована ли команда в данном чате.

        Сначала проверяется global wildcard "*", затем конкретный chat_id.
        Команда нормализуется: убирается ведущий '!' / '.' / '/'.
        """
        cmd = _normalize(command)
        with self._lock:
            # Global block имеет приоритет
            if cmd in self._data.get(_GLOBAL_KEY, []):
                return True
            return cmd in self._data.get(str(chat_id), [])

    def add_block(self, chat_id: Union[int, str], command: str) -> bool:
        """Добавить блок. Возвращает True если добавлен, False если уже был."""
        key = _GLOBAL_KEY if str(chat_id) == _GLOBAL_KEY else str(chat_id)
        cmd = _normalize(command)
        with self._lock:
            existing = self._data.setdefault(key, [])
            if cmd in existing:
                return False
            existing.append(cmd)
            self._persist()
        logger.info("command_blocklist_added", chat=key, command=cmd)
        return True

    def remove_block(self, chat_id: Union[int, str], command: str) -> bool:
        """Убрать блок. Возвращает True если удалён, False если не было."""
        key = _GLOBAL_KEY if str(chat_id) == _GLOBAL_KEY else str(chat_id)
        cmd = _normalize(command)
        with self._lock:
            lst = self._data.get(key, [])
            if cmd not in lst:
                return False
            lst.remove(cmd)
            if not lst:
                del self._data[key]
            self._persist()
        logger.info("command_blocklist_removed", chat=key, command=cmd)
        return True

    def list_blocks(self, chat_id: Union[int, str, None] = None) -> Union[dict, list]:
        """Вернуть все блоки (dict) или блоки конкретного чата (list)."""
        with self._lock:
            if chat_id is None:
                return {k: list(v) for k, v in self._data.items()}
            key = _GLOBAL_KEY if str(chat_id) == _GLOBAL_KEY else str(chat_id)
            return list(self._data.get(key, []))

    def reload(self) -> None:
        """Перечитать с диска (для hot-reload)."""
        with self._lock:
            self._load()


# ------------------------------------------------------------------
# Хелперы
# ------------------------------------------------------------------


def _normalize(command: str) -> str:
    """Убрать ведущие/хвостовые пробелы, ведущие символы !./ и привести к нижнему регистру."""
    return command.strip().lstrip("!/. \t").lower()


# Singleton
command_blocklist = CommandBlocklist()
