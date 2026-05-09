# -*- coding: utf-8 -*-
"""Wave 44-U observability tests.

Covers:
- record_agent_run appends jsonl line с правильной truncation
- read_runs возвращает most-recent-first + фильтрация
- get_run по request_id
- /api/observability/runs endpoint
- /api/observability/run/<rid> endpoint
- /observability page route
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.modules.web_routers._context import RouterContext
from src.modules.web_routers.observability_router import build_observability_router
from src.modules.web_routers.pages_router import build_pages_router


@pytest.fixture
def temp_runs_log(tmp_path, monkeypatch):
    """Перенаправляет RUNS_LOG в временный путь."""
    log_path = tmp_path / "runs_history.jsonl"
    monkeypatch.setattr("src.integrations._observability_log.RUNS_LOG", log_path)
    return log_path


def test_record_agent_run_appends_line(temp_runs_log: Path) -> None:
    from src.integrations._observability_log import record_agent_run

    rid = record_agent_run(
        chat_id=12345,
        user_id=999,
        model="codex-cli/gpt-5.5",
        prompt_text="Hello world",
        response_text="Hi there",
        duration_sec=1.5,
        status="ok",
    )
    assert rid
    assert temp_runs_log.exists()
    lines = temp_runs_log.read_text().strip().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["request_id"] == rid
    assert rec["chat_id"] == 12345
    assert rec["status"] == "ok"
    assert rec["model"] == "codex-cli/gpt-5.5"
    assert rec["prompt_excerpt"] == "Hello world"
    assert rec["duration_sec"] == 1.5


def test_record_truncates_prompt_to_200_chars(temp_runs_log: Path) -> None:
    from src.integrations._observability_log import record_agent_run

    long_prompt = "x" * 1000
    record_agent_run(model="m", prompt_text=long_prompt)
    rec = json.loads(temp_runs_log.read_text().strip().splitlines()[-1])
    # 200 chars + "…" suffix
    assert len(rec["prompt_excerpt"]) <= 201
    assert rec["prompt_len"] == 1000


def test_read_runs_returns_recent_first(temp_runs_log: Path) -> None:
    from src.integrations._observability_log import read_runs, record_agent_run

    record_agent_run(model="m1", prompt_text="first", chat_id=1)
    time.sleep(0.01)
    record_agent_run(model="m2", prompt_text="second", chat_id=2)

    runs = read_runs(limit=10)
    assert len(runs) == 2
    # Newest first
    assert runs[0]["chat_id"] == 2
    assert runs[1]["chat_id"] == 1


def test_read_runs_filters_by_chat_id(temp_runs_log: Path) -> None:
    from src.integrations._observability_log import read_runs, record_agent_run

    record_agent_run(model="m", chat_id=100)
    record_agent_run(model="m", chat_id=200)
    record_agent_run(model="m", chat_id=100)

    runs = read_runs(chat_id_filter=100)
    assert len(runs) == 2
    assert all(r["chat_id"] == 100 for r in runs)


def test_read_runs_filters_by_status(temp_runs_log: Path) -> None:
    from src.integrations._observability_log import read_runs, record_agent_run

    record_agent_run(model="m", status="ok")
    record_agent_run(model="m", status="error")
    record_agent_run(model="m", status="error")

    errors = read_runs(status_filter="error")
    assert len(errors) == 2
    assert all(r["status"] == "error" for r in errors)


def test_get_run_by_request_id(temp_runs_log: Path) -> None:
    from src.integrations._observability_log import get_run, record_agent_run

    rid = record_agent_run(model="m", prompt_text="test", chat_id=42)
    record_agent_run(model="m", prompt_text="other")

    rec = get_run(rid)
    assert rec is not None
    assert rec["request_id"] == rid
    assert rec["chat_id"] == 42


def test_get_run_returns_none_for_missing(temp_runs_log: Path) -> None:
    from src.integrations._observability_log import get_run, record_agent_run

    record_agent_run(model="m")
    assert get_run("nonexistent") is None


def test_record_does_not_raise_on_failure(monkeypatch) -> None:
    """Even если file write fails — не должно крашить caller."""
    from src.integrations._observability_log import record_agent_run

    bad_path = Path("/nonexistent/dir/that/cannot/exist/file.jsonl")
    monkeypatch.setattr("src.integrations._observability_log.RUNS_LOG", bad_path)
    # Disable urllib network call too
    monkeypatch.setattr(
        "src.integrations._observability_log._try_register_openclaw", lambda r: None
    )
    # mkdir on root /nonexistent will fail but record_agent_run swallows
    rid = record_agent_run(model="m", prompt_text="x")
    assert rid  # still returns an id


def _build_ctx() -> RouterContext:
    return RouterContext(
        deps={},
        project_root=Path("/tmp"),
        web_api_key_fn=lambda: "",
        assert_write_access_fn=lambda h, t: None,
    )


def _api_client() -> TestClient:
    app = FastAPI()
    app.include_router(build_observability_router(_build_ctx()))
    return TestClient(app)


def test_api_runs_endpoint(temp_runs_log: Path) -> None:
    from src.integrations._observability_log import record_agent_run

    record_agent_run(model="codex-cli/gpt-5", chat_id=111, prompt_text="hello")
    record_agent_run(model="google-gemini-cli/x", chat_id=222, prompt_text="world")

    client = _api_client()
    res = client.get("/api/observability/runs?limit=10")
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["count"] == 2
    assert len(data["runs"]) == 2


def test_api_runs_with_filter(temp_runs_log: Path) -> None:
    from src.integrations._observability_log import record_agent_run

    record_agent_run(model="codex-cli/x", chat_id=1)
    record_agent_run(model="gemini/y", chat_id=2)

    client = _api_client()
    res = client.get("/api/observability/runs?model=codex-cli")
    assert res.status_code == 200
    data = res.json()
    assert data["count"] == 1
    assert data["runs"][0]["chat_id"] == 1


def test_api_run_detail_endpoint(temp_runs_log: Path) -> None:
    from src.integrations._observability_log import record_agent_run

    rid = record_agent_run(model="m", prompt_text="hi", chat_id=7)
    client = _api_client()
    res = client.get(f"/api/observability/run/{rid}")
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["run"]["request_id"] == rid
    assert data["run"]["chat_id"] == 7


def test_api_run_detail_404(temp_runs_log: Path) -> None:
    client = _api_client()
    res = client.get("/api/observability/run/missing-id")
    assert res.status_code == 404


def _pages_client() -> TestClient:
    app = FastAPI()
    app.include_router(build_pages_router(_build_ctx()))
    return TestClient(app)


def test_observability_page_route_exists() -> None:
    client = _pages_client()
    res = client.get("/observability")
    assert res.status_code == 200
    assert "text/html" in res.headers.get("content-type", "")


def test_openclaw_register_is_best_effort(monkeypatch, temp_runs_log: Path) -> None:
    """OpenClaw register должен silently swallow network errors."""
    from src.integrations._observability_log import record_agent_run

    def _boom(req, timeout=None):
        raise OSError("connection refused")

    monkeypatch.setattr("urllib.request.urlopen", _boom)
    rid = record_agent_run(model="m", prompt_text="x")
    assert rid
    # Запись в jsonl всё равно должна произойти
    assert temp_runs_log.exists()
    assert len(temp_runs_log.read_text().strip().splitlines()) == 1
