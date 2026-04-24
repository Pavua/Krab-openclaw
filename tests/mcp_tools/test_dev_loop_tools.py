"""Тесты dev-loop MCP tools (sentry_status/resolve, run_e2e, log_tail, deploy_and_verify).

Стиль в стиле test_system_http_time.py: мокируем httpx.AsyncClient, subprocess.run,
и Path.read_text. Никаких внешних сетевых вызовов / запуска скриптов.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

# ── helpers ──────────────────────────────────────────────────────────────────


class _FakeResponse:
    def __init__(self, status=200, payload=None, text=""):
        self.status_code = status
        self._payload = payload if payload is not None else []
        self.text = text

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "err", request=MagicMock(), response=MagicMock(status_code=self.status_code)
            )


class _FakeAsyncClient:
    """Fake httpx.AsyncClient — возвращает очереди ответов по методу."""

    def __init__(self, get_resp=None, put_resp=None):
        self._get = get_resp if isinstance(get_resp, list) else [get_resp] if get_resp else []
        self._put = put_resp if isinstance(put_resp, list) else [put_resp] if put_resp else []
        self._gi = 0
        self._pi = 0

    def __call__(self, *args, **kwargs):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, params=None, headers=None, timeout=None):
        if not self._get:
            return _FakeResponse(200, [])
        r = self._get[min(self._gi, len(self._get) - 1)]
        self._gi += 1
        return r

    async def put(self, url, params=None, headers=None, json=None):
        if not self._put:
            return _FakeResponse(200, {})
        r = self._put[min(self._pi, len(self._put) - 1)]
        self._pi += 1
        return r


# ── krab_sentry_status ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sentry_status_missing_token_graceful(mcp_server, monkeypatch):
    monkeypatch.delenv("SENTRY_AUTH_TOKEN", raising=False)
    result = await mcp_server.krab_sentry_status(mcp_server._SentryStatusInput())
    data = json.loads(result)
    assert data["ok"] is False
    assert data["error"] == "SENTRY_AUTH_TOKEN_missing"


@pytest.mark.asyncio
async def test_sentry_status_aggregates_projects(mcp_server, monkeypatch):
    monkeypatch.setenv("SENTRY_AUTH_TOKEN", "fake")
    issues_payload = [
        {
            "shortId": "PYTHON-FASTAPI-1",
            "count": "42",
            "title": "KeyError foo",
            "culprit": "handler.py in foo",
            "permalink": "https://de.sentry.io/i/1",
            "id": "100",
        },
        {
            "shortId": "PYTHON-FASTAPI-2",
            "count": "7",
            "title": "TimeoutError",
            "culprit": "x",
            "permalink": "p",
            "id": "101",
        },
    ]
    fake = _FakeAsyncClient(get_resp=_FakeResponse(200, issues_payload))
    with patch.object(httpx, "AsyncClient", return_value=fake):
        result = await mcp_server.krab_sentry_status(
            mcp_server._SentryStatusInput(project="python-fastapi", statsPeriod="1h", limit=5)
        )
    data = json.loads(result)
    assert data["ok"] is True
    assert data["count_total"] == 2
    assert data["top"][0]["shortId"] == "PYTHON-FASTAPI-1"
    assert data["top"][0]["count"] == "42"
    assert data["by_project"]["python-fastapi"]["count"] == 2


# ── krab_sentry_resolve ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sentry_resolve_missing_token(mcp_server, monkeypatch):
    monkeypatch.delenv("SENTRY_AUTH_TOKEN", raising=False)
    result = await mcp_server.krab_sentry_resolve(
        mcp_server._SentryResolveInput(shortIds=["X-1"], project="python-fastapi")
    )
    data = json.loads(result)
    assert data["ok"] is False
    assert data["error"] == "SENTRY_AUTH_TOKEN_missing"


@pytest.mark.asyncio
async def test_sentry_resolve_happy_path(mcp_server, monkeypatch):
    monkeypatch.setenv("SENTRY_AUTH_TOKEN", "fake")
    # GET issues (для mapping shortId → numeric id)
    get_resp = _FakeResponse(
        200,
        [
            {"shortId": "PYTHON-FASTAPI-42", "id": "9042", "count": "1"},
            {"shortId": "PYTHON-FASTAPI-43", "id": "9043", "count": "1"},
        ],
    )
    # PUT resolve
    put_resp = _FakeResponse(200, {})
    fake = _FakeAsyncClient(get_resp=get_resp, put_resp=put_resp)
    with patch.object(httpx, "AsyncClient", return_value=fake):
        result = await mcp_server.krab_sentry_resolve(
            mcp_server._SentryResolveInput(
                shortIds=["PYTHON-FASTAPI-42", "PYTHON-FASTAPI-43"],
                project="python-fastapi",
            )
        )
    data = json.loads(result)
    assert data["ok"] is True
    assert data["resolved_count"] == 2
    assert {r["shortId"] for r in data["resolved"]} == {"PYTHON-FASTAPI-42", "PYTHON-FASTAPI-43"}


@pytest.mark.asyncio
async def test_sentry_resolve_not_found(mcp_server, monkeypatch):
    monkeypatch.setenv("SENTRY_AUTH_TOKEN", "fake")
    # GET возвращает пусто — значит shortId не найден
    fake = _FakeAsyncClient(get_resp=_FakeResponse(200, []))
    with patch.object(httpx, "AsyncClient", return_value=fake):
        result = await mcp_server.krab_sentry_resolve(
            mcp_server._SentryResolveInput(
                shortIds=["PYTHON-FASTAPI-999"], project="python-fastapi"
            )
        )
    data = json.loads(result)
    assert len(data["failed"]) == 1
    assert data["failed"][0]["error"] == "not_found"
    assert data["resolved_count"] == 0


# ── krab_run_e2e ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_e2e_script_missing(mcp_server, monkeypatch, tmp_path):
    monkeypatch.setattr(mcp_server, "_E2E_SMOKE_SCRIPT", tmp_path / "nonexistent.py")
    result = await mcp_server.krab_run_e2e(mcp_server._RunE2EInput())
    data = json.loads(result)
    assert data["ok"] is False
    assert "script not found" in data["error"]


@pytest.mark.asyncio
async def test_run_e2e_parses_pass_fail(mcp_server, monkeypatch, tmp_path):
    # Создаём фейковый скрипт-файл чтобы пройти existence check
    fake_script = tmp_path / "e2e_mcp_smoke.py"
    fake_script.write_text("# noop")
    monkeypatch.setattr(mcp_server, "_E2E_SMOKE_SCRIPT", fake_script)

    fake_stdout = (
        "PASS  version_cmd\nPASS  uptime_cmd\nFAIL  silence_status\nИтого: 2/3 passed (3.1s)\n"
    )
    fake_proc = MagicMock(returncode=0, stdout=fake_stdout, stderr="")
    with patch.object(subprocess, "run", return_value=fake_proc):
        result = await mcp_server.krab_run_e2e(mcp_server._RunE2EInput())
    data = json.loads(result)
    assert data["passed"] == 2
    assert data["failed"] == 1
    names = {c["name"] for c in data["cases"]}
    assert "version_cmd" in names and "silence_status" in names


@pytest.mark.asyncio
async def test_run_e2e_timeout_graceful(mcp_server, monkeypatch, tmp_path):
    fake_script = tmp_path / "e2e_mcp_smoke.py"
    fake_script.write_text("# noop")
    monkeypatch.setattr(mcp_server, "_E2E_SMOKE_SCRIPT", fake_script)

    def _raise_timeout(*a, **kw):
        raise subprocess.TimeoutExpired(cmd="python", timeout=300)

    with patch.object(subprocess, "run", side_effect=_raise_timeout):
        result = await mcp_server.krab_run_e2e(mcp_server._RunE2EInput())
    data = json.loads(result)
    assert data["ok"] is False
    assert data["error"] == "timeout_300s"


# ── krab_log_tail ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_log_tail_missing_file(mcp_server, monkeypatch, tmp_path):
    monkeypatch.setattr(mcp_server, "_KRAB_LAUNCHD_LOG", tmp_path / "no.log")
    result = await mcp_server.krab_log_tail(mcp_server._LogTailInput())
    data = json.loads(result)
    assert data["ok"] is False
    assert "log not found" in data["error"]


@pytest.mark.asyncio
async def test_log_tail_filters_warn_error(mcp_server, monkeypatch, tmp_path):
    log = tmp_path / "krab_launchd.out.log"
    log.write_text(
        "2026-04-24 INFO startup ok\n"
        "2026-04-24 WARNING flaky provider\n"
        "2026-04-24 INFO noise\n"
        "2026-04-24 ERROR ConnectionRefused openclaw\n"
        "2026-04-24 DEBUG detail\n"
        "2026-04-24 CRITICAL Traceback boom\n"
    )
    monkeypatch.setattr(mcp_server, "_KRAB_LAUNCHD_LOG", log)
    result = await mcp_server.krab_log_tail(mcp_server._LogTailInput(level="warn+error", n=50))
    data = json.loads(result)
    assert data["ok"] is True
    # ожидаем 3 строки: WARNING, ERROR, CRITICAL
    assert data["count"] == 3
    joined = "\n".join(data["lines"])
    assert "WARNING" in joined and "ERROR" in joined and "CRITICAL" in joined
    assert "DEBUG detail" not in joined


@pytest.mark.asyncio
async def test_log_tail_regex_filter(mcp_server, monkeypatch, tmp_path):
    log = tmp_path / "krab_launchd.out.log"
    log.write_text("ERROR foo bar\nERROR baz qux\nERROR hello world\nWARN hello other\n")
    monkeypatch.setattr(mcp_server, "_KRAB_LAUNCHD_LOG", log)
    result = await mcp_server.krab_log_tail(
        mcp_server._LogTailInput(pattern=r"hello", level="all", n=10)
    )
    data = json.loads(result)
    assert data["count"] == 2
    assert all("hello" in line for line in data["lines"])


@pytest.mark.asyncio
async def test_log_tail_strips_ansi(mcp_server, monkeypatch, tmp_path):
    log = tmp_path / "krab_launchd.out.log"
    log.write_text("\x1b[31mERROR red thing\x1b[0m\n")
    monkeypatch.setattr(mcp_server, "_KRAB_LAUNCHD_LOG", log)
    result = await mcp_server.krab_log_tail(mcp_server._LogTailInput(level="error", n=5))
    data = json.loads(result)
    assert data["count"] == 1
    assert "\x1b[" not in data["lines"][0]
    assert "ERROR red thing" in data["lines"][0]


# ── krab_deploy_and_verify ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_deploy_skip_tests(mcp_server, monkeypatch):
    # git push → ok
    async def fake_push():
        return {"branch": "fix/x", "exit_code": 0, "output": "Everything up-to-date"}

    # launcher stop/start → ok
    async def fake_stop(*a, **kw):
        return {"ok": True, "exit_code": 0, "output": "stopped"}

    async def fake_start(*a, **kw):
        return {"ok": True, "exit_code": 0, "output": "started"}

    async def fake_wait():
        return {"ok": True, "elapsed_s": 5.0}

    monkeypatch.setattr(mcp_server, "_git_push_current_branch", fake_push)

    async def fake_launcher(path, timeout=60):
        if "Stop" in str(path):
            return await fake_stop()
        return await fake_start()

    monkeypatch.setattr(mcp_server, "_run_launcher", fake_launcher)

    async def fake_wait_up(max_seconds=120):
        return await fake_wait()

    monkeypatch.setattr(mcp_server, "_wait_for_up", fake_wait_up)

    # asyncio.sleep → моментально
    async def instant_sleep(_):
        return None

    monkeypatch.setattr(mcp_server.asyncio, "sleep", instant_sleep)

    result = await mcp_server.krab_deploy_and_verify(mcp_server._DeployVerifyInput(skip_tests=True))
    data = json.loads(result)
    assert data["ok"] is True
    assert data["e2e"] == {"skipped": True}
    assert data["health"]["ok"] is True


@pytest.mark.asyncio
async def test_deploy_health_failure_sets_suggestion(mcp_server, monkeypatch):
    async def fake_push():
        return {"branch": "fix/x", "exit_code": 0, "output": "ok"}

    async def fake_launcher(path, timeout=60):
        return {"ok": True, "exit_code": 0, "output": ""}

    async def fake_wait_up(max_seconds=120):
        return {"ok": False, "error": "timeout", "waited_s": 120}

    async def instant_sleep(_):
        return None

    monkeypatch.setattr(mcp_server, "_git_push_current_branch", fake_push)
    monkeypatch.setattr(mcp_server, "_run_launcher", fake_launcher)
    monkeypatch.setattr(mcp_server, "_wait_for_up", fake_wait_up)
    monkeypatch.setattr(mcp_server.asyncio, "sleep", instant_sleep)

    result = await mcp_server.krab_deploy_and_verify(
        mcp_server._DeployVerifyInput(skip_tests=False)
    )
    data = json.loads(result)
    assert data["ok"] is False
    assert data["e2e"]["skipped"] is True
    assert data["e2e"]["reason"] == "health_not_up"
    assert any("Health" in s or "health" in s for s in data["suggestions"])
