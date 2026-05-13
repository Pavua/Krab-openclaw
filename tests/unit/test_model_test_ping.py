# -*- coding: utf-8 -*-
"""
Unit tests for ``POST /api/admin/model/test_ping`` — Wave 232 (replay
of deferred Wave 224).

Реальный probe-endpoint в ``src.modules.web_routers.models_admin_router``.
Покрытие:
- Happy path для MLX local: latency_ms / tokens_per_sec_estimated / preview.
- MLX alias resolution (короткий id → resolved_model = полный путь).
- Reasoning fallback (Wave 221): content="", reasoning="hello" → used_reasoning_fallback=True.
- Query param fallback (?model_id=... без body).
- Env override KRAB_TEST_PING_MLX_LOCAL_KV4_URL.
- Unsupported provider → 500 + unsupported_provider.
- Missing model_id → 400 + model_id_required.
- connect_failed → 500 stage=connect.
- http_error (500 от backend) → 500 stage=http_error.
- parse_error (мусор вместо JSON) → 500 stage=parse.
- Write-access enforcement: WEB_API_KEY set без header/token → 403.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.modules.web_routers._context import RouterContext
from src.modules.web_routers.models_admin_router import build_models_admin_router

# ── Helpers ────────────────────────────────────────────────────────────────


def _build_ctx() -> RouterContext:
    """Минимальный RouterContext (test_ping не использует LM Studio probe)."""
    deps: dict[str, Any] = {"router": object()}
    return RouterContext(
        deps=deps,
        project_root=Path("/tmp"),
        web_api_key_fn=lambda: "",
        assert_write_access_fn=lambda *_a, **_kw: None,
    )


def _client() -> TestClient:
    """FastAPI TestClient с одним зарегистрированным models_admin_router."""
    app = FastAPI()
    app.include_router(build_models_admin_router(_build_ctx()))
    return TestClient(app)


class _FakeResponse:
    """Минимальная замена ``httpx.Response`` для AsyncClient.post."""

    def __init__(
        self,
        *,
        status_code: int = 200,
        json_data: dict[str, Any] | None = None,
        text: str = "",
    ) -> None:
        self.status_code = status_code
        self._json = json_data if json_data is not None else {}
        # text — fallback и для http_error пути.
        self.text = text or ("" if json_data is None else __import__("json").dumps(json_data))

    def json(self) -> dict[str, Any]:
        return self._json


class _FakeAsyncClient:
    """Заглушка httpx.AsyncClient: возвращает заданный response / raises."""

    def __init__(
        self,
        *,
        response: _FakeResponse | None = None,
        exc: Exception | None = None,
        on_post=None,
    ) -> None:
        self._response = response
        self._exc = exc
        self._on_post = on_post  # callable(url, payload, headers) для inspection
        self.last_url: str | None = None
        self.last_payload: dict[str, Any] | None = None
        self.last_headers: dict[str, str] | None = None

    async def __aenter__(self) -> "_FakeAsyncClient":
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        return None

    async def post(
        self,
        url: str,
        *,
        json: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> _FakeResponse:
        self.last_url = url
        self.last_payload = json
        self.last_headers = headers
        if self._on_post is not None:
            self._on_post(url, json, headers)
        if self._exc is not None:
            raise self._exc
        return self._response or _FakeResponse()


def _patch_httpx_with(fake: _FakeAsyncClient):
    """Возвращает контекст-менеджер с подменой ``httpx.AsyncClient`` внутри
    endpoint'а (импорт через ``import httpx`` в module-scope)."""

    def _factory(*_args: Any, **_kw: Any) -> _FakeAsyncClient:
        return fake

    return patch(
        "src.modules.web_routers.models_admin_router.httpx.AsyncClient",
        _factory,
    )


# ── Tests ──────────────────────────────────────────────────────────────────


def test_test_ping_happy_path_mlx_local(monkeypatch: pytest.MonkeyPatch) -> None:
    """MLX local: 200 OK, latency_ms > 0, response_preview = "pong"."""
    monkeypatch.delenv("WEB_API_KEY", raising=False)
    fake = _FakeAsyncClient(
        response=_FakeResponse(
            json_data={
                "choices": [{"message": {"content": "pong"}}],
                "usage": {"completion_tokens": 1},
            }
        )
    )
    client = _client()
    with _patch_httpx_with(fake):
        resp = client.post(
            "/api/admin/model/test_ping",
            json={"model_id": "mlx-local-kv4/gemma-4-26b"},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["model_id"] == "mlx-local-kv4/gemma-4-26b"
    assert body["provider"] == "mlx-local-kv4"
    assert body["response_preview"] == "pong"
    assert body["response_chars"] == 4
    assert body["tokens_estimated"] == 1
    assert body["latency_ms"] >= 0
    assert body["used_reasoning_fallback"] is False
    # Backend URL — :8088 по дефолту.
    assert ":8088" in body["resolved_url"]


def test_test_ping_resolves_mlx_alias_to_full_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Wave 225: короткий id переписан в payload на полный путь каталога."""
    monkeypatch.delenv("WEB_API_KEY", raising=False)
    fake = _FakeAsyncClient(
        response=_FakeResponse(json_data={"choices": [{"message": {"content": "ok"}}]})
    )
    client = _client()
    with _patch_httpx_with(fake):
        resp = client.post(
            "/api/admin/model/test_ping",
            json={"model_id": "mlx-local-kv4/gemma-4-26b"},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # resolved_model должен быть полным путём (из _DEFAULT_ALIASES).
    assert body["resolved_model"].startswith("/Volumes/")
    # payload, отправленный в backend, тоже содержит полный путь.
    assert fake.last_payload is not None
    assert fake.last_payload["model"].startswith("/Volumes/")
    # chat_template_args.enable_thinking=false добавлен (Wave 221).
    assert fake.last_payload.get("chat_template_args") == {"enable_thinking": False}


def test_test_ping_reasoning_fallback_when_content_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Wave 221: content='', reasoning='hello' → preview='hello',
    used_reasoning_fallback=True."""
    monkeypatch.delenv("WEB_API_KEY", raising=False)
    fake = _FakeAsyncClient(
        response=_FakeResponse(
            json_data={"choices": [{"message": {"content": "", "reasoning": "thinking-pong"}}]}
        )
    )
    client = _client()
    with _patch_httpx_with(fake):
        resp = client.post(
            "/api/admin/model/test_ping",
            json={"model_id": "mlx-local-kv4/gemma-4-26b"},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["response_preview"] == "thinking-pong"
    assert body["used_reasoning_fallback"] is True


def test_test_ping_query_param_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """Если body пуст, model_id берётся из ?model_id=..."""
    monkeypatch.delenv("WEB_API_KEY", raising=False)
    fake = _FakeAsyncClient(
        response=_FakeResponse(json_data={"choices": [{"message": {"content": "x"}}]})
    )
    client = _client()
    with _patch_httpx_with(fake):
        resp = client.post(
            "/api/admin/model/test_ping?model_id=mlx-local-kv4/gemma-4-26b",
            json={},
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["model_id"] == "mlx-local-kv4/gemma-4-26b"


def test_test_ping_env_override_for_backend_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """KRAB_TEST_PING_MLX_LOCAL_KV4_URL переопределяет дефолт."""
    monkeypatch.delenv("WEB_API_KEY", raising=False)
    monkeypatch.setenv("KRAB_TEST_PING_MLX_LOCAL_KV4_URL", "http://override.local:9090")
    fake = _FakeAsyncClient(
        response=_FakeResponse(json_data={"choices": [{"message": {"content": "ok"}}]})
    )
    client = _client()
    with _patch_httpx_with(fake):
        resp = client.post(
            "/api/admin/model/test_ping",
            json={"model_id": "mlx-local-kv4/gemma-4-26b"},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["resolved_url"] == "http://override.local:9090"
    assert fake.last_url == "http://override.local:9090/v1/chat/completions"


def test_test_ping_unsupported_provider_returns_500(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """model_id с unknown prefix → 500 unsupported_provider."""
    monkeypatch.delenv("WEB_API_KEY", raising=False)
    client = _client()
    resp = client.post(
        "/api/admin/model/test_ping",
        json={"model_id": "unknown-provider/foo"},
    )
    assert resp.status_code == 500
    assert "unsupported_provider" in resp.json()["detail"]


def test_test_ping_missing_model_id_returns_400(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Без model_id → 400 model_id_required."""
    monkeypatch.delenv("WEB_API_KEY", raising=False)
    client = _client()
    resp = client.post("/api/admin/model/test_ping", json={})
    assert resp.status_code == 400
    assert resp.json()["detail"] == "model_id_required"


def test_test_ping_connect_failed_returns_500_with_stage_connect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ConnectError → 500 stage=connect."""
    monkeypatch.delenv("WEB_API_KEY", raising=False)
    fake = _FakeAsyncClient(exc=httpx.ConnectError("connection refused"))
    client = _client()
    with _patch_httpx_with(fake):
        resp = client.post(
            "/api/admin/model/test_ping",
            json={"model_id": "mlx-local-kv4/gemma-4-26b"},
        )
    assert resp.status_code == 500
    detail = resp.json()["detail"]
    assert isinstance(detail, dict)
    assert detail["stage"] == "connect"
    assert "connection refused" in detail["error"]


def test_test_ping_http_error_returns_500_with_stage_http_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Backend 500 → 500 stage=http_error + body preview."""
    monkeypatch.delenv("WEB_API_KEY", raising=False)
    fake = _FakeAsyncClient(response=_FakeResponse(status_code=500, text="internal model error"))
    client = _client()
    with _patch_httpx_with(fake):
        resp = client.post(
            "/api/admin/model/test_ping",
            json={"model_id": "mlx-local-kv4/gemma-4-26b"},
        )
    assert resp.status_code == 500
    detail = resp.json()["detail"]
    assert detail["stage"] == "http_error"
    assert detail["status"] == 500
    assert "internal model error" in detail["body"]


def test_test_ping_parse_error_returns_500_with_stage_parse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """200 OK но мусор вместо JSON → 500 stage=parse."""
    monkeypatch.delenv("WEB_API_KEY", raising=False)

    class _BrokenJsonResponse(_FakeResponse):
        def json(self) -> dict[str, Any]:
            raise ValueError("not json")

    fake = _FakeAsyncClient(response=_BrokenJsonResponse(status_code=200, text="<html>err</html>"))
    client = _client()
    with _patch_httpx_with(fake):
        resp = client.post(
            "/api/admin/model/test_ping",
            json={"model_id": "mlx-local-kv4/gemma-4-26b"},
        )
    assert resp.status_code == 500
    detail = resp.json()["detail"]
    assert detail["stage"] == "parse"


def test_test_ping_write_access_enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    """WEB_API_KEY set, без header/token → 403."""
    monkeypatch.setenv("WEB_API_KEY", "secret-key")
    client = _client()
    resp = client.post(
        "/api/admin/model/test_ping",
        json={"model_id": "mlx-local-kv4/gemma-4-26b"},
    )
    assert resp.status_code == 403


def test_test_ping_cloud_provider_uses_gateway_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cloud провайдер (google-vertex) идёт через Gateway, не через :8088."""
    monkeypatch.delenv("WEB_API_KEY", raising=False)
    fake = _FakeAsyncClient(
        response=_FakeResponse(
            json_data={
                "choices": [{"message": {"content": "pong"}}],
                "usage": {"completion_tokens": 1},
            }
        )
    )
    client = _client()
    with _patch_httpx_with(fake):
        resp = client.post(
            "/api/admin/model/test_ping",
            json={"model_id": "google-vertex/gemini-3-pro-preview"},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Gateway по дефолту :18789, точно не :8088.
    assert ":8088" not in body["resolved_url"]
    # MLX-specific chat_template_args НЕ применяются для cloud.
    assert fake.last_payload is not None
    assert "chat_template_args" not in fake.last_payload
