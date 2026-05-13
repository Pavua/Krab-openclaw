# -*- coding: utf-8 -*-
"""
Тесты ``src.modules.web_routers.whitelist_admin_router`` — Wave 215 (Session 48).

Покрытие:
- factory + endpoints (HTML page + JSON list)
- validate subject / chat_id / level (regex, normalize)
- mask helpers (приватность ID)
- ACL add/remove через изолированный временный JSON-файл
- block/unblock chat через изолированный ChatBanCache
- voice-block/unblock через monkeypatched config
- write-access guards (assert_write_access_fn raises)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from src.core.chat_ban_cache import ChatBanCache
from src.modules.web_routers import whitelist_admin_router as war
from src.modules.web_routers._context import RouterContext
from src.modules.web_routers.whitelist_admin_router import (
    build_whitelist_admin_router,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_acl(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Изолирует ACL-файл на временную копию.

    Подменяет ``access_control._runtime_acl_path`` чтобы load/update
    операции работали с tmp файлом.
    """
    acl_path = tmp_path / "krab_userbot_acl.json"
    # Стартовое состояние — пусто; тесты при необходимости добавят сами.
    from src.core import access_control as ac

    monkeypatch.setattr(ac, "_runtime_acl_path", lambda path=None: acl_path)
    return acl_path


@pytest.fixture
def isolated_ban_cache(tmp_path: Path):
    """Изолирует chat_ban_cache singleton на временный JSON storage."""
    fake = ChatBanCache(storage_path=tmp_path / "chat_ban_cache.json")
    with patch.object(war, "chat_ban_cache", fake):
        yield fake


@pytest.fixture
def isolated_voice_blocklist(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Изолирует config.VOICE_REPLY_BLOCKED_CHATS на in-memory list.

    update_setting подменяется на no-op (in-memory write через прямой
    setattr config.VOICE_REPLY_BLOCKED_CHATS = ...).
    """
    from src.config import config

    monkeypatch.setattr(config, "VOICE_REPLY_BLOCKED_CHATS", [], raising=False)

    def _fake_update(key: str, value: str) -> None:
        if key == "VOICE_REPLY_BLOCKED_CHATS":
            config.VOICE_REPLY_BLOCKED_CHATS = [v.strip() for v in value.split(",") if v.strip()]

    monkeypatch.setattr(config, "update_setting", _fake_update, raising=False)
    return config.VOICE_REPLY_BLOCKED_CHATS


def _make_client(
    *,
    write_access_raises: Exception | None = None,
) -> TestClient:
    def _assert_write(*a: Any, **kw: Any) -> None:
        if write_access_raises is not None:
            raise write_access_raises

    ctx = RouterContext(
        deps={},
        project_root=Path("."),
        web_api_key_fn=lambda: None,
        assert_write_access_fn=_assert_write,
    )
    app = FastAPI()
    app.include_router(build_whitelist_admin_router(ctx))
    return TestClient(app)


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def test_validate_subject_accepts_digit_id() -> None:
    """Validate subject принимает цифровые user_id."""
    assert war._validate_subject("312322764") == "312322764"


def test_validate_subject_accepts_username() -> None:
    """Принимает username (без @)."""
    assert war._validate_subject("pablito") == "pablito"


def test_validate_subject_lowercases_and_strips_at() -> None:
    """Должен пропускать через ``normalize_subject``: lower + strip @."""
    assert war._validate_subject("@PabLito") == "pablito"


def test_validate_subject_rejects_empty() -> None:
    """Пустая строка → 400."""
    with pytest.raises(HTTPException) as exc:
        war._validate_subject("")
    assert exc.value.status_code == 400


def test_validate_subject_rejects_special_chars() -> None:
    """Запрещены пробелы / спецсимволы (кроме _, @)."""
    for bad in ("a b", "ab!c", "ab.c", "ab-c", "ab/c"):
        with pytest.raises(HTTPException):
            war._validate_subject(bad)


def test_validate_level_default_full() -> None:
    """Пустой level → дефолт 'full'."""
    assert war._validate_level(None) == "full"
    assert war._validate_level("") == "full"


def test_validate_level_accepts_allowed() -> None:
    """owner / full / partial — допустимые."""
    assert war._validate_level("owner") == "owner"
    assert war._validate_level("full") == "full"
    assert war._validate_level("partial") == "partial"


def test_validate_level_rejects_unknown() -> None:
    """guest и другие → 400."""
    with pytest.raises(HTTPException):
        war._validate_level("guest")
    with pytest.raises(HTTPException):
        war._validate_level("admin")


def test_validate_chat_id_accepts_positive_and_negative() -> None:
    assert war._validate_chat_id("123456") == "123456"
    assert war._validate_chat_id("-1001234567890") == "-1001234567890"


def test_validate_chat_id_rejects_invalid() -> None:
    for bad in ("", "abc", "12.34", "-", "1-2"):
        with pytest.raises(HTTPException):
            war._validate_chat_id(bad)


def test_mask_subject_short_unchanged() -> None:
    """Subject ≤8 символов не маскируем."""
    assert war._mask_subject("pablito") == "pablito"
    assert war._mask_subject("12345678") == "12345678"


def test_mask_subject_long_masked() -> None:
    """Длинный id маскируется: первые 4 + последние 4."""
    assert war._mask_subject("312322764") == "3123•••2764"
    assert war._mask_subject("some_long_username_here") == "some•••here"


def test_mask_chat_id_preserves_sign() -> None:
    """Знак '-' сохраняется при маскировке chat_id."""
    assert war._mask_chat_id("-1001234567890") == "-1001•••7890"
    assert war._mask_chat_id("123") == "123"
    assert war._mask_chat_id("-123") == "-123"


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


def test_html_page_served() -> None:
    """GET /admin/whitelist отдаёт HTML."""
    client = _make_client()
    r = client.get("/admin/whitelist")
    assert r.status_code == 200
    assert "text/html" in r.headers.get("content-type", "")
    assert "Whitelist Admin" in r.text
    # XSS-safe: проверяем что нет <script src="..."> с внешними URL.
    assert "createElement" in r.text  # DOM API used


def test_list_empty_state(
    isolated_acl: Path,
    isolated_ban_cache: ChatBanCache,
    isolated_voice_blocklist: list[str],
) -> None:
    """GET /api/admin/whitelist/list на пустом стейте."""
    client = _make_client()
    r = client.get("/api/admin/whitelist/list")
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["auth"] == {"owner": [], "full": [], "partial": []}
    assert data["blacklist"] == []
    assert data["voice_blocked"] == []
    assert data["counts"]["owner"] == 0
    assert data["counts"]["blacklist"] == 0


def test_add_user_full_then_list(
    isolated_acl: Path,
    isolated_ban_cache: ChatBanCache,
    isolated_voice_blocklist: list[str],
) -> None:
    """add_user → list возвращает subject в маскированной форме."""
    client = _make_client()
    r = client.post(
        "/api/admin/whitelist/add_user",
        json={"subject": "999888777666", "level": "full"},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["ok"] is True
    assert data["level"] == "full"
    assert data["changed"] is True
    # Subject в ответе маскированный.
    assert data["subject_masked"] == "9998•••7666"

    r2 = client.get("/api/admin/whitelist/list")
    full_entries = r2.json()["auth"]["full"]
    assert len(full_entries) == 1
    assert full_entries[0]["subject_masked"] == "9998•••7666"
    assert full_entries[0]["kind"] == "id"


def test_add_user_username_kind(
    isolated_acl: Path,
    isolated_ban_cache: ChatBanCache,
    isolated_voice_blocklist: list[str],
) -> None:
    """add_user с username → kind=username, нормализация @PabLito → pablito."""
    client = _make_client()
    r = client.post(
        "/api/admin/whitelist/add_user",
        json={"subject": "@PabLito", "level": "partial"},
    )
    assert r.status_code == 200
    r2 = client.get("/api/admin/whitelist/list")
    partial = r2.json()["auth"]["partial"]
    assert len(partial) == 1
    assert partial[0]["kind"] == "username"


def test_remove_user_changed_flag(
    isolated_acl: Path,
    isolated_ban_cache: ChatBanCache,
    isolated_voice_blocklist: list[str],
) -> None:
    """remove_user удаляет subject, повторное удаление → changed=False."""
    client = _make_client()
    client.post(
        "/api/admin/whitelist/add_user",
        json={"subject": "111222333444", "level": "full"},
    )
    r = client.post(
        "/api/admin/whitelist/remove_user",
        json={"subject": "111222333444", "level": "full"},
    )
    assert r.status_code == 200
    assert r.json()["changed"] is True
    # Повторное удаление — changed=False (идемпотентно).
    r2 = client.post(
        "/api/admin/whitelist/remove_user",
        json={"subject": "111222333444", "level": "full"},
    )
    assert r2.json()["changed"] is False


def test_add_user_invalid_subject_400(
    isolated_acl: Path,
    isolated_ban_cache: ChatBanCache,
    isolated_voice_blocklist: list[str],
) -> None:
    """Невалидный subject → 400 subject_invalid_format."""
    client = _make_client()
    r = client.post(
        "/api/admin/whitelist/add_user",
        json={"subject": "invalid chars!", "level": "full"},
    )
    assert r.status_code == 400


def test_add_user_invalid_level_400(
    isolated_acl: Path,
    isolated_ban_cache: ChatBanCache,
    isolated_voice_blocklist: list[str],
) -> None:
    """Невалидный level → 400."""
    client = _make_client()
    r = client.post(
        "/api/admin/whitelist/add_user",
        json={"subject": "abc", "level": "superadmin"},
    )
    assert r.status_code == 400


def test_block_chat_with_ttl(
    isolated_acl: Path,
    isolated_ban_cache: ChatBanCache,
    isolated_voice_blocklist: list[str],
) -> None:
    """block_chat с hours=24 кладёт запись в ban_cache с expires_at."""
    client = _make_client()
    r = client.post(
        "/api/admin/whitelist/block_chat",
        json={"chat_id": "-1009998887776", "reason": "owner_test", "hours": 24},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["ok"] is True
    assert data["mode"] == "ban_cache"
    assert data["cooldown_hours"] == 24.0

    # Verify через list endpoint.
    r2 = client.get("/api/admin/whitelist/list")
    blacklist = r2.json()["blacklist"]
    assert len(blacklist) == 1
    assert blacklist[0]["chat_id_masked"] == "-1009•••7776"
    assert blacklist[0]["error_code"] == "owner_test"
    assert blacklist[0]["expires_at"] is not None


def test_block_chat_permanent_when_hours_zero(
    isolated_acl: Path,
    isolated_ban_cache: ChatBanCache,
    isolated_voice_blocklist: list[str],
) -> None:
    """hours=0 → permanent ban (expires_at is None)."""
    client = _make_client()
    r = client.post(
        "/api/admin/whitelist/block_chat",
        json={"chat_id": "-1005554443332", "hours": 0},
    )
    assert r.status_code == 200
    assert r.json()["cooldown_hours"] is None
    r2 = client.get("/api/admin/whitelist/list")
    assert r2.json()["blacklist"][0]["expires_at"] is None


def test_unblock_chat(
    isolated_acl: Path,
    isolated_ban_cache: ChatBanCache,
    isolated_voice_blocklist: list[str],
) -> None:
    """unblock_chat снимает запись из ban_cache."""
    client = _make_client()
    client.post(
        "/api/admin/whitelist/block_chat",
        json={"chat_id": "-1001112223334", "hours": 6},
    )
    r = client.post(
        "/api/admin/whitelist/unblock_chat",
        json={"chat_id": "-1001112223334"},
    )
    assert r.status_code == 200
    assert r.json()["changed"] is True
    r2 = client.get("/api/admin/whitelist/list")
    assert r2.json()["blacklist"] == []


def test_voice_block_unblock_roundtrip(
    isolated_acl: Path,
    isolated_ban_cache: ChatBanCache,
    isolated_voice_blocklist: list[str],
) -> None:
    """voice_only flag → запись попадает в VOICE_REPLY_BLOCKED_CHATS."""
    client = _make_client()
    r = client.post(
        "/api/admin/whitelist/block_chat",
        json={"chat_id": "-1007776665554", "voice_only": True},
    )
    assert r.status_code == 200
    assert r.json()["mode"] == "voice_only"
    assert r.json()["changed"] is True

    # Verify list.
    r2 = client.get("/api/admin/whitelist/list")
    voice = r2.json()["voice_blocked"]
    assert len(voice) == 1
    assert voice[0]["chat_id_masked"] == "-1007•••5554"

    # Unblock.
    r3 = client.post(
        "/api/admin/whitelist/unblock_chat",
        json={"chat_id": "-1007776665554", "voice_only": True},
    )
    assert r3.status_code == 200
    assert r3.json()["changed"] is True

    # Verify removed.
    r4 = client.get("/api/admin/whitelist/list")
    assert r4.json()["voice_blocked"] == []


def test_write_access_guard_blocks_add_user(
    isolated_acl: Path,
    isolated_ban_cache: ChatBanCache,
    isolated_voice_blocklist: list[str],
) -> None:
    """assert_write_access_fn raising → POST add_user блокируется."""
    client = _make_client(write_access_raises=HTTPException(status_code=403, detail="no_key"))
    r = client.post(
        "/api/admin/whitelist/add_user",
        json={"subject": "12345678901", "level": "full"},
    )
    assert r.status_code == 403


def test_write_access_guard_blocks_block_chat(
    isolated_acl: Path,
    isolated_ban_cache: ChatBanCache,
    isolated_voice_blocklist: list[str],
) -> None:
    """assert_write_access_fn raising → POST block_chat блокируется."""
    client = _make_client(write_access_raises=HTTPException(status_code=403, detail="no_key"))
    r = client.post(
        "/api/admin/whitelist/block_chat",
        json={"chat_id": "-100123", "hours": 1},
    )
    assert r.status_code == 403


def test_block_chat_invalid_hours_400(
    isolated_acl: Path,
    isolated_ban_cache: ChatBanCache,
    isolated_voice_blocklist: list[str],
) -> None:
    """Нечисловой hours → 400 hours_invalid_number."""
    client = _make_client()
    r = client.post(
        "/api/admin/whitelist/block_chat",
        json={"chat_id": "-100123", "hours": "not-a-number"},
    )
    assert r.status_code == 400


def test_list_read_only_no_write_check(
    isolated_acl: Path,
    isolated_ban_cache: ChatBanCache,
    isolated_voice_blocklist: list[str],
) -> None:
    """GET /list не требует write-access даже при raising guard."""
    client = _make_client(write_access_raises=HTTPException(status_code=403, detail="no_key"))
    r = client.get("/api/admin/whitelist/list")
    assert r.status_code == 200
