# -*- coding: utf-8 -*-
"""
Тесты ``src.modules.web_routers.help_admin_router`` — Wave 187 (Session 48).

Покрытие:
- factory + endpoints (HTML page + JSON metadata)
- metadata schema (все 14 страниц, обязательные поля)
- git log парсер (с моком subprocess)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.modules.web_routers import help_admin_router as har
from src.modules.web_routers._context import RouterContext
from src.modules.web_routers.help_admin_router import build_help_admin_router

_EXPECTED_PATHS = {
    "/admin/models",
    "/admin/swarm",
    "/admin/costs",
    "/admin/ecosystem",
    "/admin/inbox",
    "/admin/routing",
    "/admin/cron",
    "/admin/sentry",
    "/admin/logs",
    "/admin/db",
    "/admin/network",
    "/admin/voice",
    "/admin/memory",
    "/admin/health",
}


def _make_client() -> TestClient:
    ctx = RouterContext(
        deps={},
        project_root=Path("."),
        web_api_key_fn=lambda: None,
        assert_write_access_fn=lambda *a, **kw: None,
    )
    app = FastAPI()
    app.include_router(build_help_admin_router(ctx))
    return TestClient(app)


# ── Metadata structure ──────────────────────────────────────────────────────


def test_admin_pages_count_is_14() -> None:
    """Гарантируем что constant остаётся ровно из 14 страниц."""
    assert len(har._ADMIN_PAGES) == 14


def test_all_expected_paths_present() -> None:
    """Все 14 ожидаемых URL зарегистрированы."""
    actual_paths = {p["path"] for p in har._ADMIN_PAGES}
    assert actual_paths == _EXPECTED_PATHS


def test_each_page_has_required_fields() -> None:
    """Каждая запись имеет path/wave/emoji/title/purpose/endpoints/when."""
    required = {"path", "wave", "emoji", "title", "purpose", "endpoints", "when"}
    for page in har._ADMIN_PAGES:
        missing = required - set(page.keys())
        assert not missing, f"page {page.get('path')} missing fields: {missing}"
        assert isinstance(page["wave"], int)
        assert isinstance(page["endpoints"], list)
        assert len(page["endpoints"]) >= 1
        assert page["emoji"]
        assert page["title"]


def test_health_page_has_highest_wave() -> None:
    """/admin/health (Wave 186) — самая последняя в списке fixture."""
    waves = {p["path"]: p["wave"] for p in har._ADMIN_PAGES}
    assert waves["/admin/health"] == 186
    assert waves["/admin/models"] == 144
    assert waves["/admin/cron"] == 165


# ── /api/admin/help/pages endpoint ──────────────────────────────────────────


def test_pages_endpoint_returns_metadata() -> None:
    client = _make_client()
    res = client.get("/api/admin/help/pages")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["count"] == 14
    assert len(body["pages"]) == 14
    paths = {p["path"] for p in body["pages"]}
    assert paths == _EXPECTED_PATHS


def test_pages_endpoint_includes_recent_waves_field() -> None:
    """recent_waves всегда присутствует как list (может быть пустым)."""
    client = _make_client()
    body = client.get("/api/admin/help/pages").json()
    assert "recent_waves" in body
    assert isinstance(body["recent_waves"], list)


# ── /admin/help HTML page ───────────────────────────────────────────────────


def test_help_html_page_renders() -> None:
    client = _make_client()
    res = client.get("/admin/help")
    assert res.status_code == 200
    assert "text/html" in res.headers["content-type"]
    body = res.text
    # Ключевые маркеры присутствуют.
    assert "Admin Help" in body
    assert "/api/admin/help/pages" in body  # клиентский fetch
    assert "/admin/health" in body  # quick-health link
    assert "Wave 187" in body


# ── _git_recent_waves ───────────────────────────────────────────────────────


def test_git_recent_waves_parses_wave_commits() -> None:
    sample = (
        "cf742ed Wave 186-fix-2: foo\n"
        "38d21c0 Wave 186: bar\n"
        "abc1234 chore: gitignore tweak\n"  # не-wave, пропускается
        "1cd9b9d Wave 183: voice page\n"
    )

    class _Proc:
        returncode = 0
        stdout = sample

    with patch.object(har.subprocess, "run", return_value=_Proc()):
        result = har._git_recent_waves(limit=5)

    # 3 Wave-коммита, не-wave отфильтрован
    assert len(result) == 3
    assert result[0]["sha"] == "cf742ed"
    assert "Wave 186-fix-2" in result[0]["subject"]
    assert all("sha" in r and "subject" in r for r in result)


def test_git_recent_waves_handles_subprocess_error() -> None:
    """На любую ошибку — возвращается пустой list (graceful)."""
    with patch.object(
        har.subprocess,
        "run",
        side_effect=FileNotFoundError("no git"),
    ):
        result = har._git_recent_waves(limit=10)
    assert result == []


def test_git_recent_waves_handles_nonzero_returncode() -> None:
    class _Proc:
        returncode = 128
        stdout = ""

    with patch.object(har.subprocess, "run", return_value=_Proc()):
        result = har._git_recent_waves(limit=10)
    assert result == []


def test_git_recent_waves_respects_limit() -> None:
    lines = "\n".join(f"sha{i:03d} Wave {100 + i}: msg" for i in range(20))

    class _Proc:
        returncode = 0
        stdout = lines

    with patch.object(har.subprocess, "run", return_value=_Proc()):
        result = har._git_recent_waves(limit=5)
    assert len(result) == 5


# ── Factory smoke ───────────────────────────────────────────────────────────


def test_factory_returns_router_with_two_routes() -> None:
    ctx = RouterContext(
        deps={},
        project_root=Path("."),
        web_api_key_fn=lambda: None,
        assert_write_access_fn=lambda *a, **kw: None,
    )
    router = build_help_admin_router(ctx)
    paths = {r.path for r in router.routes}  # type: ignore[attr-defined]
    assert "/api/admin/help/pages" in paths
    assert "/admin/help" in paths
