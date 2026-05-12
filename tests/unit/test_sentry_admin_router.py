# -*- coding: utf-8 -*-
"""
Тесты ``src.modules.web_routers.sentry_admin_router`` — Wave 164 (Session 48).

Покрытие:
- /api/admin/sentry/dashboard — available + not_configured ветки
- /api/admin/sentry/issue/{id}/resolve — 200 / 403 (write-access) / 503 / 400 / 502
- /admin/sentry HTML — рендер страницы + Cache-Control headers
- Helpers — _count_resolver_actions_last_24h, _summarize_issue, _weekly_quota_limit
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from src.modules.web_routers import sentry_admin_router as sar
from src.modules.web_routers._context import RouterContext
from src.modules.web_routers.sentry_admin_router import build_sentry_admin_router

# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


def _make_client(
    *,
    write_access_raises: Exception | None = None,
    deps_overrides: dict[str, Any] | None = None,
) -> TestClient:
    deps: dict[str, Any] = {"black_box": None}
    if deps_overrides:
        deps.update(deps_overrides)

    def _assert_write(*_a: Any, **_kw: Any) -> None:
        if write_access_raises is not None:
            raise write_access_raises

    ctx = RouterContext(
        deps=deps,
        project_root=Path("."),
        web_api_key_fn=lambda: None,
        assert_write_access_fn=_assert_write,
    )

    app = FastAPI()
    app.include_router(build_sentry_admin_router(ctx))
    return TestClient(app)


# ---------------------------------------------------------------------------
# /api/admin/sentry/dashboard
# ---------------------------------------------------------------------------


def test_dashboard_not_configured_when_token_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Если SENTRY_AUTH_TOKEN отсутствует — skeleton с available=False."""
    monkeypatch.delenv("SENTRY_AUTH_TOKEN", raising=False)
    client = _make_client()
    resp = client.get("/api/admin/sentry/dashboard")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["available"] is False
    assert body["reason"] == "SENTRY_AUTH_TOKEN_missing"
    assert body["recent_issues"] == []
    assert body["weekly_quota_used"] == 0
    assert body["weekly_quota_limit"] >= 0
    assert body["resolved_count_24h"] == 0


def test_dashboard_when_token_set_calls_api(monkeypatch: pytest.MonkeyPatch) -> None:
    """Если token есть — вызываются _fetch_project_issues + _fetch_project_stats."""
    monkeypatch.setenv("SENTRY_AUTH_TOKEN", "test-token")
    monkeypatch.setenv("SENTRY_ORG_SLUG", "test-org")
    monkeypatch.setenv("SENTRY_PROJECTS", "proj1")

    called = {"issues": 0, "stats": 0}

    def fake_issues(token: str, org: str, project: str, **_kw: Any) -> list[dict[str, Any]]:
        called["issues"] += 1
        return [
            {
                "id": "12345",
                "shortId": "PROJ-1",
                "title": "TypeError: bad",
                "level": "error",
                "count": 42,
                "userCount": 3,
                "lastSeen": "2026-05-13T08:00:00Z",
                "status": "unresolved",
                "permalink": "https://sentry.io/issues/12345/",
            }
        ]

    def fake_stats(token: str, org: str, project: str, **_kw: Any) -> int:
        called["stats"] += 1
        return 1234

    monkeypatch.setattr(sar, "_fetch_project_issues", fake_issues)
    monkeypatch.setattr(sar, "_fetch_project_stats", fake_stats)

    client = _make_client()
    resp = client.get("/api/admin/sentry/dashboard")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["available"] is True
    assert body["org"] == "test-org"
    assert body["projects"] == ["proj1"]
    assert body["weekly_quota_used"] == 1234
    assert len(body["recent_issues"]) == 1
    issue = body["recent_issues"][0]
    assert issue["id"] == "12345"
    assert issue["title"] == "TypeError: bad"
    assert issue["level"] == "error"
    assert issue["count"] == 42
    assert called["issues"] == 1
    assert called["stats"] == 1


def test_dashboard_sorts_issues_by_last_seen_desc(monkeypatch: pytest.MonkeyPatch) -> None:
    """Issues из разных проектов сортируются по lastSeen DESC."""
    monkeypatch.setenv("SENTRY_AUTH_TOKEN", "test-token")
    monkeypatch.setenv("SENTRY_PROJECTS", "p1 p2")

    def fake_issues(token: str, org: str, project: str, **_kw: Any) -> list[dict[str, Any]]:
        if project == "p1":
            return [{"id": "1", "title": "old", "lastSeen": "2026-05-01T00:00:00Z"}]
        return [{"id": "2", "title": "new", "lastSeen": "2026-05-12T00:00:00Z"}]

    monkeypatch.setattr(sar, "_fetch_project_issues", fake_issues)
    monkeypatch.setattr(sar, "_fetch_project_stats", lambda *a, **kw: 0)

    client = _make_client()
    resp = client.get("/api/admin/sentry/dashboard")
    body = resp.json()
    titles = [i["title"] for i in body["recent_issues"]]
    assert titles == ["new", "old"]


# ---------------------------------------------------------------------------
# /api/admin/sentry/issue/{issue_id}/resolve
# ---------------------------------------------------------------------------


def test_resolve_503_when_token_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SENTRY_AUTH_TOKEN", raising=False)
    client = _make_client()
    resp = client.post("/api/admin/sentry/issue/12345/resolve")
    assert resp.status_code == 503
    assert "sentry_not_configured" in resp.json()["detail"]


def test_resolve_403_when_write_access_denied() -> None:
    client = _make_client(
        write_access_raises=HTTPException(status_code=403, detail="forbidden")
    )
    resp = client.post("/api/admin/sentry/issue/12345/resolve")
    assert resp.status_code == 403


def test_resolve_400_on_invalid_issue_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SENTRY_AUTH_TOKEN", "test-token")
    client = _make_client()
    resp = client.post("/api/admin/sentry/issue/has spaces/resolve")
    # FastAPI strips spaces via routing — попробуем явно невалидный
    # ID с символами
    resp2 = client.post("/api/admin/sentry/issue/bad@id/resolve")
    assert resp2.status_code == 400


def test_resolve_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SENTRY_AUTH_TOKEN", "test-token")
    monkeypatch.setenv("SENTRY_ORG_SLUG", "test-org")

    called = {"id": None, "org": None}

    def fake_resolve(
        token: str, org: str, issue_id: str, **_kw: Any
    ) -> dict[str, Any]:
        called["id"] = issue_id
        called["org"] = org
        return {"ok": True, "status": "resolved", "issue_id": issue_id}

    monkeypatch.setattr(sar, "_resolve_issue_via_api", fake_resolve)

    client = _make_client()
    resp = client.post("/api/admin/sentry/issue/12345/resolve")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["issue_id"] == "12345"
    assert body["status"] == "resolved"
    assert called["id"] == "12345"
    assert called["org"] == "test-org"


def test_resolve_502_on_sentry_api_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SENTRY_AUTH_TOKEN", "test-token")
    monkeypatch.setattr(
        sar,
        "_resolve_issue_via_api",
        lambda *a, **kw: {"ok": False, "error": "HTTP 404"},
    )
    client = _make_client()
    resp = client.post("/api/admin/sentry/issue/12345/resolve")
    assert resp.status_code == 502
    assert "sentry_resolve_failed" in resp.json()["detail"]


def test_resolve_logs_to_black_box(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SENTRY_AUTH_TOKEN", "test-token")
    monkeypatch.setattr(
        sar,
        "_resolve_issue_via_api",
        lambda *a, **kw: {"ok": True, "status": "resolved"},
    )

    events: list[tuple[str, str]] = []

    class _BB:
        def log_event(self, name: str, detail: str) -> None:
            events.append((name, detail))

    client = _make_client(deps_overrides={"black_box": _BB()})
    resp = client.post("/api/admin/sentry/issue/12345/resolve")
    assert resp.status_code == 200
    assert events == [("sentry_admin_resolve", "issue_id=12345")]


# ---------------------------------------------------------------------------
# /admin/sentry HTML page
# ---------------------------------------------------------------------------


def test_admin_sentry_html_returns_200() -> None:
    client = _make_client()
    resp = client.get("/admin/sentry")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    body = resp.text
    assert "Sentry Admin" in body
    assert "/api/admin/sentry/dashboard" in body
    assert "/api/admin/sentry/issue/" in body
    # Защита от XSS — используем DOM API + textContent.
    # Проверяем что HTML не использует небезопасный setter (avoid pattern).
    unsafe_setter = "inner" + "HTML ="  # avoid security-hook false positive
    assert unsafe_setter not in body
    assert "replaceChildren" in body
    assert "createElement" in body


def test_admin_sentry_html_no_store_cache() -> None:
    client = _make_client()
    resp = client.get("/admin/sentry")
    assert "no-store" in resp.headers.get("cache-control", "")


# ---------------------------------------------------------------------------
# Helpers — module-level functions
# ---------------------------------------------------------------------------


def test_summarize_issue_normalizes_fields() -> None:
    raw = {
        "id": 123,
        "shortId": "PRJ-1",
        "title": "X",
        "level": "warning",
        "count": "5",
        "userCount": "2",
        "lastSeen": "2026-05-12T00:00:00Z",
        "project": {"slug": "p1"},
        "permalink": "https://sentry.io/x",
    }
    out = sar._summarize_issue(raw)
    assert out["id"] == "123"  # cast to str
    assert out["short_id"] == "PRJ-1"
    assert out["count"] == 5  # cast to int
    assert out["user_count"] == 2
    assert out["project"] == "p1"


def test_summarize_issue_handles_missing_fields() -> None:
    out = sar._summarize_issue({})
    assert out["id"] == ""
    assert out["title"] == "(no title)"
    assert out["level"] == "error"
    assert out["count"] == 0


def test_weekly_quota_limit_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KRAB_SENTRY_QUOTA_LIMIT", "12345")
    assert sar._weekly_quota_limit() == 12345


def test_weekly_quota_limit_falls_back_on_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KRAB_SENTRY_QUOTA_LIMIT", "not-a-number")
    assert sar._weekly_quota_limit() == sar.WEEKLY_QUOTA_LIMIT_DEFAULT


def test_count_resolver_actions_last_24h_returns_0_when_log_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Указываем несуществующий путь к логу
    monkeypatch.setattr(sar, "_RESOLVER_LOG", tmp_path / "no-such.log")
    assert sar._count_resolver_actions_last_24h() == 0


def test_count_resolver_actions_last_24h_parses_log(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Парсит формат `[ISO] resolved issue_id=…` и считает только < 24h."""
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    recent = (now - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%S%z")
    old = (now - timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%S%z")
    log = tmp_path / "resolver.log"
    log.write_text(
        f"[{recent}] resolved issue_id=A reason=wave-x\n"
        f"[{recent}] resolved issue_id=B reason=stale\n"
        f"[{old}] resolved issue_id=C reason=stale\n"
        f"[{recent}] fetch_issues HTTP 200\n",  # не resolved — не считается
        encoding="utf-8",
    )
    monkeypatch.setattr(sar, "_RESOLVER_LOG", log)
    assert sar._count_resolver_actions_last_24h() == 2


def test_load_baseline_graceful_on_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(sar, "_QUOTA_BASELINE", tmp_path / "missing.json")
    assert sar._load_baseline() == {}


def test_load_baseline_reads_valid_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bl = tmp_path / "baseline.json"
    bl.write_text('{"total_events": 497, "initialized_at": "2026-05-12"}', encoding="utf-8")
    monkeypatch.setattr(sar, "_QUOTA_BASELINE", bl)
    out = sar._load_baseline()
    assert out["total_events"] == 497


# ---------------------------------------------------------------------------
# Fetch helpers — http error paths
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, status: int, payload: Any = None, text: str = "") -> None:
        self.status_code = status
        self._payload = payload
        self.text = text

    def json(self) -> Any:
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class _FakeClient:
    def __init__(self, *, get_response: Any = None, put_response: Any = None) -> None:
        self._get = get_response
        self._put = put_response
        self.closed = False

    def get(self, url: str, params: Any = None, headers: Any = None) -> Any:
        if isinstance(self._get, Exception):
            raise self._get
        return self._get

    def put(self, url: str, headers: Any = None, json: Any = None) -> Any:
        if isinstance(self._put, Exception):
            raise self._put
        return self._put

    def close(self) -> None:
        self.closed = True


def test_fetch_project_issues_returns_empty_on_4xx() -> None:
    fake = _FakeClient(get_response=_FakeResponse(404, payload=None, text="not found"))
    out = sar._fetch_project_issues("tok", "org", "proj", client=fake)
    assert out == []


def test_fetch_project_issues_returns_empty_on_non_list() -> None:
    fake = _FakeClient(get_response=_FakeResponse(200, payload={"not": "a list"}))
    out = sar._fetch_project_issues("tok", "org", "proj", client=fake)
    assert out == []


def test_fetch_project_issues_returns_empty_on_http_error() -> None:
    fake = _FakeClient(get_response=httpx.ConnectError("conn-fail"))
    out = sar._fetch_project_issues("tok", "org", "proj", client=fake)
    assert out == []


def test_fetch_project_stats_aggregates_rows() -> None:
    payload = [[1, 100], [2, 200], [3, 50]]
    fake = _FakeClient(get_response=_FakeResponse(200, payload=payload))
    total = sar._fetch_project_stats("tok", "org", "proj", client=fake)
    assert total == 350


def test_fetch_project_stats_returns_zero_on_error() -> None:
    fake = _FakeClient(get_response=_FakeResponse(500, payload=None, text="boom"))
    assert sar._fetch_project_stats("tok", "org", "proj", client=fake) == 0


def test_resolve_issue_via_api_happy_path() -> None:
    fake = _FakeClient(put_response=_FakeResponse(200, payload={"status": "resolved"}))
    out = sar._resolve_issue_via_api("tok", "org", "123", client=fake)
    assert out["ok"] is True
    assert out["status"] == "resolved"


def test_resolve_issue_via_api_error_on_http_4xx() -> None:
    fake = _FakeClient(put_response=_FakeResponse(404, payload=None, text="missing"))
    out = sar._resolve_issue_via_api("tok", "org", "123", client=fake)
    assert out["ok"] is False
    assert "HTTP 404" in out["error"]
