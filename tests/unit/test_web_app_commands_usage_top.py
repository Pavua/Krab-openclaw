# -*- coding: utf-8 -*-
"""
Тесты для GET /api/commands/usage/top endpoint.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.modules.web_app import WebApp

# ---------------------------------------------------------------------------
# Вспомогательные заглушки
# ---------------------------------------------------------------------------


class _DummyRouter:
    def get_model_info(self) -> dict:
        return {}


class _FakeOpenClaw:
    def get_last_runtime_route(self) -> dict:
        return {"channel": "cloud", "provider": "google", "model": "gemini", "status": "ok"}

    def get_tier_state_export(self) -> dict:
        return {"active_tier": "free"}

    async def get_cloud_runtime_check(self) -> dict:
        return {"ok": True}

    async def health_check(self) -> bool:
        return True


class _FakeKraab:
    def get_translator_runtime_profile(self) -> dict:
        return {"enabled": False}

    def get_translator_session_state(self) -> dict:
        return {"active": False, "session_id": None}


class _FakeHealthClient:
    async def health_check(self) -> bool:
        return True

    async def health_report(self) -> dict:
        return {"ok": True}

    async def capabilities_report(self) -> dict:
        return {"ok": True}


def _make_client(usage: dict[str, int] | None = None) -> TestClient:
    """Создаёт TestClient с замоканным get_usage."""
    deps = {
        "router": _DummyRouter(),
        "openclaw_client": _FakeOpenClaw(),
        "black_box": None,
        "health_service": None,
        "provisioning_service": None,
        "ai_runtime": None,
        "reaction_engine": None,
        "voice_gateway_client": _FakeHealthClient(),
        "krab_ear_client": _FakeHealthClient(),
        "perceptor": None,
        "watchdog": None,
        "queue": None,
        "kraab_userbot": _FakeKraab(),
    }
    app = WebApp(deps, port=18080, host="127.0.0.1")
    client = TestClient(app.app)

    # Храним usage для патча прямо в closure
    _usage = usage if usage is not None else {}

    # Патч применим через атрибут _usage_data на клиенте для удобства
    client._usage_data = _usage  # type: ignore[attr-defined]
    return client


# ---------------------------------------------------------------------------
# Тесты
# ---------------------------------------------------------------------------


_SAMPLE_USAGE = {
    "!ping": 42,
    "!ask": 30,
    "!search": 25,
    "!health": 20,
    "!stats": 15,
    "!model": 12,
    "!voice": 10,
    "!translate": 8,
    "!memo": 5,
    "!remind": 3,
    "!todo": 1,
}


def test_usage_top_default_limit_returns_sorted_top() -> None:
    """GET /api/commands/usage/top (default limit=10) возвращает отсортированный топ-10."""
    client = _make_client()
    with patch("src.core.command_registry.get_usage", return_value=dict(_SAMPLE_USAGE)):
        resp = client.get("/api/commands/usage/top")

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["total_commands"] == len(_SAMPLE_USAGE)

    top = data["top"]
    assert len(top) == 10
    # Проверяем сортировку count DESC
    counts = [item["count"] for item in top]
    assert counts == sorted(counts, reverse=True)
    # Первый — самый вызываемый
    assert top[0]["command"] == "!ping"
    assert top[0]["count"] == 42


def test_usage_top_custom_limit() -> None:
    """GET /api/commands/usage/top?limit=5 возвращает ровно 5 элементов."""
    client = _make_client()
    with patch("src.core.command_registry.get_usage", return_value=dict(_SAMPLE_USAGE)):
        resp = client.get("/api/commands/usage/top?limit=5")

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    top = data["top"]
    assert len(top) == 5
    # Топ-5 должен содержать самые частые
    assert top[0]["command"] == "!ping"
    assert top[4]["command"] == "!stats"


def test_usage_top_limit_clamped_to_100() -> None:
    """GET /api/commands/usage/top?limit=9999 обрезается до max 100."""
    # Генерируем 120 команд
    big_usage = {f"!cmd{i}": 120 - i for i in range(120)}
    client = _make_client()
    with patch("src.core.command_registry.get_usage", return_value=big_usage):
        resp = client.get("/api/commands/usage/top?limit=9999")

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    # Clamp к 100
    assert len(data["top"]) == 100
    assert data["total_commands"] == 120


def test_usage_top_empty_usage() -> None:
    """GET /api/commands/usage/top при пустом счётчике → top=[], total_commands=0."""
    client = _make_client()
    with patch("src.core.command_registry.get_usage", return_value={}):
        resp = client.get("/api/commands/usage/top")

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["top"] == []
    assert data["total_commands"] == 0


def test_usage_top_ties_sorted_by_name_asc() -> None:
    """При одинаковом count команды сортируются по имени ASC (стабильная сортировка)."""
    tied_usage = {"!zzz": 10, "!aaa": 10, "!mmm": 10}
    client = _make_client()
    with patch("src.core.command_registry.get_usage", return_value=tied_usage):
        resp = client.get("/api/commands/usage/top")

    assert resp.status_code == 200
    top = resp.json()["top"]
    names = [item["command"] for item in top]
    assert names == ["!aaa", "!mmm", "!zzz"]
