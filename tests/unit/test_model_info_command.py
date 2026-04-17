# -*- coding: utf-8 -*-
"""
Тесты для `!model info` (Session 11 feature #3).

Проверяем формирование Markdown-отчёта с активным маршрутом,
providers health и fallback chain. Тестируем graceful-деградацию,
когда cloud API недоступен или данные отсутствуют.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock

import pytest

import src.handlers.command_handlers as command_handlers_module
from src.handlers.command_handlers import _format_model_info

# ─────────────────────────────────────────────────────────────────────────────
# Вспомогательные моки
# ─────────────────────────────────────────────────────────────────────────────


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict:
        return self._payload


class _FakeAsyncClient:
    """Минимальный stub httpx.AsyncClient: только .get() возвращает fake response."""

    def __init__(self, status_code: int = 200, payload: dict | None = None) -> None:
        self._status = status_code
        self._payload = payload or {}

    async def __aenter__(self) -> "_FakeAsyncClient":
        return self

    async def __aexit__(self, *args, **kwargs) -> None:
        return None

    async def get(self, url: str) -> _FakeResponse:
        return _FakeResponse(self._status, self._payload)


def _patch_route(monkeypatch: pytest.MonkeyPatch, route: dict | None) -> None:
    monkeypatch.setattr(
        command_handlers_module.openclaw_client,
        "get_last_runtime_route",
        lambda: route or {},
    )


def _patch_cloud_http(
    monkeypatch: pytest.MonkeyPatch,
    *,
    status: int = 200,
    payload: dict | None = None,
    raise_exc: bool = False,
) -> None:
    if raise_exc:

        class _Broken:
            async def __aenter__(self):
                raise ConnectionError("cloud API down")

            async def __aexit__(self, *a, **k):
                return None

        monkeypatch.setattr(command_handlers_module.httpx, "AsyncClient", lambda *a, **k: _Broken())
    else:
        monkeypatch.setattr(
            command_handlers_module.httpx,
            "AsyncClient",
            lambda *a, **k: _FakeAsyncClient(status, payload),
        )


def _patch_fallback(monkeypatch: pytest.MonkeyPatch, chain: list[str]) -> None:
    import src.core.cloud_gateway as cloud_gateway_module

    monkeypatch.setattr(cloud_gateway_module, "get_cloud_fallback_chain", lambda: chain)


def _patch_lm_studio(monkeypatch: pytest.MonkeyPatch, available: bool) -> None:
    monkeypatch.setattr(
        command_handlers_module,
        "is_lm_studio_available",
        AsyncMock(return_value=available),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Тесты
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_format_with_last_route(monkeypatch: pytest.MonkeyPatch) -> None:
    """При полном route и cloud-report формирует все разделы Markdown-отчёта."""
    _patch_route(
        monkeypatch,
        {
            "timestamp": int(time.time()),
            "provider": "google",
            "model": "google/gemini-3-pro-preview",
            "active_tier": "free",
            "status": "ok",
        },
    )
    _patch_cloud_http(
        monkeypatch,
        status=200,
        payload={
            "available": True,
            "report": {
                "ok": True,
                "providers": {
                    "google": {
                        "ok": True,
                        "provider_status": "ok",
                        "http_status": 200,
                    }
                },
            },
        },
    )
    _patch_fallback(
        monkeypatch,
        [
            "google/gemini-3-pro-preview",
            "google/gemini-2.5-pro-preview",
            "gemini-2.5-flash",
        ],
    )
    _patch_lm_studio(monkeypatch, available=True)

    text = await _format_model_info()

    assert "🤖 **Model Info**" in text
    assert "**Active route:**" in text
    assert "`google`" in text
    assert "`google/gemini-3-pro-preview`" in text
    assert "`free`" in text
    assert "**Fallback chain:**" in text
    assert "1. google/gemini-3-pro-preview" in text
    assert "**Providers health:**" in text
    assert "google:" in text
    assert "LM Studio: ready" in text


@pytest.mark.asyncio
async def test_format_with_missing_data(monkeypatch: pytest.MonkeyPatch) -> None:
    """При пустом route и отсутствующих providers выводит 'n/a' вместо падения."""
    _patch_route(monkeypatch, {})
    _patch_cloud_http(monkeypatch, status=200, payload={"available": False})
    _patch_fallback(monkeypatch, [])
    _patch_lm_studio(monkeypatch, available=False)

    text = await _format_model_info()

    assert "🤖 **Model Info**" in text
    assert "`n/a`" in text
    # Fallback chain пустой
    assert "(пусто или недоступно)" in text
    # LM Studio недоступен
    assert "LM Studio: idle" in text


@pytest.mark.asyncio
async def test_providers_section_when_cloud_down(monkeypatch: pytest.MonkeyPatch) -> None:
    """Когда web-app down (exception на httpx.get), providers fallback-ит
    в graceful-сообщение, а не падает."""
    _patch_route(
        monkeypatch,
        {
            "timestamp": int(time.time()),
            "provider": "google",
            "model": "google/gemini-3-pro-preview",
            "active_tier": "free",
            "status": "ok",
        },
    )
    _patch_cloud_http(monkeypatch, raise_exc=True)
    _patch_fallback(monkeypatch, ["google/gemini-3-pro-preview"])
    _patch_lm_studio(monkeypatch, available=False)

    text = await _format_model_info()

    # Функция не упала и собрала маршрут
    assert "🤖 **Model Info**" in text
    assert "`google`" in text
    # Providers секция в graceful-режиме
    assert "**Providers health:**" in text
    assert "google:" in text
    # fallback сообщение про недоступный cloud API
    assert "недоступно" in text or "cloud API" in text
