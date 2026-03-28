# -*- coding: utf-8 -*-
"""
access_control.py — общий ACL-слой для owner/full/partial доступа.

Что это:
- единая точка, где userbot определяет уровень доступа отправителя;
- мягкая миграция от старого `ALLOWED_USERS` к явным ролям owner/full/partial;
- безопасная база для будущей web-настройки прав без дублирования логики.

Зачем нужно:
- раньше в проекте был только бинарный флаг "allowed / not allowed";
- для userbot-primary это слишком грубо: владельцу нужны максимальные права,
  доверенным пользователям — полный или частичный доступ, а гостям — safe-mode;
- этот слой не ломает старый allowlist: legacy `ALLOWED_USERS` автоматически
  считается частью full-доступа, пока UI и runtime не переведены полностью.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from ..config import config


class AccessLevel(str, Enum):
    """Поддерживаемые уровни доступа userbot."""

    OWNER = "owner"
    FULL = "full"
    PARTIAL = "partial"
    GUEST = "guest"


USERBOT_KNOWN_COMMANDS: frozenset[str] = frozenset(
    {
        "status",
        "model",
        "clear",
        "config",
        "set",
        "role",
        "voice",
        "translator",
        "web",
        "sysinfo",
        "panel",
        "restart",
        "search",
        "mac",
        "watch",
        "memory",
        "inbox",
        "remember",
        "recall",
        "ls",
        "read",
        "write",
        "agent",
        "diagnose",
        "help",
        "remind",
        "reminders",
        "rm_remind",
        "cronstatus",
        "acl",
        "access",
        "reasoning",
        # CLI runner команды (Приоритет 3)
        "codex",
        "gemini",
        "claude_cli",
        "opencode",
        # Desktop automation
        "hs",
        # Browser & screenshots
        "screenshot",
        "browser",
        # Shopping
        "shop",
        # Agent swarm
        "swarm",
        # Capability policy matrix
        "cap",
    }
)


OWNER_ONLY_COMMANDS: frozenset[str] = frozenset(
    {
        "set",
        "restart",
        "acl",
        "access",
        "reasoning",
        # CLI runner — запускают внешние процессы на машине, только владелец
        "codex",
        "gemini",
        "claude_cli",
        "opencode",
        # Desktop automation — управляет окнами через Hammerspoon, только владелец
        "hs",
        # Capability policy matrix — runtime toggle, только владелец
        "cap",
    }
)


PARTIAL_ACCESS_COMMANDS: frozenset[str] = frozenset(
    {
        "help",
        "search",
        "status",
    }
)


def normalize_subject(value: object) -> str:
    """Нормализует id/username для ACL-сравнения."""

    return str(value or "").strip().lstrip("@").lower()


def _split_subjects(items: list[object]) -> tuple[set[str], set[str]]:
    """Разделяет список субъектов на id и usernames."""

    ids: set[str] = set()
    usernames: set[str] = set()
    for raw in items:
        item = normalize_subject(raw)
        if not item:
            continue
        if item.isdigit():
            ids.add(item)
        else:
            usernames.add(item)
    return ids, usernames


def _extract_acl_subjects(raw_block: object) -> tuple[set[str], set[str]]:
    """
    Извлекает ACL-субъекты из нескольких совместимых форматов.

    Поддерживаемые форматы:
    - ["123", "@user"]
    - {"ids": ["123"], "usernames": ["user"]}
    """

    if isinstance(raw_block, list):
        return _split_subjects(raw_block)
    if isinstance(raw_block, dict):
        items: list[object] = []
        for key in ("ids", "usernames", "subjects", "users"):
            value = raw_block.get(key)
            if isinstance(value, list):
                items.extend(value)
        return _split_subjects(items)
    return set(), set()


@dataclass(frozen=True)
class AccessProfile:
    """Разрешённый runtime-профиль пользователя."""

    level: AccessLevel
    source: str = ""
    matched_subject: str = ""

    @property
    def is_trusted(self) -> bool:
        """Доверенный контур: owner и full."""

        return self.level in {AccessLevel.OWNER, AccessLevel.FULL}

    @property
    def can_use_partial_commands(self) -> bool:
        """Минимальный командный слой для частичного доступа."""

        return self.level in {AccessLevel.OWNER, AccessLevel.FULL, AccessLevel.PARTIAL}

    def can_execute_command(self, command_name: str, known_commands: set[str]) -> bool:
        """Проверяет, разрешена ли Telegram-команда для профиля."""

        command = normalize_subject(command_name)
        if not command or command not in known_commands:
            return False
        if self.level == AccessLevel.OWNER:
            return True
        if self.level == AccessLevel.FULL:
            return command not in OWNER_ONLY_COMMANDS
        if self.level == AccessLevel.PARTIAL:
            return command in PARTIAL_ACCESS_COMMANDS
        return False


def build_command_access_matrix(known_commands: set[str] | frozenset[str] | None = None) -> dict[str, Any]:
    """
    Возвращает единый truth-срез командного доступа по ролям.

    Нужен, чтобы userbot, owner UI и policy matrix не расходились в вопросе:
    какие команды доступны `owner/full/partial/guest`, а какие оставлены только владельцу.
    """

    commands = {
        normalize_subject(item)
        for item in (known_commands or USERBOT_KNOWN_COMMANDS)
        if normalize_subject(item)
    }
    owner_commands = sorted(commands)
    full_commands = sorted(command for command in commands if command not in OWNER_ONLY_COMMANDS)
    partial_commands = sorted(command for command in commands if command in PARTIAL_ACCESS_COMMANDS)
    return {
        "role_order": [
            AccessLevel.OWNER.value,
            AccessLevel.FULL.value,
            AccessLevel.PARTIAL.value,
            AccessLevel.GUEST.value,
        ],
        "owner_only_commands": sorted(OWNER_ONLY_COMMANDS),
        "roles": {
            AccessLevel.OWNER.value: {
                "commands": owner_commands,
                "command_count": len(owner_commands),
                "notes": [
                    "Owner-контур включает admin/runtime команды и write-операции.",
                ],
            },
            AccessLevel.FULL.value: {
                "commands": full_commands,
                "command_count": len(full_commands),
                "notes": [
                    "Full не получает owner-only admin-команды `!set`, `!restart`, `!acl`, `!access`.",
                ],
            },
            AccessLevel.PARTIAL.value: {
                "commands": partial_commands,
                "command_count": len(partial_commands),
                "notes": [
                    "Partial ограничен безопасными командами без runtime/admin мутаций.",
                ],
            },
            AccessLevel.GUEST.value: {
                "commands": [],
                "command_count": 0,
                "notes": [
                    "Guest работает как обычный чат без служебных Telegram-команд.",
                ],
            },
        },
        "summary": {
            "known_commands": len(commands),
            "owner_only_count": len(OWNER_ONLY_COMMANDS),
            "full_without_owner_only_count": len(full_commands),
            "partial_count": len(partial_commands),
        },
    }


def _load_acl_file(path: Path) -> dict[str, Any]:
    """Читает runtime-файл ACL; при ошибке возвращает пустой словарь."""

    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_acl_file(path: Path, payload: dict[str, Any]) -> None:
    """Сохраняет runtime ACL-файл в детерминированном JSON-виде."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _runtime_acl_path(path: Path | None = None) -> Path:
    """Возвращает канонический путь runtime ACL-файла."""
    if isinstance(path, Path):
        return path
    candidate = getattr(config, "USERBOT_ACL_FILE", None)
    if isinstance(candidate, Path):
        return candidate
    return Path.home() / ".openclaw" / "krab_userbot_acl.json"


def load_acl_runtime_state(path: Path | None = None) -> dict[str, list[str]]:
    """
    Возвращает нормализованное runtime-состояние ACL-файла.

    Формат результата всегда единый:
    - `owner`: список subjects из файла;
    - `full`: список subjects из файла;
    - `partial`: список subjects из файла.
    """
    acl_path = _runtime_acl_path(path)
    raw_payload = _load_acl_file(acl_path)
    state: dict[str, list[str]] = {}
    for level in (AccessLevel.OWNER.value, AccessLevel.FULL.value, AccessLevel.PARTIAL.value):
        ids, usernames = _extract_acl_subjects(raw_payload.get(level))
        state[level] = sorted(ids | usernames)
    return state


def get_effective_owner_subjects(path: Path | None = None) -> list[str]:
    """
    Возвращает truthful owner subjects для runtime/UI.

    Почему нужен отдельный helper:
    - `OWNER_USERNAME` в config может быть историческим fallback и не отражать
      текущего владельца в multi-account среде;
    - runtime ACL-файл для userbot является более свежим источником истины;
    - owner UI и Telegram-команда `!acl status` должны показывать именно тот
      owner-контур, который реально даёт права сейчас.
    """

    state = load_acl_runtime_state(path)
    runtime_owner_items = sorted(
        {
            normalize_subject(item)
            for item in state.get(AccessLevel.OWNER.value, [])
            if normalize_subject(item)
        }
    )
    if runtime_owner_items:
        return runtime_owner_items

    fallback_items: set[str] = {
        normalize_subject(item)
        for item in list(getattr(config, "OWNER_USER_IDS", []))
        if normalize_subject(item)
    }
    owner_username = normalize_subject(getattr(config, "OWNER_USERNAME", ""))
    if owner_username:
        fallback_items.add(owner_username)
    return sorted(fallback_items)


def get_effective_owner_label(path: Path | None = None) -> str:
    """
    Возвращает человекочитаемую строку owner-контекста.

    Нужна для UI/команд, которым исторически удобнее работать со строкой, а не
    со списком субъектов.
    """

    subjects = get_effective_owner_subjects(path)
    return ", ".join(subjects) if subjects else "-"


def save_acl_runtime_state(state: dict[str, list[str]], path: Path | None = None) -> Path:
    """Сохраняет нормализованный ACL state в runtime-файл."""
    acl_path = _runtime_acl_path(path)
    payload: dict[str, list[str]] = {}
    for level in (AccessLevel.OWNER.value, AccessLevel.FULL.value, AccessLevel.PARTIAL.value):
        values = state.get(level) if isinstance(state, dict) else []
        normalized = sorted(
            {
                normalize_subject(item)
                for item in (values or [])
                if normalize_subject(item)
            }
        )
        payload[level] = normalized
    _save_acl_file(acl_path, payload)
    return acl_path


def update_acl_subject(level: str | AccessLevel, subject: object, *, add: bool, path: Path | None = None) -> dict[str, Any]:
    """
    Добавляет или удаляет subject из runtime ACL-файла.

    Возвращает:
    - `changed`: было ли фактическое изменение;
    - `state`: новое нормализованное состояние;
    - `path`: куда записан ACL.
    """
    normalized_level = str(level.value if isinstance(level, AccessLevel) else level or "").strip().lower()
    if normalized_level not in {AccessLevel.OWNER.value, AccessLevel.FULL.value, AccessLevel.PARTIAL.value}:
        raise ValueError(f"unsupported_acl_level:{normalized_level or 'empty'}")

    normalized_subject = normalize_subject(subject)
    if not normalized_subject:
        raise ValueError("empty_acl_subject")

    state = load_acl_runtime_state(path)
    entries = set(state.get(normalized_level, []))
    before = set(entries)
    if add:
        entries.add(normalized_subject)
    else:
        entries.discard(normalized_subject)
    state[normalized_level] = sorted(entries)
    changed = before != entries
    acl_path = save_acl_runtime_state(state, path) if changed else _runtime_acl_path(path)
    return {
        "changed": changed,
        "path": acl_path,
        "state": state,
        "level": normalized_level,
        "subject": normalized_subject,
    }


def resolve_access_profile(*, user_id: object, username: object, self_user_id: object | None) -> AccessProfile:
    """
    Определяет ACL-профиль пользователя.

    Порядок приоритетов:
    1. Сам userbot-пользователь и явный owner.
    2. `owner` из ACL runtime-файла.
    3. `full` из ACL runtime-файла и legacy `ALLOWED_USERS`.
    4. `partial` из ACL runtime-файла / env.
    5. guest.
    """

    normalized_user_id = normalize_subject(user_id)
    normalized_username = normalize_subject(username)
    normalized_self_id = normalize_subject(self_user_id)

    acl_path = Path(getattr(config, "USERBOT_ACL_FILE", Path.home() / ".openclaw" / "krab_userbot_acl.json"))
    acl_payload = _load_acl_file(acl_path)

    owner_file_ids, owner_file_names = _extract_acl_subjects(acl_payload.get("owner"))
    full_file_ids, full_file_names = _extract_acl_subjects(acl_payload.get("full"))
    partial_file_ids, partial_file_names = _extract_acl_subjects(acl_payload.get("partial"))

    owner_env_ids, owner_env_names = _split_subjects(list(getattr(config, "OWNER_USER_IDS", [])))
    full_env_ids, full_env_names = _split_subjects(list(getattr(config, "FULL_ACCESS_USERS", [])))
    partial_env_ids, partial_env_names = _split_subjects(list(getattr(config, "PARTIAL_ACCESS_USERS", [])))
    owner_username = normalize_subject(getattr(config, "OWNER_USERNAME", ""))

    def _matches(ids: set[str], usernames: set[str]) -> str:
        if normalized_user_id and normalized_user_id in ids:
            return normalized_user_id
        if normalized_username and normalized_username in usernames:
            return normalized_username
        return ""

    if normalized_self_id and normalized_user_id and normalized_self_id == normalized_user_id:
        return AccessProfile(level=AccessLevel.OWNER, source="self", matched_subject=normalized_user_id)

    owner_match = _matches(owner_env_ids | owner_file_ids, owner_env_names | owner_file_names)
    if owner_match:
        return AccessProfile(level=AccessLevel.OWNER, source="owner_acl", matched_subject=owner_match)

    if owner_username and normalized_username and owner_username == normalized_username:
        return AccessProfile(level=AccessLevel.OWNER, source="config.owner_username", matched_subject=normalized_username)

    full_match = _matches(full_env_ids | full_file_ids, full_env_names | full_file_names)
    if full_match:
        return AccessProfile(level=AccessLevel.FULL, source="full_acl", matched_subject=full_match)

    partial_match = _matches(partial_env_ids | partial_file_ids, partial_env_names | partial_file_names)
    if partial_match:
        return AccessProfile(level=AccessLevel.PARTIAL, source="partial_acl", matched_subject=partial_match)

    return AccessProfile(level=AccessLevel.GUEST, source="default_guest", matched_subject="")
