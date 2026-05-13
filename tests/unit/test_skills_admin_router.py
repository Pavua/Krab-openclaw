# -*- coding: utf-8 -*-
"""
Тесты ``src.modules.web_routers.skills_admin_router`` — Wave 198 (Session 48).

Read-only router: проверяем factory-pattern, AST-парсеры, валидацию имени
и HTML/JSON-эндпоинты через TestClient. Файловая система мокируется через
временные каталоги (``tmp_path``) — реальный ``src/skills/`` не трогается.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.modules.web_routers import skills_admin_router as sar
from src.modules.web_routers._context import RouterContext
from src.modules.web_routers.skills_admin_router import build_skills_admin_router

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_client(*, project_root: Path) -> TestClient:
    ctx = RouterContext(
        deps={},
        project_root=project_root,
        web_api_key_fn=lambda: None,
        assert_write_access_fn=lambda *a, **kw: None,
    )
    app = FastAPI()
    app.include_router(build_skills_admin_router(ctx))
    return TestClient(app)


def _make_skill_tree(root: Path, files: dict[str, str]) -> Path:
    """Создаёт src/skills/<name>.py в tmp-каталоге, возвращает project_root."""
    skills_dir = root / "src" / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        (skills_dir / name).write_text(content, encoding="utf-8")
    return root


# ---------------------------------------------------------------------------
# AST helpers — _extract_public_functions + _extract_module_docstring
# ---------------------------------------------------------------------------


def test_extract_public_functions_basic() -> None:
    src = (
        '"""Doc."""\n'
        "async def foo(x):\n    return x\n\n"
        "def bar():\n    pass\n\n"
        "def _private():\n    pass\n"
    )
    funcs = sar._extract_public_functions(src)
    names = {f["name"] for f in funcs}
    assert names == {"foo", "bar"}
    foo = next(f for f in funcs if f["name"] == "foo")
    assert foo["is_async"] is True
    bar = next(f for f in funcs if f["name"] == "bar")
    assert bar["is_async"] is False


def test_extract_public_functions_handles_syntax_error() -> None:
    funcs = sar._extract_public_functions("def broken( :\n")
    assert funcs == []


def test_extract_module_docstring_first_line() -> None:
    src = '"""First line.\n\nSecond para."""\nx = 1\n'
    assert sar._extract_module_docstring(src) == "First line."


def test_extract_module_docstring_missing_returns_empty() -> None:
    assert sar._extract_module_docstring("x = 1\n") == ""


# ---------------------------------------------------------------------------
# Skill name validation
# ---------------------------------------------------------------------------


def test_validate_skill_name_rejects_traversal() -> None:
    import pytest
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        sar._validate_skill_name("../etc/passwd")
    assert exc.value.status_code == 400


def test_validate_skill_name_rejects_empty() -> None:
    import pytest
    from fastapi import HTTPException

    with pytest.raises(HTTPException):
        sar._validate_skill_name("")


def test_validate_skill_name_accepts_normal() -> None:
    assert sar._validate_skill_name("mercadona") == "mercadona"
    assert sar._validate_skill_name("web_search") == "web_search"


# ---------------------------------------------------------------------------
# _enumerate_skills — file-system scan
# ---------------------------------------------------------------------------


def test_enumerate_skills_filters_hidden_and_init(tmp_path: Path) -> None:
    project_root = _make_skill_tree(
        tmp_path,
        {
            "__init__.py": "",
            "stealth_browser.py": '"""Helper."""\ndef helper():\n    pass\n',
            "mercadona.py": (
                '"""Mercadona scraper."""\n'
                "async def search(query):\n    return query\n"
                "def parse(data):\n    return data\n"
            ),
            "crypto.py": '"""Crypto prices."""\nasync def get_price():\n    return 1\n',
        },
    )
    ctx = RouterContext(
        deps={},
        project_root=project_root,
        web_api_key_fn=lambda: None,
        assert_write_access_fn=lambda *a, **kw: None,
    )
    skills = sar._enumerate_skills(ctx)
    names = {s["name"] for s in skills}
    assert names == {"mercadona", "crypto"}  # __init__ + stealth_browser скрыты
    merc = next(s for s in skills if s["name"] == "mercadona")
    assert merc["public_function_count"] == 2
    assert merc["docstring"] == "Mercadona scraper."
    assert merc["relative_file"] == "src/skills/mercadona.py"


# ---------------------------------------------------------------------------
# /api/admin/skills/list endpoint
# ---------------------------------------------------------------------------


def test_skills_list_endpoint_returns_metadata(tmp_path: Path) -> None:
    project_root = _make_skill_tree(
        tmp_path,
        {
            "crypto.py": '"""Crypto."""\nasync def get_price():\n    return 1\n',
        },
    )
    client = _make_client(project_root=project_root)
    res = client.get("/api/admin/skills/list")
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["count"] == 1
    skill = data["skills"][0]
    assert skill["name"] == "crypto"
    assert skill["public_function_count"] == 1
    assert skill["public_functions"][0]["name"] == "get_price"
    assert skill["public_functions"][0]["is_async"] is True
    assert skill["mtime"] is not None


def test_skills_list_handles_empty_dir(tmp_path: Path) -> None:
    # Каталог src/skills есть, но пустой.
    (tmp_path / "src" / "skills").mkdir(parents=True)
    client = _make_client(project_root=tmp_path)
    res = client.get("/api/admin/skills/list")
    assert res.status_code == 200
    assert res.json() == {"ok": True, "count": 0, "skills": []}


# ---------------------------------------------------------------------------
# /api/admin/skills/{name}/curator_reports endpoint
# ---------------------------------------------------------------------------


def test_curator_reports_endpoint_returns_team_data(tmp_path: Path) -> None:
    # Подменяем _CURATOR_DAILY_DIR на tmp-структуру.
    fake_daily = tmp_path / "skill_curator" / "daily"
    (fake_daily / "traders").mkdir(parents=True)
    payload: dict[str, Any] = {
        "team": "traders",
        "date": "2026-05-06",
        "rounds_analyzed": 12,
        "success_rate": 0.83,
        "distinct_topics": 4,
        "recurring_failure_tags": ["timeout"],
        "generated_at": "2026-05-06T18:26:00+00:00",
    }
    (fake_daily / "traders" / "2026-05-06.json").write_text(json.dumps(payload), encoding="utf-8")

    project_root = _make_skill_tree(tmp_path, {})
    client = _make_client(project_root=project_root)
    with patch.object(sar, "_CURATOR_DAILY_DIR", fake_daily):
        res = client.get("/api/admin/skills/traders/curator_reports")
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["name"] == "traders"
    assert data["count"] == 1
    assert data["reports"][0]["rounds_analyzed"] == 12
    assert data["reports"][0]["success_rate"] == 0.83


def test_curator_reports_unknown_name_aggregates_all_teams(tmp_path: Path) -> None:
    fake_daily = tmp_path / "skill_curator" / "daily"
    for team in ("traders", "coders"):
        (fake_daily / team).mkdir(parents=True)
        (fake_daily / team / "2026-05-06.json").write_text(
            json.dumps(
                {
                    "team": team,
                    "date": "2026-05-06",
                    "rounds_analyzed": 1,
                    "success_rate": 1.0,
                }
            ),
            encoding="utf-8",
        )
    project_root = _make_skill_tree(tmp_path, {})
    client = _make_client(project_root=project_root)
    with patch.object(sar, "_CURATOR_DAILY_DIR", fake_daily):
        res = client.get("/api/admin/skills/mercadona/curator_reports")
    assert res.status_code == 200
    data = res.json()
    # mercadona не в списке teams — берём все команды
    teams_in_reports = {r["team"] for r in data["reports"]}
    assert teams_in_reports == {"traders", "coders"}


def test_curator_reports_rejects_traversal(tmp_path: Path) -> None:
    project_root = _make_skill_tree(tmp_path, {})
    client = _make_client(project_root=project_root)
    res = client.get("/api/admin/skills/..%2Fetc/curator_reports")
    # FastAPI декодирует %2F → "../etc" → 400 from validator.
    # Если роутер интерпретирует это как 404 path mismatch — тоже допустимо.
    assert res.status_code in (400, 404)


def test_curator_reports_missing_dir_returns_empty(tmp_path: Path) -> None:
    project_root = _make_skill_tree(tmp_path, {})
    client = _make_client(project_root=project_root)
    nonexistent = tmp_path / "nope"
    with patch.object(sar, "_CURATOR_DAILY_DIR", nonexistent):
        res = client.get("/api/admin/skills/traders/curator_reports")
    assert res.status_code == 200
    assert res.json() == {
        "ok": True,
        "name": "traders",
        "count": 0,
        "reports": [],
    }


# ---------------------------------------------------------------------------
# /admin/skills — HTML page
# ---------------------------------------------------------------------------


def test_admin_skills_html_page_served(tmp_path: Path) -> None:
    client = _make_client(project_root=tmp_path)
    res = client.get("/admin/skills")
    assert res.status_code == 200
    assert "text/html" in res.headers["content-type"]
    body = res.text
    assert "Krab · Skills Admin" in body
    assert "/api/admin/skills/list" in body
