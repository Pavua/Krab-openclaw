# -*- coding: utf-8 -*-
"""
Тесты ``src.modules.web_routers.spawn_admin_router`` — Wave 234.

Покрывают: success-path через openclaw, success-path через direct-8088,
backend switch, rate-limit, history persist + read, error-path,
unknown backend fallback, маскировку длинных промптов, валидацию
input, защиту write-access.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, AsyncIterator
from unittest.mock import patch

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from src.modules.web_routers import spawn_admin_router as sar
from src.modules.web_routers._context import RouterContext
from src.modules.web_routers.spawn_admin_router import build_spawn_admin_router

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeOpenClawClient:
    """Минимальный stub openclaw_client.send_message_stream."""

    def __init__(self, chunks: list[str], record: dict[str, Any] | None = None) -> None:
        self._chunks = chunks
        self._record = record if record is not None else {}

    async def send_message_stream(
        self,
        *,
        message: str,
        chat_id: str,
        system_prompt: Any = None,
        images: Any = None,
        force_cloud: bool = False,
        preferred_model: Any = None,
        max_output_tokens: Any = None,
        disable_tools: bool = False,
    ) -> AsyncIterator[str]:
        self._record["message"] = message
        self._record["chat_id"] = chat_id
        self._record["force_cloud"] = force_cloud
        self._record["preferred_model"] = preferred_model
        self._record["disable_tools"] = disable_tools
        for c in self._chunks:
            yield c


def _make_client(
    *,
    openclaw: Any = None,
    write_access_raises: Exception | None = None,
    rate_state: dict[str, Any] | None = None,
    tmp_history: Path | None = None,
) -> TestClient:
    def _assert_write(*a: Any, **kw: Any) -> None:
        if write_access_raises is not None:
            raise write_access_raises

    deps: dict[str, Any] = {}
    if openclaw is not None:
        deps["openclaw_client"] = openclaw

    ctx = RouterContext(
        deps=deps,
        project_root=Path("."),
        web_api_key_fn=lambda: None,
        assert_write_access_fn=_assert_write,
        rate_state=rate_state if rate_state is not None else {},
    )
    app = FastAPI()
    app.include_router(build_spawn_admin_router(ctx))

    # Если указан tmp_history — подменяем canonical path на временный.
    if tmp_history is not None:
        patcher = patch.object(sar, "_HISTORY_PATH", tmp_history)
        patcher.start()
        client = TestClient(app)
        client._spawn_history_patcher = patcher  # type: ignore[attr-defined]
        return client
    return TestClient(app)


@pytest.fixture
def tmp_history(tmp_path: Path) -> Path:
    return tmp_path / "spawn_history.jsonl"


# ---------------------------------------------------------------------------
# _mask_prompt
# ---------------------------------------------------------------------------


def test_mask_prompt_short_kept_as_is() -> None:
    s = "Hello world"
    assert sar._mask_prompt(s) == s


def test_mask_prompt_long_truncated_with_ellipsis() -> None:
    s = "x" * 1000
    masked = sar._mask_prompt(s)
    assert len(masked) == sar._PROMPT_MASK_LENGTH + 1  # +1 для "…"
    assert masked.endswith("…")


def test_mask_prompt_non_string_returns_empty() -> None:
    assert sar._mask_prompt(None) == ""  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# _normalize_backend
# ---------------------------------------------------------------------------


def test_normalize_backend_known() -> None:
    assert sar._normalize_backend("mlx-local-kv4") == "mlx-local-kv4"
    assert sar._normalize_backend("Primary") == "primary"


def test_normalize_backend_unknown_falls_back_to_primary() -> None:
    assert sar._normalize_backend("evil-backdoor") == "primary"
    assert sar._normalize_backend("") == "primary"


# ---------------------------------------------------------------------------
# _rate_check_and_record
# ---------------------------------------------------------------------------


def test_rate_limit_allows_up_to_limit() -> None:
    state: dict[str, Any] = {}
    for _ in range(sar._RATE_LIMIT):
        sar._rate_check_and_record(state)
    # Следующий должен сорваться.
    with pytest.raises(HTTPException) as exc:
        sar._rate_check_and_record(state)
    assert exc.value.status_code == 429


def test_rate_limit_resets_after_window() -> None:
    import time as _t

    state: dict[str, Any] = {sar._RATE_KEY: [_t.time() - 999.0] * sar._RATE_LIMIT}
    # Все старые → должны быть отброшены, новый запрос проходит.
    sar._rate_check_and_record(state)
    assert len(state[sar._RATE_KEY]) == 1


# ---------------------------------------------------------------------------
# history read/append
# ---------------------------------------------------------------------------


def test_append_and_read_history_roundtrip(tmp_history: Path) -> None:
    with patch.object(sar, "_HISTORY_PATH", tmp_history):
        sar._append_history({"history_id": "a", "ok": True, "ts": 1.0})
        sar._append_history({"history_id": "b", "ok": False, "ts": 2.0})
        rows = sar._read_history(10)
    # Свежее сверху → "b" первым.
    assert [r["history_id"] for r in rows] == ["b", "a"]


def test_read_history_limit_truncates(tmp_history: Path) -> None:
    with patch.object(sar, "_HISTORY_PATH", tmp_history):
        for i in range(15):
            sar._append_history({"history_id": str(i), "ok": True, "ts": float(i)})
        rows = sar._read_history(5)
    assert len(rows) == 5
    # Самые свежие — 14..10
    assert rows[0]["history_id"] == "14"
    assert rows[-1]["history_id"] == "10"


def test_read_history_skips_bad_lines(tmp_history: Path) -> None:
    tmp_history.parent.mkdir(parents=True, exist_ok=True)
    tmp_history.write_text(
        '{"history_id": "ok1", "ok": true}\n{garbage\n\n{"history_id": "ok2", "ok": true}\n',
        encoding="utf-8",
    )
    with patch.object(sar, "_HISTORY_PATH", tmp_history):
        rows = sar._read_history(10)
    assert {r["history_id"] for r in rows} == {"ok1", "ok2"}


def test_read_history_empty_when_missing(tmp_path: Path) -> None:
    missing = tmp_path / "absent.jsonl"
    with patch.object(sar, "_HISTORY_PATH", missing):
        assert sar._read_history() == []


# ---------------------------------------------------------------------------
# POST /api/admin/spawn/run — happy paths
# ---------------------------------------------------------------------------


def test_spawn_run_primary_success(tmp_history: Path) -> None:
    record: dict[str, Any] = {}
    openclaw = _FakeOpenClawClient(["Hello", " world!"], record=record)
    client = _make_client(openclaw=openclaw, tmp_history=tmp_history)

    resp = client.post(
        "/api/admin/spawn/run",
        json={"prompt": "Ping?", "backend": "primary", "tools_enabled": False},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["response_text"] == "Hello world!"
    assert body["backend"] == "primary"
    assert body["latency_ms"] >= 0
    assert body["tokens_used"] >= 1
    assert body["history_id"]
    # preferred_model не передан (primary), force_cloud False, disable_tools True.
    assert record["preferred_model"] is None
    assert record["force_cloud"] is False
    assert record["disable_tools"] is True


def test_spawn_run_mlx_local_passes_preferred_model(tmp_history: Path) -> None:
    record: dict[str, Any] = {}
    openclaw = _FakeOpenClawClient(["mlx-output"], record=record)
    client = _make_client(openclaw=openclaw, tmp_history=tmp_history)

    resp = client.post(
        "/api/admin/spawn/run",
        json={"prompt": "test mlx", "backend": "mlx-local-kv4", "tools_enabled": True},
    )

    assert resp.status_code == 200
    assert resp.json()["backend"] == "mlx-local-kv4"
    assert record["preferred_model"] == sar._MLX_LOCAL_MODEL
    assert record["force_cloud"] is False
    # tools_enabled=True → disable_tools должен быть False.
    assert record["disable_tools"] is False


def test_spawn_run_openclaw_cloud_sets_force_cloud(tmp_history: Path) -> None:
    record: dict[str, Any] = {}
    openclaw = _FakeOpenClawClient(["cloud-response"], record=record)
    client = _make_client(openclaw=openclaw, tmp_history=tmp_history)

    resp = client.post(
        "/api/admin/spawn/run",
        json={"prompt": "cloud", "backend": "openclaw-cloud"},
    )

    assert resp.status_code == 200
    assert record["force_cloud"] is True


def test_spawn_run_unknown_backend_falls_back_to_primary(tmp_history: Path) -> None:
    record: dict[str, Any] = {}
    openclaw = _FakeOpenClawClient(["ok"], record=record)
    client = _make_client(openclaw=openclaw, tmp_history=tmp_history)

    resp = client.post(
        "/api/admin/spawn/run",
        json={"prompt": "?", "backend": "evil-foo"},
    )
    assert resp.status_code == 200
    assert resp.json()["backend"] == "primary"
    assert record["preferred_model"] is None
    assert record["force_cloud"] is False


def test_spawn_run_direct_8088_bypasses_openclaw(tmp_history: Path) -> None:
    # openclaw намеренно None — direct path не должен его трогать.
    client = _make_client(openclaw=None, tmp_history=tmp_history)

    def _fake_post(url: str, body: dict) -> httpx.Response:  # noqa: ARG001
        assert url == sar._DIRECT_8088_URL
        payload = {
            "choices": [{"message": {"content": "direct hello"}}],
            "model": "direct-8088-test",
        }
        return httpx.Response(200, json=payload)

    async def _mock_async_post(self: Any, url: str, json: dict) -> httpx.Response:  # noqa: ARG001
        return _fake_post(url, json)

    with patch.object(httpx.AsyncClient, "post", new=_mock_async_post):
        resp = client.post(
            "/api/admin/spawn/run",
            json={"prompt": "ping", "backend": "direct-8088", "tools_enabled": True},
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["response_text"] == "direct hello"
    assert body["backend"] == "direct-8088"
    # tools_enabled должен быть форс-disabled для direct backend.
    # (meta.tools_enabled = False — но мы это не возвращаем явно, проверим через
    # отсутствие openclaw call: openclaw=None и success → не упало).


def test_spawn_run_persists_to_history(tmp_history: Path) -> None:
    openclaw = _FakeOpenClawClient(["resp"])
    client = _make_client(openclaw=openclaw, tmp_history=tmp_history)

    resp = client.post(
        "/api/admin/spawn/run",
        json={"prompt": "save me", "backend": "primary"},
    )
    assert resp.status_code == 200

    # Прочитаем через тот же patched _HISTORY_PATH.
    with patch.object(sar, "_HISTORY_PATH", tmp_history):
        rows = sar._read_history(10)
    assert len(rows) == 1
    assert rows[0]["ok"] is True
    assert rows[0]["prompt"] == "save me"
    assert rows[0]["backend"] == "primary"


def test_spawn_run_masks_long_prompt_in_history(tmp_history: Path) -> None:
    long_prompt = "Q" * 800
    openclaw = _FakeOpenClawClient(["ok"])
    client = _make_client(openclaw=openclaw, tmp_history=tmp_history)
    resp = client.post(
        "/api/admin/spawn/run",
        json={"prompt": long_prompt, "backend": "primary"},
    )
    assert resp.status_code == 200
    with patch.object(sar, "_HISTORY_PATH", tmp_history):
        rows = sar._read_history(10)
    assert rows[0]["prompt"].endswith("…")
    assert len(rows[0]["prompt"]) == sar._PROMPT_MASK_LENGTH + 1
    assert rows[0]["prompt_chars"] == 800


# ---------------------------------------------------------------------------
# POST /api/admin/spawn/run — error paths
# ---------------------------------------------------------------------------


def test_spawn_run_rejects_empty_prompt(tmp_history: Path) -> None:
    openclaw = _FakeOpenClawClient(["x"])
    client = _make_client(openclaw=openclaw, tmp_history=tmp_history)
    resp = client.post(
        "/api/admin/spawn/run",
        json={"prompt": "", "backend": "primary"},
    )
    # Pydantic min_length=1 → 422.
    assert resp.status_code == 422


def test_spawn_run_blocked_when_write_access_denied(tmp_history: Path) -> None:
    openclaw = _FakeOpenClawClient(["x"])
    client = _make_client(
        openclaw=openclaw,
        write_access_raises=HTTPException(status_code=403, detail="forbidden"),
        tmp_history=tmp_history,
    )
    resp = client.post(
        "/api/admin/spawn/run",
        json={"prompt": "hi", "backend": "primary"},
    )
    assert resp.status_code == 403


def test_spawn_run_rate_limit_returns_429(tmp_history: Path) -> None:
    openclaw = _FakeOpenClawClient(["ok"])
    state: dict[str, Any] = {}
    client = _make_client(openclaw=openclaw, rate_state=state, tmp_history=tmp_history)

    # Прогоним _RATE_LIMIT successful запросов.
    for i in range(sar._RATE_LIMIT):
        r = client.post(
            "/api/admin/spawn/run",
            json={"prompt": f"q{i}", "backend": "primary"},
        )
        assert r.status_code == 200, r.text

    # Следующий — 429.
    r = client.post(
        "/api/admin/spawn/run",
        json={"prompt": "over", "backend": "primary"},
    )
    assert r.status_code == 429
    assert "spawn_rate_limit" in r.json()["detail"]


def test_spawn_run_openclaw_unavailable_returns_503(tmp_history: Path) -> None:
    client = _make_client(openclaw=None, tmp_history=tmp_history)
    resp = client.post(
        "/api/admin/spawn/run",
        json={"prompt": "hello", "backend": "primary"},
    )
    assert resp.status_code == 503
    assert "openclaw_client_unavailable" in resp.json()["detail"]


def test_spawn_run_error_records_failure_in_history(tmp_history: Path) -> None:
    client = _make_client(openclaw=None, tmp_history=tmp_history)
    resp = client.post(
        "/api/admin/spawn/run",
        json={"prompt": "x", "backend": "primary"},
    )
    assert resp.status_code == 503
    with patch.object(sar, "_HISTORY_PATH", tmp_history):
        rows = sar._read_history(10)
    assert len(rows) == 1
    assert rows[0]["ok"] is False
    assert "openclaw_client_unavailable" in rows[0]["error"]


def test_spawn_run_direct_8088_handles_network_error(tmp_history: Path) -> None:
    client = _make_client(openclaw=None, tmp_history=tmp_history)

    async def _explode(self: Any, url: str, json: dict) -> httpx.Response:  # noqa: ARG001, ARG002
        raise httpx.ConnectError("no route to host")

    with patch.object(httpx.AsyncClient, "post", new=_explode):
        resp = client.post(
            "/api/admin/spawn/run",
            json={"prompt": "x", "backend": "direct-8088"},
        )
    assert resp.status_code == 502
    assert "direct_8088_unreachable" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# GET /api/admin/spawn/history
# ---------------------------------------------------------------------------


def test_history_endpoint_returns_entries(tmp_history: Path) -> None:
    client = _make_client(tmp_history=tmp_history)
    # Прямой append (без spawn_run).
    sar._append_history({"history_id": "h1", "ok": True, "ts": 1.0, "backend": "primary"})
    sar._append_history({"history_id": "h2", "ok": False, "ts": 2.0, "backend": "primary"})
    resp = client.get("/api/admin/spawn/history")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["count"] == 2
    assert [r["history_id"] for r in body["history"]] == ["h2", "h1"]


def test_history_endpoint_empty(tmp_history: Path) -> None:
    client = _make_client(tmp_history=tmp_history)
    resp = client.get("/api/admin/spawn/history")
    assert resp.status_code == 200
    assert resp.json()["count"] == 0


# ---------------------------------------------------------------------------
# GET /admin/spawn — HTML
# ---------------------------------------------------------------------------


def test_admin_spawn_page_returns_html() -> None:
    client = _make_client()
    resp = client.get("/admin/spawn")
    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "")
    assert "Spawn Admin" in resp.text
    # JS вызывает оба endpoint'а.
    assert "/api/admin/spawn/run" in resp.text
    assert "/api/admin/spawn/history" in resp.text
    # Каждый backend представлен в select.
    assert "mlx-local-kv4" in resp.text
    assert "direct-8088" in resp.text
