# -*- coding: utf-8 -*-
"""
Тесты deprecation warning для OWNER_USER_IDS env var (Session 15, priority 10).

Проверяем:
- emit_deprecation_warnings() логирует warning при наличии OWNER_USER_IDS в env
- при пустом env — warning не эмитируется
- backward compat: OWNER_USER_IDS всё ещё даёт owner access через is_owner_user_id
- ACL-json-only: is_owner_user_id работает без env
"""

from __future__ import annotations

import json
import logging
import os
import warnings
from pathlib import Path

import pytest

from src.config import config, emit_deprecation_warnings
from src.core.access_control import is_owner_user_id


def _write_acl(path: Path, owner_ids: list[str]) -> None:
    """Записывает минимальный ACL-файл."""
    path.write_text(
        json.dumps({"owner": owner_ids, "full": [], "partial": []}, ensure_ascii=False),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# emit_deprecation_warnings: warning при установленном env var
# ---------------------------------------------------------------------------


def test_deprecation_warning_emitted_when_env_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """emit_deprecation_warnings() эмитирует DeprecationWarning если OWNER_USER_IDS в env."""
    monkeypatch.setattr(config, "OWNER_USER_IDS", ["123456"], raising=False)
    monkeypatch.setenv("OWNER_USER_IDS", "123456")

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        emit_deprecation_warnings()

    deprecation_warns = [w for w in caught if issubclass(w.category, DeprecationWarning)]
    assert len(deprecation_warns) >= 1
    assert "OWNER_USER_IDS" in str(deprecation_warns[0].message)
    assert "Session 20" in str(deprecation_warns[0].message)


def test_deprecation_warning_logged_when_env_set(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """emit_deprecation_warnings() пишет warning в лог при установленном env."""
    monkeypatch.setattr(config, "OWNER_USER_IDS", ["111222"], raising=False)
    monkeypatch.setenv("OWNER_USER_IDS", "111222")

    with caplog.at_level(logging.WARNING, logger="src.config"):
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            emit_deprecation_warnings()

    assert any("owner_user_ids_env_deprecated" in r.message for r in caplog.records)


def test_no_warning_when_env_not_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """emit_deprecation_warnings() НЕ эмитирует DeprecationWarning если OWNER_USER_IDS пуст."""
    monkeypatch.setattr(config, "OWNER_USER_IDS", [], raising=False)
    monkeypatch.delenv("OWNER_USER_IDS", raising=False)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        emit_deprecation_warnings()

    deprecation_warns = [w for w in caught if issubclass(w.category, DeprecationWarning)]
    assert len(deprecation_warns) == 0


def test_no_warning_when_env_empty_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """emit_deprecation_warnings() не эмитирует warning при OWNER_USER_IDS='' (пустой строке)."""
    monkeypatch.setattr(config, "OWNER_USER_IDS", [], raising=False)
    monkeypatch.setenv("OWNER_USER_IDS", "")

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        emit_deprecation_warnings()

    deprecation_warns = [w for w in caught if issubclass(w.category, DeprecationWarning)]
    assert len(deprecation_warns) == 0


# ---------------------------------------------------------------------------
# Backward compat: OWNER_USER_IDS env var всё ещё даёт owner access
# ---------------------------------------------------------------------------


def test_backward_compat_env_still_grants_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OWNER_USER_IDS env var deprecated, но owner access через него всё ещё работает."""
    acl_path = tmp_path / "acl.json"
    monkeypatch.setattr(config, "OWNER_USER_IDS", ["555666"], raising=False)
    # ACL-файл пустой — только env
    assert is_owner_user_id(555666, path=acl_path) is True


def test_backward_compat_env_non_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Не owner ID не проходит при env-only check."""
    acl_path = tmp_path / "acl.json"
    monkeypatch.setattr(config, "OWNER_USER_IDS", ["555666"], raising=False)
    assert is_owner_user_id(999888, path=acl_path) is False


# ---------------------------------------------------------------------------
# ACL-only: работает без env (предпочтительный новый путь)
# ---------------------------------------------------------------------------


def test_acl_only_grants_owner_without_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ACL-json owner section работает без OWNER_USER_IDS env var."""
    acl_path = tmp_path / "acl.json"
    _write_acl(acl_path, ["777888"])
    monkeypatch.setattr(config, "OWNER_USER_IDS", [], raising=False)
    monkeypatch.delenv("OWNER_USER_IDS", raising=False)

    assert is_owner_user_id(777888, path=acl_path) is True


def test_acl_only_no_env_no_deprecation_warning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """При миграции на ACL-only deprecation warning не эмитируется."""
    acl_path = tmp_path / "acl.json"
    _write_acl(acl_path, ["777888"])
    monkeypatch.setattr(config, "OWNER_USER_IDS", [], raising=False)
    monkeypatch.delenv("OWNER_USER_IDS", raising=False)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        emit_deprecation_warnings()

    deprecation_warns = [w for w in caught if issubclass(w.category, DeprecationWarning)]
    assert len(deprecation_warns) == 0
    # ACL check при этом работает
    assert is_owner_user_id(777888, path=acl_path) is True
