# -*- coding: utf-8 -*-
"""
Тесты ``src.modules.web_routers.reactions_admin_router`` — Wave 227 (Session 49).

Все правила/логи изолируются на временные файлы через monkeypatch.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.modules.web_routers import reactions_admin_router as rar
from src.modules.web_routers._context import RouterContext
from src.modules.web_routers.reactions_admin_router import (
    build_reactions_admin_router,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Изолирует rules-file и reactions-log на временную директорию."""
    rules_file = tmp_path / "reaction_rules.json"
    log_file = tmp_path / "reactions_log.jsonl"
    monkeypatch.setattr(rar, "_RULES_FILE", rules_file)
    monkeypatch.setattr(rar, "_REACTIONS_LOG_FILE", log_file)
    return {"rules": rules_file, "log": log_file}


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
    app.include_router(build_reactions_admin_router(ctx))
    return TestClient(app)


# ---------------------------------------------------------------------------
# Validators
# ---------------------------------------------------------------------------


def test_validate_pattern_accepts_valid_regex() -> None:
    assert rar._validate_pattern(r"\bспасибо\b") == r"\bспасибо\b"
    assert rar._validate_pattern("hello") == "hello"


def test_validate_pattern_rejects_empty() -> None:
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        rar._validate_pattern("")
    assert exc.value.status_code == 400


def test_validate_pattern_rejects_bad_regex() -> None:
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        rar._validate_pattern("[unterminated")
    assert exc.value.status_code == 400
    assert "invalid_regex" in exc.value.detail


def test_validate_emoji_accepts_unicode_emoji() -> None:
    assert rar._validate_emoji("👍") == "👍"
    assert rar._validate_emoji("🔥") == "🔥"
    assert rar._validate_emoji("❤️") == "❤️"


def test_validate_emoji_accepts_short_code() -> None:
    assert rar._validate_emoji(":fire:") == ":fire:"
    assert rar._validate_emoji(":thumbs_up:") == ":thumbs_up:"


def test_validate_emoji_rejects_plain_text() -> None:
    from fastapi import HTTPException

    with pytest.raises(HTTPException):
        rar._validate_emoji("notanemoji")
    with pytest.raises(HTTPException):
        rar._validate_emoji("")


def test_validate_scope_normalizes() -> None:
    assert rar._validate_scope("any") == "any"
    assert rar._validate_scope("CHAT") == "chat"
    assert rar._validate_scope("user") == "user"


def test_validate_scope_rejects_unknown() -> None:
    from fastapi import HTTPException

    with pytest.raises(HTTPException):
        rar._validate_scope("global")


# ---------------------------------------------------------------------------
# GET /api/admin/reactions/list
# ---------------------------------------------------------------------------


def test_list_returns_empty_when_no_storage(isolated_storage) -> None:
    client = _make_client()
    resp = client.get("/api/admin/reactions/list")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["count"] == 0
    assert body["rules"] == []
    assert body["recent"] == []
    # stats всегда есть с 7 днями (filled с нулями).
    assert "per_day" in body["stats"]
    assert len(body["stats"]["per_day"]) == 7


def test_list_returns_existing_rules(isolated_storage) -> None:
    rules = [
        {
            "id": "abc123",
            "pattern": r"\bпривет\b",
            "emoji": "👋",
            "scope": "any",
            "enabled": True,
            "created_ts": 1000.0,
        }
    ]
    isolated_storage["rules"].write_text(json.dumps(rules), encoding="utf-8")
    client = _make_client()
    resp = client.get("/api/admin/reactions/list")
    body = resp.json()
    assert body["count"] == 1
    assert body["enabled_count"] == 1
    assert body["rules"][0]["id"] == "abc123"
    assert body["rules"][0]["emoji"] == "👋"


def test_list_reads_recent_events(isolated_storage) -> None:
    now = time.time()
    events = [
        {
            "ts": now - 100,
            "type": "reaction_added",
            "chat_id": 100,
            "message_id": 200,
            "user_id": 42,
            "username": "uid:42",
            "emoji": "👍",
        },
        {
            "ts": now - 50,
            "type": "reaction_added",
            "chat_id": 100,
            "message_id": 201,
            "user_id": 42,
            "username": "uid:42",
            "emoji": "🔥",
        },
    ]
    log_lines = "\n".join(json.dumps(e) for e in events)
    isolated_storage["log"].write_text(log_lines, encoding="utf-8")
    client = _make_client()
    resp = client.get("/api/admin/reactions/list")
    body = resp.json()
    assert len(body["recent"]) == 2
    # Newest first.
    assert body["recent"][0]["emoji"] == "🔥"
    assert body["stats"]["total_7d"] == 2


# ---------------------------------------------------------------------------
# POST /api/admin/reactions/add
# ---------------------------------------------------------------------------


def test_add_creates_rule(isolated_storage) -> None:
    client = _make_client()
    resp = client.post(
        "/api/admin/reactions/add",
        json={"pattern": r"\bcпасибо\b", "emoji": "🙏", "scope": "any"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["rule"]["pattern"] == r"\bcпасибо\b"
    assert body["rule"]["emoji"] == "🙏"
    assert body["rule"]["enabled"] is True
    # Storage реально содержит правило.
    on_disk = json.loads(isolated_storage["rules"].read_text(encoding="utf-8"))
    assert len(on_disk) == 1
    assert on_disk[0]["emoji"] == "🙏"


def test_add_rejects_invalid_pattern(isolated_storage) -> None:
    client = _make_client()
    resp = client.post(
        "/api/admin/reactions/add",
        json={"pattern": "[bad", "emoji": "👍", "scope": "any"},
    )
    assert resp.status_code == 400


def test_add_rejects_invalid_emoji(isolated_storage) -> None:
    client = _make_client()
    resp = client.post(
        "/api/admin/reactions/add",
        json={"pattern": "hello", "emoji": "notemoji", "scope": "any"},
    )
    assert resp.status_code == 400


def test_add_rejects_duplicate(isolated_storage) -> None:
    client = _make_client()
    payload = {"pattern": "hi", "emoji": "👋", "scope": "any"}
    r1 = client.post("/api/admin/reactions/add", json=payload)
    assert r1.status_code == 200
    r2 = client.post("/api/admin/reactions/add", json=payload)
    assert r2.status_code == 409


def test_add_blocked_when_write_access_denied(isolated_storage) -> None:
    from fastapi import HTTPException

    client = _make_client(
        write_access_raises=HTTPException(status_code=403, detail="forbidden"),
    )
    resp = client.post(
        "/api/admin/reactions/add",
        json={"pattern": "hi", "emoji": "👋", "scope": "any"},
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# POST /api/admin/reactions/toggle/{id}
# ---------------------------------------------------------------------------


def test_toggle_flips_enabled(isolated_storage) -> None:
    client = _make_client()
    # Add rule first.
    r1 = client.post(
        "/api/admin/reactions/add",
        json={"pattern": "hi", "emoji": "👋", "scope": "any"},
    )
    rule_id = r1.json()["rule"]["id"]
    # Toggle off.
    r2 = client.post(f"/api/admin/reactions/toggle/{rule_id}")
    assert r2.status_code == 200
    assert r2.json()["rule"]["enabled"] is False
    # Toggle on.
    r3 = client.post(f"/api/admin/reactions/toggle/{rule_id}")
    assert r3.json()["rule"]["enabled"] is True


def test_toggle_404_when_missing(isolated_storage) -> None:
    client = _make_client()
    resp = client.post("/api/admin/reactions/toggle/no_such_id")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/admin/reactions/remove/{id}
# ---------------------------------------------------------------------------


def test_remove_deletes_rule(isolated_storage) -> None:
    client = _make_client()
    r1 = client.post(
        "/api/admin/reactions/add",
        json={"pattern": "hi", "emoji": "👋", "scope": "any"},
    )
    rule_id = r1.json()["rule"]["id"]
    r2 = client.post(f"/api/admin/reactions/remove/{rule_id}")
    assert r2.status_code == 200
    assert r2.json()["ok"] is True
    # Список должен быть пустым.
    on_disk = json.loads(isolated_storage["rules"].read_text(encoding="utf-8"))
    assert on_disk == []


def test_remove_404_when_missing(isolated_storage) -> None:
    client = _make_client()
    resp = client.post("/api/admin/reactions/remove/no_such_id")
    assert resp.status_code == 404


def test_remove_blocked_when_write_access_denied(isolated_storage) -> None:
    from fastapi import HTTPException

    client = _make_client(
        write_access_raises=HTTPException(status_code=403, detail="forbidden"),
    )
    resp = client.post("/api/admin/reactions/remove/any_id")
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# GET /admin/reactions — HTML page
# ---------------------------------------------------------------------------


def test_admin_reactions_page_returns_html() -> None:
    client = _make_client()
    resp = client.get("/admin/reactions")
    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "")
    assert "Reactions Admin" in resp.text
    assert "/api/admin/reactions/list" in resp.text
    assert "/api/admin/reactions/add" in resp.text
    assert "/api/admin/reactions/toggle/" in resp.text
    assert "/api/admin/reactions/remove/" in resp.text
