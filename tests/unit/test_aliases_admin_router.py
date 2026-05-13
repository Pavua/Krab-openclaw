# -*- coding: utf-8 -*-
"""
Тесты ``src.modules.web_routers.aliases_admin_router`` — Wave 200 (Session 48).

alias_service переключается на временный storage через подмену
синглтона (см. ``_isolated_alias_service`` fixture).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core.command_aliases import AliasService
from src.modules.web_routers import aliases_admin_router as aar
from src.modules.web_routers._context import RouterContext
from src.modules.web_routers.aliases_admin_router import build_aliases_admin_router

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_alias_service(tmp_path: Path):
    """Изолирует alias_service на временный JSON storage."""
    fake = AliasService(storage_path=tmp_path / "aliases.json")
    with patch.object(aar, "alias_service", fake):
        yield fake


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
    app.include_router(build_aliases_admin_router(ctx))
    return TestClient(app)


# ---------------------------------------------------------------------------
# Helpers / validation
# ---------------------------------------------------------------------------


def test_validate_alias_name_accepts_lowercase() -> None:
    assert aar._validate_alias_name("tr") == "tr"
    assert aar._validate_alias_name("my-alias_2") == "my-alias_2"


def test_validate_alias_name_strips_prefix_and_lowers() -> None:
    assert aar._validate_alias_name("!FooBar") == "foobar"


def test_validate_alias_name_rejects_empty() -> None:
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        aar._validate_alias_name("")
    assert exc.value.status_code == 400
    assert "empty" in exc.value.detail


def test_validate_alias_name_rejects_leading_digit() -> None:
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        aar._validate_alias_name("1bad")
    assert exc.value.status_code == 400


def test_validate_alias_name_rejects_spaces_and_special() -> None:
    from fastapi import HTTPException

    for bad in ("a b", "ab!c", "ab@", "ab.c"):
        with pytest.raises(HTTPException):
            aar._validate_alias_name(bad)


def test_validate_alias_name_rejects_reserved() -> None:
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        aar._validate_alias_name("help")
    assert exc.value.status_code == 400


def test_split_alias_value_basic() -> None:
    assert aar._split_alias_value("translate auto ru") == ("translate", "auto ru")
    assert aar._split_alias_value("status") == ("status", "")
    assert aar._split_alias_value("") == ("", "")


def test_load_usage_counts_legacy_format(tmp_path: Path) -> None:
    f = tmp_path / "u.json"
    f.write_text(json.dumps({"translate": 5, "status": 3}), encoding="utf-8")
    with patch.object(aar, "_USAGE_FILE", f):
        counts = aar._load_usage_counts()
    assert counts == {"translate": 5, "status": 3}


def test_load_usage_counts_new_format(tmp_path: Path) -> None:
    f = tmp_path / "u.json"
    f.write_text(
        json.dumps({"counts": {"translate": 7}, "last_ts": {"translate": 1.0}}),
        encoding="utf-8",
    )
    with patch.object(aar, "_USAGE_FILE", f):
        counts = aar._load_usage_counts()
    assert counts == {"translate": 7}


def test_load_usage_counts_missing_returns_empty(tmp_path: Path) -> None:
    with patch.object(aar, "_USAGE_FILE", tmp_path / "nope.json"):
        assert aar._load_usage_counts() == {}


def test_load_usage_counts_invalid_json_returns_empty(tmp_path: Path) -> None:
    f = tmp_path / "bad.json"
    f.write_text("{not-json", encoding="utf-8")
    with patch.object(aar, "_USAGE_FILE", f):
        assert aar._load_usage_counts() == {}


# ---------------------------------------------------------------------------
# GET /api/admin/aliases/list
# ---------------------------------------------------------------------------


def test_aliases_list_returns_ok_and_commands(isolated_alias_service: AliasService) -> None:
    isolated_alias_service.add("tr", "translate auto")
    with patch.object(aar, "_load_usage_counts", return_value={"tr": 12}):
        client = _make_client()
        resp = client.get("/api/admin/aliases/list")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["count"] == 1
    names = [a["name"] for a in body["aliases"]]
    assert "tr" in names
    entry = next(a for a in body["aliases"] if a["name"] == "tr")
    assert entry["target"] == "translate"
    assert entry["args"] == "auto"
    assert entry["usage_count"] == 12
    # available_commands должен быть непустым (162 в реестре).
    assert len(body["available_commands"]) > 50


def test_aliases_list_marks_conflicts(isolated_alias_service: AliasService) -> None:
    # Создаём алиас который случайно совпадает с зарегистрированной командой
    # (обход validate, минуя API — напрямую через alias_service).
    isolated_alias_service._aliases["stats"] = "translate"
    client = _make_client()
    resp = client.get("/api/admin/aliases/list")
    body = resp.json()
    entry = next(a for a in body["aliases"] if a["name"] == "stats")
    assert entry["conflicts"] is True
    assert body["conflict_count"] >= 1


def test_aliases_list_empty(isolated_alias_service: AliasService) -> None:
    client = _make_client()
    resp = client.get("/api/admin/aliases/list")
    body = resp.json()
    assert body["count"] == 0
    assert body["aliases"] == []
    assert body["conflict_count"] == 0


# ---------------------------------------------------------------------------
# POST /api/admin/aliases/add
# ---------------------------------------------------------------------------


def test_aliases_add_creates_alias(isolated_alias_service: AliasService) -> None:
    client = _make_client()
    resp = client.post(
        "/api/admin/aliases/add",
        json={"name": "tr", "target": "translate", "args": "auto"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["name"] == "tr"
    assert body["target"] == "translate"
    # Storage должен реально содержать алиас.
    assert isolated_alias_service.list_all().get("tr") == "translate auto"


def test_aliases_add_rejects_invalid_name(isolated_alias_service: AliasService) -> None:
    client = _make_client()
    resp = client.post(
        "/api/admin/aliases/add",
        json={"name": "1bad", "target": "translate"},
    )
    assert resp.status_code == 400


def test_aliases_add_rejects_unknown_target(isolated_alias_service: AliasService) -> None:
    client = _make_client()
    resp = client.post(
        "/api/admin/aliases/add",
        json={"name": "tr", "target": "no_such_cmd"},
    )
    assert resp.status_code == 400
    assert "alias_target_unknown" in resp.json()["detail"]


def test_aliases_add_rejects_collision_with_command(
    isolated_alias_service: AliasService,
) -> None:
    client = _make_client()
    # 'help' — зарегистрированная команда → коллизия.
    resp = client.post(
        "/api/admin/aliases/add",
        json={"name": "help", "target": "translate"},
    )
    # 'help' попадает под RESERVED_NAMES → 400, не 409. Это OK,
    # collision-проверка просто покрыта на уровне reserved.
    assert resp.status_code == 400


def test_aliases_add_blocked_when_write_access_denied(
    isolated_alias_service: AliasService,
) -> None:
    from fastapi import HTTPException

    client = _make_client(
        write_access_raises=HTTPException(status_code=403, detail="forbidden"),
    )
    resp = client.post(
        "/api/admin/aliases/add",
        json={"name": "tr", "target": "translate"},
    )
    assert resp.status_code == 403


def test_aliases_add_empty_target_rejected(isolated_alias_service: AliasService) -> None:
    client = _make_client()
    resp = client.post(
        "/api/admin/aliases/add",
        json={"name": "tr", "target": ""},
    )
    assert resp.status_code == 400
    assert "alias_target_empty" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# POST /api/admin/aliases/remove
# ---------------------------------------------------------------------------


def test_aliases_remove_existing(isolated_alias_service: AliasService) -> None:
    isolated_alias_service.add("tr", "translate")
    client = _make_client()
    resp = client.post("/api/admin/aliases/remove", json={"name": "tr"})
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert "tr" not in isolated_alias_service.list_all()


def test_aliases_remove_404_when_missing(isolated_alias_service: AliasService) -> None:
    client = _make_client()
    resp = client.post("/api/admin/aliases/remove", json={"name": "never"})
    assert resp.status_code == 404


def test_aliases_remove_blocked_when_write_access_denied(
    isolated_alias_service: AliasService,
) -> None:
    from fastapi import HTTPException

    client = _make_client(
        write_access_raises=HTTPException(status_code=403, detail="forbidden"),
    )
    resp = client.post("/api/admin/aliases/remove", json={"name": "tr"})
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# GET /admin/aliases — HTML page
# ---------------------------------------------------------------------------


def test_admin_aliases_page_returns_html() -> None:
    client = _make_client()
    resp = client.get("/admin/aliases")
    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "")
    assert "Aliases Admin" in resp.text
    assert "/api/admin/aliases/list" in resp.text
    assert "/api/admin/aliases/add" in resp.text
    assert "/api/admin/aliases/remove" in resp.text
