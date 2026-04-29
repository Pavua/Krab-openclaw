# -*- coding: utf-8 -*-
"""
Cross-project distributed tracing: Main Krab → Krab Ear backend.

Проверяем:
1) Когда sentry_sdk активен и есть traceparent/baggage — headers пробрасываются
   в HTTP-вызов Ear backend.
2) Graceful degrade: если sentry_sdk не установлен или нет активного span —
   вызов работает нормально, headers пустые, exception не бросается.
"""

from __future__ import annotations

import sys
import types
from typing import Any
from unittest.mock import patch

import pytest

from src.integrations import krab_ear_client as ear_mod


class _FakeResponse:
    def __init__(self, status_code: int = 200, payload: dict[str, Any] | None = None) -> None:
        self.status_code = status_code
        self._payload = payload or {"status": "ok"}
        self.headers = {"content-type": "application/json"}
        self.content = b"{}"
        self.text = "{}"

    def json(self) -> dict[str, Any]:
        return self._payload


class _FakeAsyncClient:
    """Минимальный httpx.AsyncClient stub, запоминает последние headers."""

    last_headers: dict[str, str] | None = None

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    async def __aenter__(self) -> "_FakeAsyncClient":
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        return None

    async def get(self, _url: str, headers: dict[str, str] | None = None) -> _FakeResponse:
        _FakeAsyncClient.last_headers = dict(headers) if headers else None
        return _FakeResponse()


@pytest.mark.asyncio
async def test_trace_headers_added_when_sentry_active(monkeypatch: pytest.MonkeyPatch) -> None:
    """Когда sentry_sdk активен — sentry-trace и baggage уезжают в HTTP-запрос."""
    fake_sdk = types.ModuleType("sentry_sdk")
    fake_sdk.get_traceparent = lambda: "00-aaaabbbbccccddddeeeeffff00001111-2222333344445555-01"  # type: ignore[attr-defined]
    fake_sdk.get_baggage = lambda: (
        "sentry-trace_id=aaaabbbbccccddddeeeeffff00001111,sentry-public_key=abc"
    )  # type: ignore[attr-defined]
    fake_sdk.set_tag = lambda *_a, **_kw: None  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sentry_sdk", fake_sdk)

    client = ear_mod.KrabEarClient(
        base_url="http://127.0.0.1:9999", socket_path="/nonexistent.sock"
    )

    _FakeAsyncClient.last_headers = None
    with patch.object(ear_mod.httpx, "AsyncClient", _FakeAsyncClient):
        status_code, payload = await client._fetch_health_payload()

    assert status_code == 200
    assert payload.get("status") == "ok"
    assert _FakeAsyncClient.last_headers is not None
    assert _FakeAsyncClient.last_headers.get("sentry-trace", "").startswith("00-")
    assert "sentry-trace_id" in _FakeAsyncClient.last_headers.get("baggage", "")


@pytest.mark.asyncio
async def test_no_headers_when_sdk_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Если sentry_sdk не установлен — graceful: headers пустые, no raise."""
    # Блокируем импорт sentry_sdk на уровне sys.modules.
    monkeypatch.setitem(sys.modules, "sentry_sdk", None)

    # И убедимся, что helper сам по себе возвращает пустой dict.
    headers = ear_mod._get_sentry_trace_headers()
    assert headers == {}

    client = ear_mod.KrabEarClient(
        base_url="http://127.0.0.1:9999", socket_path="/nonexistent.sock"
    )
    _FakeAsyncClient.last_headers = None
    with patch.object(ear_mod.httpx, "AsyncClient", _FakeAsyncClient):
        status_code, _payload = await client._fetch_health_payload()

    assert status_code == 200
    # httpx.AsyncClient.get получает headers=None (нет Sentry → нет headers).
    assert _FakeAsyncClient.last_headers is None
