# -*- coding: utf-8 -*-
"""Тесты Wave 29-B: GET /api/quota — multi-provider quota status."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.modules.web_routers._context import RouterContext
from src.modules.web_routers.quota_router import build_quota_router


def _make_client() -> TestClient:
    """Создаём тестовый FastAPI app с quota router."""
    ctx = RouterContext(
        deps={},
        project_root=Path("."),
        web_api_key_fn=lambda: None,
        assert_write_access_fn=lambda *a, **kw: None,
    )
    app = FastAPI()
    app.include_router(build_quota_router(ctx))
    return TestClient(app)


# ── Вспомогательные mock-значения ────────────────────────────────────────────

_MOCK_COUNTS = {"gemini": 5, "codex": 3, "vertex": 12, "anthropic": 0}


def _patch_helpers(
    *,
    gemini: str = "✅ ok",
    anthropic: str = "✅ ok",
    vertex: str = "✅ ok",
    counts: dict[str, int] | None = None,
):
    """Контекст-менеджер: патчит все 4 helpers разом."""
    if counts is None:
        counts = _MOCK_COUNTS.copy()

    return (
        patch(
            "src.modules.web_routers.quota_router._LOG_FILE",
            new=Path("/dev/null"),
        ),
        patch(
            "src.handlers.commands.observability_commands._probe_gemini_cli",
            new=AsyncMock(return_value=gemini),
        ),
        patch(
            "src.handlers.commands.observability_commands._probe_anthropic_vertex",
            new=AsyncMock(return_value=anthropic),
        ),
        patch(
            "src.handlers.commands.observability_commands._probe_vertex_gemini",
            new=AsyncMock(return_value=vertex),
        ),
        patch(
            "src.handlers.commands.observability_commands._count_today_calls",
            return_value=counts,
        ),
    )


# ── Тест 1: probe=false — быстрый ответ без live probe ───────────────────────


def test_quota_no_probe_returns_ok():
    """GET /api/quota?probe=false → 200, ok=True, 4 провайдера, probe=skipped."""
    client = _make_client()

    counts = {"gemini": 7, "codex": 2, "vertex": 0, "anthropic": 1}
    with patch(
        "src.handlers.commands.observability_commands._count_today_calls",
        return_value=counts,
    ):
        r = client.get("/api/quota?probe=false")

    assert r.status_code == 200
    body = r.json()

    assert body["ok"] is True
    assert "date" in body

    providers = body["providers"]
    assert set(providers.keys()) == {"gemini-cli", "codex-cli", "google-vertex", "anthropic-vertex"}

    # probe=false → все статусы "skipped"
    assert providers["gemini-cli"]["probe"] == "skipped"
    assert providers["google-vertex"]["probe"] == "skipped"
    assert providers["anthropic-vertex"]["probe"] == "skipped"

    # счётчики прокинуты
    assert providers["gemini-cli"]["today_calls"] == 7
    assert providers["codex-cli"]["today_calls"] == 2
    assert providers["google-vertex"]["today_calls"] == 0
    assert providers["anthropic-vertex"]["today_calls"] == 1


# ── Тест 2: probe=true (default) с моками ────────────────────────────────────


def test_quota_with_probes_mocked():
    """GET /api/quota → live probe через моки, статусы отражаются в ответе."""
    client = _make_client()

    patches = _patch_helpers(gemini="✅ ok", anthropic="✅ ok", vertex="✅ ok")
    with patches[0], patches[1], patches[2], patches[3], patches[4]:
        r = client.get("/api/quota")

    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True

    providers = body["providers"]

    assert providers["gemini-cli"]["probe"] == "✅ ok"
    assert providers["google-vertex"]["probe"] == "✅ ok"
    assert providers["anthropic-vertex"]["probe"] == "✅ ok"

    # Метаданные тиров присутствуют
    assert "tier" in providers["gemini-cli"]
    assert "tier_limit" in providers["gemini-cli"]
    assert providers["google-vertex"]["probe_model"] == "gemini-2.5-flash"
    assert providers["anthropic-vertex"]["probe_model"] == "claude-haiku-4-5"

    # Счётчики из mock counts
    assert providers["gemini-cli"]["today_calls"] == _MOCK_COUNTS["gemini"]
    assert providers["codex-cli"]["today_calls"] == _MOCK_COUNTS["codex"]


# ── Тест 3: probe failures — graceful degradation ────────────────────────────


def test_quota_probe_failures_graceful():
    """Если probe бросает или возвращает error-строку — endpoint не падает."""
    client = _make_client()

    patches = _patch_helpers(
        gemini="⏱ timeout",
        anthropic="⚠️ 429 quota exceeded",
        vertex="⚠️ credentials error",
    )
    with patches[0], patches[1], patches[2], patches[3], patches[4]:
        r = client.get("/api/quota")

    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True

    providers = body["providers"]
    # Ошибки прокидываются as-is — не 500
    assert "timeout" in providers["gemini-cli"]["probe"] or "⏱" in providers["gemini-cli"]["probe"]
    assert "429" in providers["anthropic-vertex"]["probe"] or "quota" in providers["anthropic-vertex"]["probe"]
    assert "credentials" in providers["google-vertex"]["probe"] or "⚠️" in providers["google-vertex"]["probe"]


# ── Тест 4: counts из _count_today_calls отражаются корректно ────────────────


def test_quota_counts_reflected():
    """_count_today_calls() mock → значения попадают в каждый provider."""
    client = _make_client()

    custom_counts = {"gemini": 42, "codex": 17, "vertex": 3, "anthropic": 99}
    patches = _patch_helpers(counts=custom_counts)
    with patches[0], patches[1], patches[2], patches[3], patches[4]:
        r = client.get("/api/quota")

    assert r.status_code == 200
    providers = r.json()["providers"]

    assert providers["gemini-cli"]["today_calls"] == 42
    assert providers["codex-cli"]["today_calls"] == 17
    assert providers["google-vertex"]["today_calls"] == 3
    assert providers["anthropic-vertex"]["today_calls"] == 99
