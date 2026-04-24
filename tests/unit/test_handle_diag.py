# -*- coding: utf-8 -*-
"""
Тесты команды !diag — one-shot диагностический summary для владельца.
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.core.access_control import AccessLevel, AccessProfile
from src.handlers.command_handlers import handle_diag


def _make_bot(*, access_level: AccessLevel = AccessLevel.OWNER) -> SimpleNamespace:
    """Stub KraabUserbot с нужным уровнем доступа."""
    profile = AccessProfile(level=access_level, source="test", matched_subject="42")
    return SimpleNamespace(
        _session_start_time=time.time() - 3600,  # 1h uptime
        _get_access_profile=lambda _u: profile,
    )


def _make_message() -> SimpleNamespace:
    return SimpleNamespace(
        from_user=SimpleNamespace(id=42, username="owner"),
        chat=SimpleNamespace(id=42),
        reply=AsyncMock(),
    )


class _FakeResp:
    def __init__(self, data, status: int = 200) -> None:
        self._data = data
        self.status_code = status

    def json(self):
        return self._data


class _FakeClient:
    """Стаб httpx.AsyncClient — маршрутизация по path → fixture."""

    def __init__(self, routes: dict[str, object]) -> None:
        self._routes = routes

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url: str, timeout: float | None = None):
        # url = base + path; ищем path-суффикс в routes
        for path, payload in self._routes.items():
            if url.endswith(path):
                if isinstance(payload, Exception):
                    raise payload
                if isinstance(payload, _FakeResp):
                    return payload
                return _FakeResp(payload)
        return _FakeResp({}, status=404)


@pytest.mark.asyncio
async def test_diag_owner_returns_full_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    """!diag от owner — reply содержит все ключевые секции."""
    import src.handlers.command_handlers as mod

    routes = {
        "/api/health/lite": {
            "services": {
                "openclaw_gateway": {"ok": True},
                "mcp_yung_nagato": {"ok": True},
                "mcp_p0lrd": {"ok": True},
                "lm_studio": {"ok": False, "detail": "401 auth"},
                "cloudflared": {"ok": True},
            }
        },
        "/api/model/status": {
            "active": "google/gemini-3-pro-preview",
            "tier": "free",
            "last_route": "30 min ago",
        },
        "/api/stats": {"messages_1h": 124, "llm_calls_1h": 18, "swarm_rounds_1h": 2},
        "/api/costs/budget": {"spent": 0.42, "budget": 10.0},
        "/api/memory/stats": {
            "archive": {"size_mb": 506, "chunks": 72000, "vec": 72000},
            "retrieval_mode": "hybrid",
            "latency": {"fts_p50": 16, "vec_p50": 12, "mmr_p50": 5},
        },
        "/api/ops/alerts": {"active": [{"code": "RuntimeError", "message": "Queue 3 events"}]},
        "/api/inbox/status": {"open_items": 3, "stale_items": 0},
        "/api/openclaw/cron/jobs": {
            "jobs": [
                {"name": "daily-morning-brief", "last_fire": "08:00", "fires_today": 1},
                {"name": "cost-budget-midday", "last_fire": "13:00", "fires_today": 1},
            ]
        },
        "/api/memory/phase2/status": {
            "flag": "shadow",
            "model_loaded": True,
            "model_dim": 256,
            "vec_chunks_count": 72328,
            "vec_join_pct": 100.0,
            "retrieval_mode_hour": {"fts": 15, "vec": 8, "hybrid": 7, "none": 0},
            "latency_avg": {"fts": 16, "vec": 12, "mmr": 5, "total": 34},
            "shadow_delta_pct": 38.0,
        },
    }
    monkeypatch.setattr(mod.httpx, "AsyncClient", lambda **_: _FakeClient(routes))
    # Stub Sentry + Security collectors (parallel tasks)
    monkeypatch.setattr(
        mod,
        "_diag_fetch_sentry",
        AsyncMock(
            return_value={
                "unresolved": 12,
                "unresolved_by_project": {"python-fastapi": 8, "krab-ear-agent": 2},
                "top_groups": [
                    {"title": "RuntimeError:Queue", "count": 66},
                    {"title": "SQLite locked", "count": 15},
                ],
                "auto_resolved_today": 30,
                "trace_sample_rate": "10%",
            }
        ),
    )
    monkeypatch.setattr(
        mod,
        "_diag_collect_security",
        AsyncMock(
            return_value={
                "phantom_guard_matched": 0,
                "command_blocklist_skip": 12,
                "operator_pii_sanitized": 0,
                "swarm_tool_blocked": 0,
            }
        ),
    )

    bot = _make_bot()
    msg = _make_message()
    await handle_diag(bot, msg)

    msg.reply.assert_awaited_once()
    text = msg.reply.call_args[0][0]
    assert "Krab Diagnostics" in text
    assert "Infrastructure" in text
    assert "Model routing" in text
    assert "google/gemini-3-pro-preview" in text
    assert "Traffic" in text
    assert "124" in text  # messages
    assert "$0.42" in text
    assert "Memory" in text
    assert "506 MB" in text
    assert "Errors" in text
    assert "RuntimeError" in text
    assert "Inbox" in text
    assert "Open items: 3" in text
    assert "Cron" in text
    assert "daily-morning-brief" in text
    # LM Studio в health отмечен как ❌
    assert "❌" in text
    assert "LM Studio" in text
    # Memory Phase 2 секция (shadow mode)
    assert "Memory Phase 2" in text
    assert "shadow" in text
    assert "72328" in text
    assert "38" in text  # shadow_delta_pct
    # Sentry секция
    assert "Sentry" in text
    assert "Unresolved: 12" in text
    assert "RuntimeError" in text
    # Security секция
    assert "Security" in text
    assert "Phantom guard: 0" in text
    assert "Command blocklist silent skips: 12" in text


@pytest.mark.asyncio
async def test_diag_guest_rejected() -> None:
    """!diag от не-владельца — отказ, без обращений к panel."""
    bot = _make_bot(access_level=AccessLevel.GUEST)
    msg = _make_message()

    await handle_diag(bot, msg)

    msg.reply.assert_awaited_once()
    text = msg.reply.call_args[0][0]
    assert "владельца" in text.lower() or "owner" in text.lower()


@pytest.mark.asyncio
async def test_diag_partial_outage_graceful(monkeypatch: pytest.MonkeyPatch) -> None:
    """!diag: /api/memory/stats падает → остальные секции показываются."""
    import src.handlers.command_handlers as mod

    routes = {
        "/api/health/lite": {"services": {"openclaw_gateway": {"ok": True}}},
        "/api/model/status": {"active": "gemini-3-pro", "tier": "free"},
        "/api/stats": {"messages_1h": 10, "llm_calls_1h": 2},
        "/api/costs/budget": {"spent": 0.1, "budget": 10.0},
        # memory/stats отсутствует → graceful fallback
        "/api/memory/stats": _FakeResp({}, status=500),
        "/api/ops/alerts": {"active": []},
        "/api/inbox/status": {"open_items": 0, "stale_items": 0},
        "/api/openclaw/cron/jobs": {"jobs": []},
        "/api/memory/phase2/status": {"flag": "disabled"},
    }
    monkeypatch.setattr(mod.httpx, "AsyncClient", lambda **_: _FakeClient(routes))
    monkeypatch.setattr(mod, "_diag_fetch_sentry", AsyncMock(return_value=None))
    monkeypatch.setattr(mod, "_diag_collect_security", AsyncMock(return_value=None))

    bot = _make_bot()
    msg = _make_message()
    await handle_diag(bot, msg)

    text = msg.reply.call_args[0][0]
    # Memory секция присутствует, но данные недоступны
    assert "Memory" in text
    assert "Данные недоступны" in text
    # Другие секции — рабочие
    assert "gemini-3-pro" in text
    assert "Traffic" in text
    assert "Cron" in text
