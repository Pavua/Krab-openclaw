# -*- coding: utf-8 -*-
"""
Тесты ACL и access-control модулей.

Покрытие:
- permission checks (can_execute_command)
- role resolution (resolve_access_profile)
- owner / guest различие
- blocklist (MANUAL_BLOCKLIST)
- allowlist (FULL_ACCESS_USERS, PARTIAL_ACCESS_USERS)
- normalize_subject / _split_subjects
- update_acl_subject / save + load cycle
- build_command_access_matrix
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.core.access_control import (
    OWNER_ONLY_COMMANDS,
    PARTIAL_ACCESS_COMMANDS,
    AccessLevel,
    AccessProfile,
    _extract_acl_subjects,
    _split_subjects,
    build_command_access_matrix,
    load_acl_runtime_state,
    normalize_subject,
    resolve_access_profile,
    save_acl_runtime_state,
    update_acl_subject,
)


# ---------------------------------------------------------------------------
# normalize_subject
# ---------------------------------------------------------------------------


def test_normalize_subject_strips_at() -> None:
    """@ удаляется, результат lower-case."""
    assert normalize_subject("@Pavel") == "pavel"


def test_normalize_subject_empty() -> None:
    """None и пустая строка → пустая строка."""
    assert normalize_subject(None) == ""
    assert normalize_subject("") == ""


# ---------------------------------------------------------------------------
# _split_subjects
# ---------------------------------------------------------------------------


def test_split_subjects_separates_ids_and_usernames() -> None:
    """Числовые строки → ids, остальные → usernames."""
    ids, names = _split_subjects(["123456", "@alice", "bob"])
    assert "123456" in ids
    assert "alice" in names
    assert "bob" in names


# ---------------------------------------------------------------------------
# _extract_acl_subjects — dict формат
# ---------------------------------------------------------------------------


def test_extract_acl_subjects_dict_format() -> None:
    """Формат {'ids': [...], 'usernames': [...]} корректно разбирается."""
    raw = {"ids": ["111"], "usernames": ["carol"]}
    ids, names = _extract_acl_subjects(raw)
    assert "111" in ids
    assert "carol" in names


# ---------------------------------------------------------------------------
# AccessProfile — property helpers
# ---------------------------------------------------------------------------


def test_access_profile_owner_is_trusted() -> None:
    """owner и full являются доверенными; partial и guest — нет."""
    assert AccessProfile(level=AccessLevel.OWNER).is_trusted
    assert AccessProfile(level=AccessLevel.FULL).is_trusted
    assert not AccessProfile(level=AccessLevel.PARTIAL).is_trusted
    assert not AccessProfile(level=AccessLevel.GUEST).is_trusted


def test_access_profile_partial_can_use_partial_commands() -> None:
    """partial имеет доступ к базовым командам."""
    assert AccessProfile(level=AccessLevel.PARTIAL).can_use_partial_commands
    assert not AccessProfile(level=AccessLevel.GUEST).can_use_partial_commands


# ---------------------------------------------------------------------------
# can_execute_command — owner vs full vs partial vs guest
# ---------------------------------------------------------------------------

_KNOWN = {"status", "restart", "acl", "search", "help"}


def test_owner_can_execute_owner_only_commands() -> None:
    """owner запускает любые команды, включая owner-only."""
    profile = AccessProfile(level=AccessLevel.OWNER)
    assert profile.can_execute_command("restart", _KNOWN)
    assert profile.can_execute_command("acl", _KNOWN)


def test_full_cannot_execute_owner_only_commands() -> None:
    """full не может выполнять owner-only команды."""
    profile = AccessProfile(level=AccessLevel.FULL)
    for cmd in OWNER_ONLY_COMMANDS:
        if cmd in _KNOWN:
            assert not profile.can_execute_command(cmd, _KNOWN)


def test_full_can_execute_non_owner_only_commands() -> None:
    """full может выполнять обычные команды."""
    profile = AccessProfile(level=AccessLevel.FULL)
    assert profile.can_execute_command("status", _KNOWN)


def test_partial_limited_to_partial_commands() -> None:
    """partial — только команды из PARTIAL_ACCESS_COMMANDS."""
    profile = AccessProfile(level=AccessLevel.PARTIAL)
    # search входит в PARTIAL_ACCESS_COMMANDS
    assert profile.can_execute_command("search", _KNOWN)
    # status тоже в PARTIAL_ACCESS_COMMANDS
    assert profile.can_execute_command("status", _KNOWN)
    # restart — нет
    assert not profile.can_execute_command("restart", _KNOWN)


def test_guest_cannot_execute_any_command() -> None:
    """guest не может выполнять ни одну команду."""
    profile = AccessProfile(level=AccessLevel.GUEST)
    for cmd in _KNOWN:
        assert not profile.can_execute_command(cmd, _KNOWN)


def test_unknown_command_denied_for_owner() -> None:
    """Команда не из known_commands недоступна даже для owner."""
    profile = AccessProfile(level=AccessLevel.OWNER)
    assert not profile.can_execute_command("nonexistent", _KNOWN)


# ---------------------------------------------------------------------------
# resolve_access_profile — owner/full/partial/guest
# ---------------------------------------------------------------------------


def _mock_config(
    *,
    owner_ids: list = [],
    full_users: list = [],
    partial_users: list = [],
    owner_username: str = "",
    acl_file: Path | None = None,
) -> MagicMock:
    cfg = MagicMock()
    cfg.OWNER_USER_IDS = owner_ids
    cfg.FULL_ACCESS_USERS = full_users
    cfg.PARTIAL_ACCESS_USERS = partial_users
    cfg.OWNER_USERNAME = owner_username
    cfg.USERBOT_ACL_FILE = acl_file or Path("/nonexistent/acl.json")
    return cfg


def test_resolve_self_returns_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    """Сам userbot-пользователь → owner (self-id совпадает)."""
    import src.core.access_control as acl_module

    monkeypatch.setattr(acl_module, "config", _mock_config())
    profile = resolve_access_profile(user_id=42, username="me", self_user_id=42)
    assert profile.level == AccessLevel.OWNER
    assert profile.source == "self"


def test_resolve_owner_by_env_id(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """ID в OWNER_USER_IDS → owner."""
    import src.core.access_control as acl_module

    monkeypatch.setattr(
        acl_module, "config", _mock_config(owner_ids=["999"], acl_file=tmp_path / "acl.json")
    )
    profile = resolve_access_profile(user_id=999, username="someone", self_user_id=1)
    assert profile.level == AccessLevel.OWNER


def test_resolve_full_by_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Username в FULL_ACCESS_USERS → full."""
    import src.core.access_control as acl_module

    monkeypatch.setattr(
        acl_module, "config", _mock_config(full_users=["trusted"], acl_file=tmp_path / "acl.json")
    )
    profile = resolve_access_profile(user_id=55, username="trusted", self_user_id=1)
    assert profile.level == AccessLevel.FULL


def test_resolve_partial_by_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Username в PARTIAL_ACCESS_USERS → partial."""
    import src.core.access_control as acl_module

    monkeypatch.setattr(
        acl_module, "config", _mock_config(partial_users=["reader"], acl_file=tmp_path / "acl.json")
    )
    profile = resolve_access_profile(user_id=77, username="reader", self_user_id=1)
    assert profile.level == AccessLevel.PARTIAL


def test_resolve_unknown_returns_guest(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Неизвестный пользователь → guest."""
    import src.core.access_control as acl_module

    monkeypatch.setattr(acl_module, "config", _mock_config(acl_file=tmp_path / "acl.json"))
    profile = resolve_access_profile(user_id=0, username="stranger", self_user_id=1)
    assert profile.level == AccessLevel.GUEST


# ---------------------------------------------------------------------------
# update_acl_subject — add / remove
# ---------------------------------------------------------------------------


def test_update_acl_subject_add(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Добавление субъекта в ACL-файл меняет состояние."""
    import src.core.access_control as acl_module

    monkeypatch.setattr(acl_module, "config", _mock_config(acl_file=tmp_path / "acl.json"))
    result = update_acl_subject("full", "newuser", add=True, path=tmp_path / "acl.json")
    assert result["changed"] is True
    assert "newuser" in result["state"]["full"]


def test_update_acl_subject_remove(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Удаление субъекта из ACL-файла."""
    import src.core.access_control as acl_module

    acl_path = tmp_path / "acl.json"
    monkeypatch.setattr(acl_module, "config", _mock_config(acl_file=acl_path))
    # Сначала добавляем
    update_acl_subject("partial", "todelete", add=True, path=acl_path)
    # Потом удаляем
    result = update_acl_subject("partial", "todelete", add=False, path=acl_path)
    assert result["changed"] is True
    assert "todelete" not in result["state"]["partial"]


def test_update_acl_subject_invalid_level(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Неверный уровень → ValueError."""
    import src.core.access_control as acl_module

    monkeypatch.setattr(acl_module, "config", _mock_config(acl_file=tmp_path / "acl.json"))
    with pytest.raises(ValueError, match="unsupported_acl_level"):
        update_acl_subject("superadmin", "user", add=True, path=tmp_path / "acl.json")


# ---------------------------------------------------------------------------
# save / load round-trip
# ---------------------------------------------------------------------------


def test_save_load_roundtrip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """save_acl_runtime_state + load_acl_runtime_state сохраняют и восстанавливают данные."""
    import src.core.access_control as acl_module

    acl_path = tmp_path / "acl.json"
    monkeypatch.setattr(acl_module, "config", _mock_config(acl_file=acl_path))
    state_in = {"owner": ["owneruser"], "full": ["fu1", "fu2"], "partial": ["pu1"]}
    save_acl_runtime_state(state_in, path=acl_path)
    state_out = load_acl_runtime_state(path=acl_path)
    assert "owneruser" in state_out["owner"]
    assert sorted(["fu1", "fu2"]) == sorted(state_out["full"])
    assert "pu1" in state_out["partial"]


# ---------------------------------------------------------------------------
# build_command_access_matrix
# ---------------------------------------------------------------------------


def test_build_command_access_matrix_structure() -> None:
    """Матрица содержит все роли и owner_only_commands."""
    matrix = build_command_access_matrix()
    assert "roles" in matrix
    for role in ("owner", "full", "partial", "guest"):
        assert role in matrix["roles"]
    # owner-only не попадают в full
    for cmd in matrix["owner_only_commands"]:
        assert cmd not in matrix["roles"]["full"]["commands"]
    # guest — пустой список
    assert matrix["roles"]["guest"]["commands"] == []
