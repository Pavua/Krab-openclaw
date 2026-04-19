# -*- coding: utf-8 -*-
"""
Тесты unified owner detection (Wave 29-KK).

Проверяем is_owner_user_id() — единая точка истины для owner-check:
env var OWNER_USER_IDS + ACL-файл owner-секция, приоритет у ACL.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.config import config
from src.core.access_control import is_owner_user_id


def _write_acl(path: Path, owner_ids: list[str]) -> None:
    """Пишет минимальный ACL-файл с заданными owner-id."""
    path.write_text(
        json.dumps({"owner": owner_ids, "full": [], "partial": []}, ensure_ascii=False),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# env-only: OWNER_USER_IDS содержит id
# ---------------------------------------------------------------------------


def test_owner_via_env_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """env OWNER_USER_IDS=123 — owner=True для 123."""
    acl_path = tmp_path / "acl.json"
    monkeypatch.setattr(config, "OWNER_USER_IDS", ["123"], raising=False)
    # ACL-файл не существует — env должен сработать
    assert is_owner_user_id(123, path=acl_path) is True


def test_non_owner_via_env_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """env OWNER_USER_IDS=123 — owner=False для 999."""
    acl_path = tmp_path / "acl.json"
    monkeypatch.setattr(config, "OWNER_USER_IDS", ["123"], raising=False)
    assert is_owner_user_id(999, path=acl_path) is False


# ---------------------------------------------------------------------------
# ACL-only: ACL-файл содержит owner, env пуст
# ---------------------------------------------------------------------------


def test_owner_via_acl_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ACL json owner=[456] — owner=True для 456, env пуст."""
    acl_path = tmp_path / "acl.json"
    _write_acl(acl_path, ["456"])
    monkeypatch.setattr(config, "OWNER_USER_IDS", [], raising=False)
    assert is_owner_user_id(456, path=acl_path) is True


def test_non_owner_acl_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ACL json owner=[456] — owner=False для 123, env пуст."""
    acl_path = tmp_path / "acl.json"
    _write_acl(acl_path, ["456"])
    monkeypatch.setattr(config, "OWNER_USER_IDS", [], raising=False)
    assert is_owner_user_id(123, path=acl_path) is False


# ---------------------------------------------------------------------------
# Both set with different IDs: оба источника работают
# ---------------------------------------------------------------------------


def test_owner_both_sources_env_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """env id=100, ACL id=200 — оба owner=True."""
    acl_path = tmp_path / "acl.json"
    _write_acl(acl_path, ["200"])
    monkeypatch.setattr(config, "OWNER_USER_IDS", ["100"], raising=False)
    # env id проходит через ACL-проверку (не в ACL) → падает в env
    assert is_owner_user_id(100, path=acl_path) is True


def test_owner_both_sources_acl_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """env id=100, ACL id=200 — ACL id тоже True."""
    acl_path = tmp_path / "acl.json"
    _write_acl(acl_path, ["200"])
    monkeypatch.setattr(config, "OWNER_USER_IDS", ["100"], raising=False)
    assert is_owner_user_id(200, path=acl_path) is True


def test_non_owner_both_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """env id=100, ACL id=200 — третий id=999 не owner."""
    acl_path = tmp_path / "acl.json"
    _write_acl(acl_path, ["200"])
    monkeypatch.setattr(config, "OWNER_USER_IDS", ["100"], raising=False)
    assert is_owner_user_id(999, path=acl_path) is False


# ---------------------------------------------------------------------------
# Neither set: ни env, ни ACL — owner=False
# ---------------------------------------------------------------------------


def test_neither_source_returns_false(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ни env, ни ACL не содержат id — owner=False."""
    acl_path = tmp_path / "acl.json"
    monkeypatch.setattr(config, "OWNER_USER_IDS", [], raising=False)
    # ACL-файл не существует
    assert is_owner_user_id(123, path=acl_path) is False


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_zero_user_id_returns_false(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """user_id=0 — owner=False (guard против falsy значений)."""
    acl_path = tmp_path / "acl.json"
    monkeypatch.setattr(config, "OWNER_USER_IDS", ["0"], raising=False)
    assert is_owner_user_id(0, path=acl_path) is False


def test_string_user_id_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """user_id передан строкой — функция принимает int|str."""
    acl_path = tmp_path / "acl.json"
    _write_acl(acl_path, ["789"])
    monkeypatch.setattr(config, "OWNER_USER_IDS", [], raising=False)
    assert is_owner_user_id("789", path=acl_path) is True


def test_acl_priority_over_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ACL-файл имеет приоритет: id в ACL работает независимо от env."""
    acl_path = tmp_path / "acl.json"
    _write_acl(acl_path, ["300"])
    # env содержит ДРУГОЙ id
    monkeypatch.setattr(config, "OWNER_USER_IDS", ["400"], raising=False)
    # ACL-id работает
    assert is_owner_user_id(300, path=acl_path) is True
    # env-id тоже работает (ACL не блокирует env)
    assert is_owner_user_id(400, path=acl_path) is True
