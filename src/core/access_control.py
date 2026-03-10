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
        if self.level in {AccessLevel.OWNER, AccessLevel.FULL}:
            return True
        if self.level == AccessLevel.PARTIAL:
            return command in PARTIAL_ACCESS_COMMANDS
        return False


def _load_acl_file(path: Path) -> dict[str, Any]:
    """Читает runtime-файл ACL; при ошибке возвращает пустой словарь."""

    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


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

    if owner_username and normalized_username and owner_username == normalized_username:
        return AccessProfile(level=AccessLevel.OWNER, source="config.owner_username", matched_subject=normalized_username)

    owner_match = _matches(owner_env_ids | owner_file_ids, owner_env_names | owner_file_names)
    if owner_match:
        return AccessProfile(level=AccessLevel.OWNER, source="owner_acl", matched_subject=owner_match)

    full_match = _matches(full_env_ids | full_file_ids, full_env_names | full_file_names)
    if full_match:
        return AccessProfile(level=AccessLevel.FULL, source="full_acl", matched_subject=full_match)

    partial_match = _matches(partial_env_ids | partial_file_ids, partial_env_names | partial_file_names)
    if partial_match:
        return AccessProfile(level=AccessLevel.PARTIAL, source="partial_acl", matched_subject=partial_match)

    return AccessProfile(level=AccessLevel.GUEST, source="default_guest", matched_subject="")
