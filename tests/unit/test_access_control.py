# -*- coding: utf-8 -*-
"""
Тесты ACL-слоя owner/full/partial.

Покрываем:
1) legacy `ALLOWED_USERS` как источник full-доступа;
2) owner по username/id;
3) partial-доступ и безопасный набор команд.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.config import config
from src.core.access_control import (
    OWNER_ONLY_COMMANDS,
    PARTIAL_ACCESS_COMMANDS,
    USERBOT_KNOWN_COMMANDS,
    AccessLevel,
    build_command_access_matrix,
    get_effective_owner_label,
    get_effective_owner_subjects,
    load_acl_runtime_state,
    resolve_access_profile,
    update_acl_subject,
)


def test_resolve_access_profile_marks_self_as_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "USERBOT_ACL_FILE", tmp_path / "acl.json", raising=False)
    profile = resolve_access_profile(user_id=777, username="owner", self_user_id=777)
    assert profile.level == AccessLevel.OWNER


def test_resolve_access_profile_uses_owner_username(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(config, "USERBOT_ACL_FILE", tmp_path / "acl.json", raising=False)
    monkeypatch.setattr(config, "OWNER_USERNAME", "@pablito", raising=False)
    profile = resolve_access_profile(user_id=42, username="pablito", self_user_id=777)
    assert profile.level == AccessLevel.OWNER


def test_resolve_access_profile_prefers_runtime_owner_over_stale_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    acl_path = tmp_path / "acl.json"
    acl_path.write_text('{"owner":["p0lrd"]}', encoding="utf-8")
    monkeypatch.setattr(config, "USERBOT_ACL_FILE", acl_path, raising=False)
    monkeypatch.setattr(config, "OWNER_USERNAME", "@yung_nagato", raising=False)
    profile = resolve_access_profile(user_id=42, username="p0lrd", self_user_id=777)
    assert profile.level == AccessLevel.OWNER
    assert profile.source == "owner_acl"


def test_get_effective_owner_subjects_prefers_runtime_acl(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    acl_path = tmp_path / "acl.json"
    acl_path.write_text('{"owner":["312322764","p0lrd"]}', encoding="utf-8")
    monkeypatch.setattr(config, "USERBOT_ACL_FILE", acl_path, raising=False)
    monkeypatch.setattr(config, "OWNER_USERNAME", "@yung_nagato", raising=False)
    assert get_effective_owner_subjects() == ["312322764", "p0lrd"]
    assert get_effective_owner_label() == "312322764, p0lrd"


def test_resolve_access_profile_maps_legacy_allowed_users_to_full(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(config, "USERBOT_ACL_FILE", tmp_path / "acl.json", raising=False)
    monkeypatch.setattr(config, "FULL_ACCESS_USERS", ["trusted_user"], raising=False)
    monkeypatch.setattr(config, "PARTIAL_ACCESS_USERS", [], raising=False)
    monkeypatch.setattr(config, "OWNER_USER_IDS", [], raising=False)
    profile = resolve_access_profile(user_id=42, username="trusted_user", self_user_id=777)
    assert profile.level == AccessLevel.FULL


def test_resolve_access_profile_supports_partial_acl_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    acl_path = tmp_path / "acl.json"
    acl_path.write_text('{"partial":["@reader"]}', encoding="utf-8")
    monkeypatch.setattr(config, "USERBOT_ACL_FILE", acl_path, raising=False)
    monkeypatch.setattr(config, "FULL_ACCESS_USERS", [], raising=False)
    monkeypatch.setattr(config, "PARTIAL_ACCESS_USERS", [], raising=False)
    monkeypatch.setattr(config, "OWNER_USER_IDS", [], raising=False)
    profile = resolve_access_profile(user_id=55, username="reader", self_user_id=777)
    assert profile.level == AccessLevel.PARTIAL


def test_partial_profile_can_execute_only_safe_commands(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    acl_path = tmp_path / "acl.json"
    acl_path.write_text('{"partial":["reader"]}', encoding="utf-8")
    monkeypatch.setattr(config, "USERBOT_ACL_FILE", acl_path, raising=False)
    monkeypatch.setattr(config, "FULL_ACCESS_USERS", [], raising=False)
    monkeypatch.setattr(config, "PARTIAL_ACCESS_USERS", [], raising=False)
    monkeypatch.setattr(config, "OWNER_USER_IDS", [], raising=False)
    profile = resolve_access_profile(user_id=55, username="reader", self_user_id=777)

    assert profile.level == AccessLevel.PARTIAL
    assert PARTIAL_ACCESS_COMMANDS == {"help", "search", "status"}
    assert profile.can_execute_command("status", {"status", "help", "search", "write"}) is True
    assert profile.can_execute_command("write", {"status", "help", "search", "write"}) is False


def test_full_profile_cannot_execute_owner_only_commands(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    acl_path = tmp_path / "acl.json"
    acl_path.write_text('{"full":["trusted"]}', encoding="utf-8")
    monkeypatch.setattr(config, "USERBOT_ACL_FILE", acl_path, raising=False)
    monkeypatch.setattr(config, "FULL_ACCESS_USERS", [], raising=False)
    monkeypatch.setattr(config, "PARTIAL_ACCESS_USERS", [], raising=False)
    monkeypatch.setattr(config, "OWNER_USER_IDS", [], raising=False)
    profile = resolve_access_profile(user_id=55, username="trusted", self_user_id=777)

    assert profile.level == AccessLevel.FULL
    assert OWNER_ONLY_COMMANDS == {
        "access",
        "acl",
        "reasoning",
        "restart",
        "set",
        "codex",
        "gemini",
        "claude_cli",
        "opencode",
        "hs",
        "cap",
        "chatban",
    }
    assert profile.can_execute_command("status", set(USERBOT_KNOWN_COMMANDS)) is True
    assert profile.can_execute_command("acl", set(USERBOT_KNOWN_COMMANDS)) is False
    assert profile.can_execute_command("reasoning", set(USERBOT_KNOWN_COMMANDS)) is False
    assert profile.can_execute_command("restart", set(USERBOT_KNOWN_COMMANDS)) is False


def test_build_command_access_matrix_marks_owner_only_and_partial_counts() -> None:
    matrix = build_command_access_matrix()

    assert matrix["summary"]["owner_only_count"] == 12
    assert matrix["roles"]["owner"]["command_count"] == len(USERBOT_KNOWN_COMMANDS)
    assert "acl" in matrix["roles"]["owner"]["commands"]
    assert "reasoning" in matrix["roles"]["owner"]["commands"]
    assert "acl" not in matrix["roles"]["full"]["commands"]
    assert "reasoning" not in matrix["roles"]["full"]["commands"]
    assert matrix["roles"]["partial"]["commands"] == ["help", "search", "status"]


def test_update_acl_subject_adds_and_removes_runtime_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    acl_path = tmp_path / "acl.json"
    monkeypatch.setattr(config, "USERBOT_ACL_FILE", acl_path, raising=False)

    add_result = update_acl_subject("full", "@reader", add=True)
    assert add_result["changed"] is True
    assert load_acl_runtime_state()["full"] == ["reader"]

    noop_result = update_acl_subject("full", "@reader", add=True)
    assert noop_result["changed"] is False
    assert load_acl_runtime_state()["full"] == ["reader"]

    revoke_result = update_acl_subject("full", "@reader", add=False)
    assert revoke_result["changed"] is True
    assert load_acl_runtime_state()["full"] == []


def test_update_acl_subject_rejects_unsupported_level(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "USERBOT_ACL_FILE", tmp_path / "acl.json", raising=False)
    with pytest.raises(ValueError, match="unsupported_acl_level"):
        update_acl_subject("guest", "@reader", add=True)
