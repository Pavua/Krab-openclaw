# -*- coding: utf-8 -*-
"""
AliasService — пользовательские алиасы для Telegram-команд.

Алиас позволяет задать короткое имя для длинной команды:
  !alias set t !translate
  !t привет  →  !translate привет

Хранение: ~/.openclaw/krab_runtime_state/command_aliases.json
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Optional

from .logger import get_logger

logger = get_logger(__name__)

# Максимальное количество алиасов (защита от злоупотреблений)
MAX_ALIASES = 100

# Запрещённые имена алиасов (совпадают с встроенными командами Краба)
RESERVED_NAMES = frozenset(
    {
        "alias",
        "help",
        "status",
        "model",
        "clear",
        "swarm",
        "search",
        "restart",
    }
)


class AliasService:
    """Сервис управления пользовательскими алиасами команд."""

    def __init__(self, storage_path: Optional[Path] = None) -> None:
        # Путь по умолчанию — рядом с остальным runtime-state
        if storage_path is None:
            storage_path = Path.home() / ".openclaw" / "krab_runtime_state" / "command_aliases.json"
        self._path = Path(storage_path)
        self._lock = threading.Lock()
        # Словарь: имя_алиаса → строка команды (без префикса '!')
        self._aliases: dict[str, str] = {}
        self._load()

    # ─── внутренние методы ────────────────────────────────────────────────────

    def _load(self) -> None:
        """Загружает алиасы из JSON-файла."""
        try:
            if self._path.exists():
                raw = self._path.read_text(encoding="utf-8")
                data = json.loads(raw)
                if isinstance(data, dict):
                    self._aliases = {str(k): str(v) for k, v in data.items() if k and v}
                    logger.info("alias_service_loaded", count=len(self._aliases))
        except Exception as exc:
            logger.warning("alias_service_load_error", error=str(exc))
            self._aliases = {}

    def _save(self) -> None:
        """Сохраняет текущие алиасы в JSON-файл."""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps(self._aliases, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.warning("alias_service_save_error", error=str(exc))

    # ─── публичный API ────────────────────────────────────────────────────────

    def add(self, name: str, command: str) -> tuple[bool, str]:
        """
        Создаёт или обновляет алиас.

        Args:
            name: Имя алиаса (без '!'), например 't'
            command: Команда (с '!' или без), например '!translate' или 'translate'

        Returns:
            (успех, сообщение)
        """
        name = name.strip().lstrip("!").lower()
        command = command.strip()

        if not name:
            return False, "Имя алиаса не может быть пустым."

        if name in RESERVED_NAMES:
            return False, f"Имя `{name}` зарезервировано и не может быть использовано как алиас."

        if len(name) > 32:
            return False, "Имя алиаса слишком длинное (макс. 32 символа)."

        if not command:
            return False, "Команда алиаса не может быть пустой."

        # Нормализуем: убираем лишние '!' и пробелы
        command = command.lstrip("!").strip()
        if not command:
            return False, "Некорректная команда для алиаса."

        with self._lock:
            if len(self._aliases) >= MAX_ALIASES and name not in self._aliases:
                return False, f"Достигнут лимит алиасов ({MAX_ALIASES})."
            is_update = name in self._aliases
            self._aliases[name] = command
            self._save()

        action = "обновлён" if is_update else "создан"
        logger.info("alias_added", name=name, command=command, updated=is_update)
        return True, f"Алиас `!{name}` → `!{command}` {action}."

    def remove(self, name: str) -> tuple[bool, str]:
        """
        Удаляет алиас по имени.

        Returns:
            (успех, сообщение)
        """
        name = name.strip().lstrip("!").lower()
        with self._lock:
            if name not in self._aliases:
                return False, f"Алиас `!{name}` не найден."
            del self._aliases[name]
            self._save()
        logger.info("alias_removed", name=name)
        return True, f"Алиас `!{name}` удалён."

    def list_all(self) -> dict[str, str]:
        """Возвращает копию всех алиасов."""
        with self._lock:
            return dict(self._aliases)

    def resolve(self, text: str) -> str:
        """
        Если текст начинается с алиаса — подменяет его на целевую команду.

        Пример:
            resolve("!t привет")  →  "!translate привет"
            resolve("!status")    →  "!status"  (не алиас)

        Args:
            text: Исходный текст сообщения

        Returns:
            Подменённый текст (или исходный, если алиас не найден)
        """
        stripped = text.lstrip()
        if not stripped or stripped[0] not in ("!", "/", "."):
            return text

        prefix = stripped[0]
        rest = stripped[1:]  # убираем '!'
        parts = rest.split(None, 1)
        if not parts:
            return text

        alias_name = parts[0].lower()
        with self._lock:
            target_command = self._aliases.get(alias_name)

        if target_command is None:
            return text  # не алиас

        # Строим заменённый текст: !target_command [аргументы]
        tail = parts[1] if len(parts) > 1 else ""
        resolved = f"{prefix}{target_command}"
        if tail:
            resolved = f"{resolved} {tail}"

        logger.debug("alias_resolved", alias=alias_name, target=target_command, tail=tail[:80])
        return resolved

    def format_list(self) -> str:
        """Форматирует список алиасов для Telegram-ответа."""
        aliases = self.list_all()
        if not aliases:
            return "Алиасов пока нет.\n\nДобавить: `!alias set <имя> <команда>`"
        lines = [f"`!{name}` → `!{cmd}`" for name, cmd in sorted(aliases.items())]
        header = f"**Алиасы команд** ({len(aliases)}/{MAX_ALIASES}):\n"
        return header + "\n".join(lines)


# Синглтон для использования во всём проекте
alias_service = AliasService()
