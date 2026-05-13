# -*- coding: utf-8 -*-
"""
Тесты ``src.modules.web_routers.silence_admin_router`` — Wave 199 (Session 48).

Тестируем factory-pattern + JSON эндпоинты + HTML. Все обращения к
silence_manager/silence_schedule_manager — через настоящие singleton'ы
из ``src.core.silence_mode`` и ``src.core.silence_schedule``: они
in-memory, между тестами state очищается через fixture.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from src.core.silence_mode import silence_manager
from src.core.silence_schedule import silence_schedule_manager
from src.modules.web_routers import silence_admin_router as sar
from src.modules.web_routers._context import RouterContext
from src.modules.web_routers.silence_admin_router import build_silence_admin_router

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


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
    app.include_router(build_silence_admin_router(ctx))
    return TestClient(app)


@pytest.fixture(autouse=True)
def _reset_silence_state() -> None:
    """Очищаем in-memory silence state и meta перед/после каждого теста."""
    silence_manager._chat_mutes.clear()  # noqa: SLF001
    silence_manager._global_until = None  # noqa: SLF001
    sar._silence_meta.clear()  # noqa: SLF001
    # Schedule — не трогаем persistent JSON, но восстанавливаем in-mem fields.
    prev_enabled = silence_schedule_manager._enabled  # noqa: SLF001
    prev_start = silence_schedule_manager._start_str  # noqa: SLF001
    prev_end = silence_schedule_manager._end_str  # noqa: SLF001
    silence_schedule_manager._enabled = False  # noqa: SLF001
    silence_schedule_manager._start_str = None  # noqa: SLF001
    silence_schedule_manager._end_str = None  # noqa: SLF001
    yield
    silence_manager._chat_mutes.clear()  # noqa: SLF001
    silence_manager._global_until = None  # noqa: SLF001
    sar._silence_meta.clear()  # noqa: SLF001
    silence_schedule_manager._enabled = prev_enabled  # noqa: SLF001
    silence_schedule_manager._start_str = prev_start  # noqa: SLF001
    silence_schedule_manager._end_str = prev_end  # noqa: SLF001


# ---------------------------------------------------------------------------
# Validators
# ---------------------------------------------------------------------------


def test_validate_chat_id_accepts_positive_and_negative() -> None:
    assert sar._validate_chat_id("123") == "123"
    assert sar._validate_chat_id("-1001234567890") == "-1001234567890"
    assert sar._validate_chat_id(456) == "456"


def test_validate_chat_id_rejects_garbage() -> None:
    for bad in ["", " ", "abc", "12 34", "../etc/passwd", None, "1e5"]:
        with pytest.raises(HTTPException) as exc:
            sar._validate_chat_id(bad)
        assert exc.value.status_code == 400


def test_validate_duration_defaults_when_missing() -> None:
    assert sar._validate_duration(None) == sar._DEFAULT_DURATION_MIN
    assert sar._validate_duration("") == sar._DEFAULT_DURATION_MIN


def test_validate_duration_rejects_invalid() -> None:
    with pytest.raises(HTTPException):
        sar._validate_duration("abc")
    with pytest.raises(HTTPException):
        sar._validate_duration(0)
    with pytest.raises(HTTPException):
        sar._validate_duration(-1)
    with pytest.raises(HTTPException):
        sar._validate_duration(sar._MAX_DURATION_MIN + 1)


def test_sanitize_reason_strips_angle_and_truncates() -> None:
    assert sar._sanitize_reason(None) == ""
    assert sar._sanitize_reason("  hello  ") == "hello"
    cleaned = sar._sanitize_reason("<script>alert(`xss`)</script>")
    assert "<" not in cleaned
    assert ">" not in cleaned
    assert "`" not in cleaned
    very_long = "a" * (sar._MAX_REASON_LEN + 50)
    assert len(sar._sanitize_reason(very_long)) == sar._MAX_REASON_LEN


# ---------------------------------------------------------------------------
# GET /api/admin/silence/list
# ---------------------------------------------------------------------------


def test_list_empty_state() -> None:
    client = _make_client()
    res = client.get("/api/admin/silence/list")
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["active"] == []
    assert data["scheduled"]["enabled"] is False
    assert data["stats"]["silenced_now"] == 0
    assert data["stats"]["global_muted"] is False


def test_list_includes_active_mute() -> None:
    silence_manager.mute_chat("-1001234567890", minutes=10)
    client = _make_client()
    res = client.get("/api/admin/silence/list")
    assert res.status_code == 200
    data = res.json()
    assert data["stats"]["silenced_now"] == 1
    assert data["stats"]["active_per_chat"] == 1
    assert len(data["active"]) == 1
    rec = data["active"][0]
    assert rec["chat_id"] == "-1001234567890"
    assert rec["remaining_sec"] > 0
    assert rec["remaining_min"] > 0


def test_list_includes_global_mute_in_silenced_now() -> None:
    silence_manager.mute_global(minutes=5)
    client = _make_client()
    res = client.get("/api/admin/silence/list")
    data = res.json()
    assert data["stats"]["global_muted"] is True
    assert data["stats"]["silenced_now"] == 1
    assert data["stats"]["global_remaining_min"] > 0


def test_list_includes_schedule() -> None:
    silence_schedule_manager._enabled = True  # noqa: SLF001
    silence_schedule_manager._start_str = "23:00"  # noqa: SLF001
    silence_schedule_manager._end_str = "08:00"  # noqa: SLF001
    client = _make_client()
    res = client.get("/api/admin/silence/list")
    data = res.json()
    assert data["scheduled"]["enabled"] is True
    assert data["scheduled"]["start"] == "23:00"
    assert data["scheduled"]["end"] == "08:00"
    assert data["stats"]["scheduled_window"] == "23:00–08:00"


# ---------------------------------------------------------------------------
# POST /api/admin/silence/add
# ---------------------------------------------------------------------------


def test_add_mute_success() -> None:
    client = _make_client()
    res = client.post(
        "/api/admin/silence/add",
        json={"chat_id": "-1001234567890", "duration_minutes": 15, "reason": "test"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["chat_id"] == "-1001234567890"
    assert data["duration_minutes"] == 15
    assert data["reason"] == "test"
    assert silence_manager.is_chat_muted("-1001234567890") is True


def test_add_mute_default_duration() -> None:
    client = _make_client()
    res = client.post("/api/admin/silence/add", json={"chat_id": "555"})
    assert res.status_code == 200
    assert res.json()["duration_minutes"] == sar._DEFAULT_DURATION_MIN


def test_add_mute_validates_chat_id() -> None:
    client = _make_client()
    res = client.post("/api/admin/silence/add", json={"chat_id": "not-a-number"})
    assert res.status_code == 400
    assert "silence_chat_id_invalid" in res.json()["detail"]


def test_add_mute_validates_duration() -> None:
    client = _make_client()
    res = client.post(
        "/api/admin/silence/add",
        json={"chat_id": "123", "duration_minutes": -5},
    )
    assert res.status_code == 400


def test_add_mute_requires_write_access() -> None:
    client = _make_client(
        write_access_raises=HTTPException(status_code=403, detail="forbidden"),
    )
    res = client.post("/api/admin/silence/add", json={"chat_id": "123"})
    assert res.status_code == 403


# ---------------------------------------------------------------------------
# POST /api/admin/silence/remove
# ---------------------------------------------------------------------------


def test_remove_mute_success() -> None:
    silence_manager.mute_chat("777", minutes=10)
    client = _make_client()
    res = client.post("/api/admin/silence/remove", json={"chat_id": "777"})
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["was_muted"] is True
    assert silence_manager.is_chat_muted("777") is False


def test_remove_mute_idempotent_when_not_muted() -> None:
    client = _make_client()
    res = client.post("/api/admin/silence/remove", json={"chat_id": "999"})
    assert res.status_code == 200
    assert res.json()["was_muted"] is False


def test_remove_requires_write_access() -> None:
    client = _make_client(
        write_access_raises=HTTPException(status_code=403, detail="forbidden"),
    )
    res = client.post("/api/admin/silence/remove", json={"chat_id": "123"})
    assert res.status_code == 403


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------


def test_admin_silence_html_renders() -> None:
    client = _make_client()
    res = client.get("/admin/silence")
    assert res.status_code == 200
    body = res.text
    assert "Krab · Silence Admin" in body
    assert "/api/admin/silence/list" in body
    assert "/api/admin/silence/add" in body
    assert "/api/admin/silence/remove" in body


# ---------------------------------------------------------------------------
# Round-trip: add → list → remove → list
# ---------------------------------------------------------------------------


def test_round_trip_add_list_remove() -> None:
    client = _make_client()
    # add
    res = client.post(
        "/api/admin/silence/add",
        json={"chat_id": "-100777", "duration_minutes": 5, "reason": "smoke"},
    )
    assert res.status_code == 200
    # list shows it
    listed = client.get("/api/admin/silence/list").json()
    assert len(listed["active"]) == 1
    assert listed["active"][0]["chat_id"] == "-100777"
    assert listed["active"][0]["reason"] == "smoke"
    assert listed["active"][0]["since_iso"] is not None
    # remove
    res = client.post("/api/admin/silence/remove", json={"chat_id": "-100777"})
    assert res.status_code == 200
    assert res.json()["was_muted"] is True
    # list empty again
    after = client.get("/api/admin/silence/list").json()
    assert after["active"] == []
    assert after["stats"]["silenced_now"] == 0
