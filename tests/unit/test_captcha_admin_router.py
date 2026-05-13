# -*- coding: utf-8 -*-
"""
Тесты ``src.modules.web_routers.captcha_admin_router`` — Wave 220.

State-файлы (``spam_whitelist.json``, ``spam_banned_users.json``,
``spam_filter_config.json``) подменяются на tmp_path через monkeypatch
module-attributes, чтобы тесты не трогали реальный runtime state.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from src.modules.web_routers import captcha_admin_router as car
from src.modules.web_routers._context import RouterContext
from src.modules.web_routers.captcha_admin_router import build_captcha_admin_router


@pytest.fixture
def isolated_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Изолирует все captcha-related state-файлы на tmp_path."""
    monkeypatch.setattr(car, "_WHITELIST_PATH", tmp_path / "spam_whitelist.json")
    monkeypatch.setattr(car, "_BANNED_USERS_PATH", tmp_path / "spam_banned_users.json")
    monkeypatch.setattr(car, "_SPAM_CONFIG_PATH", tmp_path / "spam_filter_config.json")
    monkeypatch.setattr(car, "_DEFAULT_LOG_FILE", tmp_path / "krab_main.log")
    monkeypatch.setenv("KRAB_LOG_FILE", str(tmp_path / "krab_main.log"))
    return tmp_path


def _make_client(*, write_access_raises: Exception | None = None) -> TestClient:
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
    app.include_router(build_captcha_admin_router(ctx))
    return TestClient(app)


# ── sanitize / validation ──────────────────────────────────────────────────


def test_validate_user_id_accepts_positive() -> None:
    assert car._validate_user_id(12345) == 12345
    assert car._validate_user_id("99") == 99


def test_validate_user_id_rejects_zero_and_garbage() -> None:
    with pytest.raises(HTTPException) as exc:
        car._validate_user_id(0)
    assert exc.value.status_code == 400
    for bad in ("abc", "12.3", "1e5", "", " "):
        with pytest.raises(HTTPException):
            car._validate_user_id(bad)


def test_validate_username_lowercases_and_strips_at() -> None:
    assert car._validate_username("@FooBar") == "foobar"
    assert car._validate_username("Pavel_123") == "pavel_123"


def test_validate_username_rejects_short_and_special() -> None:
    for bad in ("ab", "x", "no-dash", "with space", "точка.x"):
        with pytest.raises(HTTPException):
            car._validate_username(bad)


def test_sanitize_preview_truncates() -> None:
    long = "a" * 200
    out = car._sanitize_preview(long)
    assert len(out) <= car._PREVIEW_MAX_CHARS + 1  # +1 for ellipsis
    assert out.endswith("…")


def test_sanitize_preview_collapses_whitespace() -> None:
    assert car._sanitize_preview("hello\n\n  world") == "hello world"
    assert car._sanitize_preview("") == ""


# ── GET /api/admin/captcha/list ─────────────────────────────────────────────


def test_list_empty_state_returns_ok(isolated_state: Path) -> None:
    client = _make_client()
    resp = client.get("/api/admin/captcha/list")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["count_recent"] == 0
    assert body["recent"] == []
    assert body["whitelist_count"] == 0
    assert body["banned_count"] == 0
    assert body["settings"]["active"] is False
    assert len(body["daily_stats"]) == 7


def test_list_reads_recent_spam_events_from_log(isolated_state: Path) -> None:
    log = isolated_state / "krab_main.log"
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    lines = [
        json.dumps(
            {
                "event": "spam_detected",
                "timestamp": now_iso,
                "chat_id": "-1001",
                "user_id": "5555",
                "reason": "flood",
                "action": "delete",
                "preview": "buy crypto now and earn 1000% per day click here",
            }
        ),
        json.dumps({"event": "other_event", "msg": "ignore me"}),
        json.dumps(
            {
                "event": "spam_detected",
                "timestamp": now_iso,
                "chat_id": "-1002",
                "user_id": "7777",
                "reason": "links",
                "action": "ban",
            }
        ),
    ]
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")

    client = _make_client()
    resp = client.get("/api/admin/captcha/list")
    body = resp.json()
    assert body["count_recent"] == 2
    reasons = sorted(c["reason"] for c in body["recent"])
    assert reasons == ["flood", "links"]
    flood = next(c for c in body["recent"] if c["reason"] == "flood")
    # PII protection: длинный preview обрезан.
    assert len(flood["preview"]) <= car._PREVIEW_MAX_CHARS + 1
    assert flood["preview"].endswith("…")


def test_list_settings_aggregates_chats(isolated_state: Path) -> None:
    cfg_path = isolated_state / "spam_filter_config.json"
    cfg_path.write_text(
        json.dumps(
            {
                "-100111": {"enabled": True, "action": "ban"},
                "-100222": {"enabled": False, "action": "delete"},
                "-100333": {"enabled": True, "action": "mute"},
            }
        ),
        encoding="utf-8",
    )

    client = _make_client()
    body = client.get("/api/admin/captcha/list").json()
    s = body["settings"]
    assert s["active"] is True
    assert s["active_chats_count"] == 2
    assert s["total_chats_count"] == 3
    assert s["flood_msg_limit"] == 5


# ── POST /api/admin/captcha/whitelist/add ──────────────────────────────────


def test_whitelist_add_by_user_id_persists(isolated_state: Path) -> None:
    client = _make_client()
    resp = client.post(
        "/api/admin/captcha/whitelist/add",
        json={"user_id": 12345, "note": "trusted ally"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["key"] == "12345"

    stored = json.loads(car._WHITELIST_PATH.read_text(encoding="utf-8"))
    assert "12345" in stored
    assert stored["12345"]["note"] == "trusted ally"


def test_whitelist_add_by_username_lowercases(isolated_state: Path) -> None:
    client = _make_client()
    resp = client.post(
        "/api/admin/captcha/whitelist/add",
        json={"username": "@FooBar"},
    )
    body = resp.json()
    assert body["ok"] is True
    assert body["key"] == "foobar"


def test_whitelist_add_requires_id_or_username(isolated_state: Path) -> None:
    client = _make_client()
    resp = client.post("/api/admin/captcha/whitelist/add", json={"note": "no key"})
    assert resp.status_code == 400


def test_whitelist_add_blocked_without_write_access(isolated_state: Path) -> None:
    client = _make_client(write_access_raises=HTTPException(status_code=403, detail="forbidden"))
    resp = client.post(
        "/api/admin/captcha/whitelist/add",
        json={"user_id": 42},
    )
    assert resp.status_code == 403


# ── POST /api/admin/captcha/whitelist/remove ───────────────────────────────


def test_whitelist_remove_existing(isolated_state: Path) -> None:
    car._save_json(car._WHITELIST_PATH, {"99": {"user_id": 99}})
    client = _make_client()
    resp = client.post(
        "/api/admin/captcha/whitelist/remove",
        json={"user_id": 99},
    )
    assert resp.status_code == 200
    assert json.loads(car._WHITELIST_PATH.read_text(encoding="utf-8")) == {}


def test_whitelist_remove_404_when_missing(isolated_state: Path) -> None:
    client = _make_client()
    resp = client.post(
        "/api/admin/captcha/whitelist/remove",
        json={"user_id": 9999},
    )
    assert resp.status_code == 404


# ── POST /api/admin/captcha/ban/{user_id} ──────────────────────────────────


def test_ban_user_persists(isolated_state: Path) -> None:
    client = _make_client()
    resp = client.post("/api/admin/captcha/ban/77777", params={"note": "scam"})
    body = resp.json()
    assert body["ok"] is True
    assert body["user_id"] == 77777
    stored = json.loads(car._BANNED_USERS_PATH.read_text(encoding="utf-8"))
    assert "77777" in stored
    assert stored["77777"]["note"] == "scam"


def test_ban_user_rejects_bad_id(isolated_state: Path) -> None:
    client = _make_client()
    resp = client.post("/api/admin/captcha/ban/notanumber")
    assert resp.status_code == 400


def test_ban_user_blocked_without_write_access(isolated_state: Path) -> None:
    client = _make_client(write_access_raises=HTTPException(status_code=403, detail="forbidden"))
    resp = client.post("/api/admin/captcha/ban/42")
    assert resp.status_code == 403


def test_unban_user_removes_entry(isolated_state: Path) -> None:
    car._save_json(car._BANNED_USERS_PATH, {"55": {"user_id": 55, "banned_ts": 1.0}})
    client = _make_client()
    resp = client.post("/api/admin/captcha/unban/55")
    assert resp.status_code == 200
    assert json.loads(car._BANNED_USERS_PATH.read_text(encoding="utf-8")) == {}


def test_unban_user_404_when_missing(isolated_state: Path) -> None:
    client = _make_client()
    resp = client.post("/api/admin/captcha/unban/12345")
    assert resp.status_code == 404


# ── POST /api/admin/captcha/reset/{chat_id} ────────────────────────────────


def test_reset_chat_state_pops_entry(isolated_state: Path) -> None:
    from src.core import spam_guard

    # Засаживаем "-1001" в флоудтрекер.
    spam_guard._flood_tracker["-1001"][111].append(time.monotonic())
    client = _make_client()
    resp = client.post("/api/admin/captcha/reset/-1001")
    body = resp.json()
    assert body["ok"] is True
    assert body["reset"] is True
    assert "-1001" not in spam_guard._flood_tracker


def test_reset_chat_noop_when_no_state(isolated_state: Path) -> None:
    client = _make_client()
    resp = client.post("/api/admin/captcha/reset/-1009")
    body = resp.json()
    assert body["ok"] is True
    assert body["reset"] is False


# ── GET /admin/captcha — HTML page ─────────────────────────────────────────


def test_admin_captcha_page_returns_html(isolated_state: Path) -> None:
    client = _make_client()
    resp = client.get("/admin/captcha")
    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "")
    assert "Captcha" in resp.text
    assert "/api/admin/captcha/list" in resp.text
    assert "/api/admin/captcha/whitelist/add" in resp.text
    # XSS-safe rendering hints — никаких innerHTML с user data.
    assert "innerHTML" not in resp.text or "textContent" in resp.text
