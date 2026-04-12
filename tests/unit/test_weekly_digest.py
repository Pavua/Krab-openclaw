# -*- coding: utf-8 -*-
"""
Тесты для WeeklyDigestService.
Паттерн: mock inbox_service + swarm_artifact_store + cost_analytics._calls.
"""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from src.core.weekly_digest import WEEKLY_DIGEST_INTERVAL_SEC, WeeklyDigestService

# ---------------------------------------------------------------------------
# Фикстуры
# ---------------------------------------------------------------------------


@pytest.fixture
def svc() -> WeeklyDigestService:
    return WeeklyDigestService()


def _make_artifact(team: str, topic: str, ts_offset: int = 0) -> dict:
    """Создаёт тестовый артефакт с timestamp в пределах последней недели."""
    ts = int(time.time()) - 3600 - ts_offset  # 1 час назад + offset
    return {
        "team": team,
        "topic": topic,
        "result": "ok",
        "timestamp": ts,
        "timestamp_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts)),
    }


# ---------------------------------------------------------------------------
# 1. Константа интервала
# ---------------------------------------------------------------------------


def test_weekly_digest_interval_is_7_days():
    assert WEEKLY_DIGEST_INTERVAL_SEC == 604800


# ---------------------------------------------------------------------------
# 2. _collect_swarm_data: базовый случай
# ---------------------------------------------------------------------------


def test_collect_swarm_data_counts_by_team(svc):
    artifacts = [
        _make_artifact("traders", "bitcoin Q2"),
        _make_artifact("traders", "eth analysis"),
        _make_artifact("coders", "refactor API"),
    ]
    with patch("src.core.weekly_digest.swarm_artifact_store") as mock_store:
        mock_store.list_artifacts.return_value = artifacts
        result = svc._collect_swarm_data(week_start_ts=time.time() - 7 * 24 * 3600 - 60)

    assert result["total_rounds"] == 3
    assert result["by_team"]["traders"] == 2
    assert result["by_team"]["coders"] == 1


# ---------------------------------------------------------------------------
# 3. _collect_swarm_data: фильтрация по дате — старые артефакты не считаются
# ---------------------------------------------------------------------------


def test_collect_swarm_data_filters_old_artifacts(svc):
    old_ts = time.time() - 10 * 24 * 3600  # 10 дней назад — старше недели
    artifacts = [
        {"team": "analysts", "topic": "old", "timestamp": old_ts, "timestamp_iso": ""},
        _make_artifact("analysts", "fresh"),
    ]
    with patch("src.core.weekly_digest.swarm_artifact_store") as mock_store:
        mock_store.list_artifacts.return_value = artifacts
        result = svc._collect_swarm_data(week_start_ts=time.time() - 7 * 24 * 3600)

    assert result["total_rounds"] == 1
    assert result["by_team"].get("analysts") == 1


# ---------------------------------------------------------------------------
# 4. _collect_swarm_data: деградация при ошибке store
# ---------------------------------------------------------------------------


def test_collect_swarm_data_degrades_on_error(svc):
    with patch("src.core.weekly_digest.swarm_artifact_store") as mock_store:
        mock_store.list_artifacts.side_effect = RuntimeError("disk full")
        result = svc._collect_swarm_data(week_start_ts=time.time() - 3600)

    assert result["total_rounds"] == 0
    assert result["by_team"] == {}


# ---------------------------------------------------------------------------
# 5. _collect_cost_data: считает только за неделю
# ---------------------------------------------------------------------------


def test_collect_cost_data_week_only(svc):
    from src.core.cost_analytics import CallRecord

    now = time.time()
    old_record = CallRecord(
        model_id="gemini",
        input_tokens=100,
        output_tokens=50,
        cost_usd=0.05,
        timestamp=now - 8 * 24 * 3600,  # 8 дней назад
    )
    fresh_record = CallRecord(
        model_id="gemini",
        input_tokens=200,
        output_tokens=100,
        cost_usd=0.10,
        timestamp=now - 3600,  # 1 час назад
    )

    with patch("src.core.weekly_digest.cost_analytics") as mock_ca:
        mock_ca._calls = [old_record, fresh_record]
        result = svc._collect_cost_data(week_start_ts=now - 7 * 24 * 3600)

    assert result["calls_count"] == 1
    assert abs(result["cost_week_usd"] - 0.10) < 0.001
    assert result["total_tokens"] == 300


# ---------------------------------------------------------------------------
# 6. _collect_inbox_data: считает error/warning
# ---------------------------------------------------------------------------


def test_collect_inbox_data_counts_severity(svc):
    open_items = [
        {"severity": "error", "title": "DB down", "created_at_utc": "2026-04-10T10:00:00"},
        {"severity": "warning", "title": "Slow query", "created_at_utc": "2026-04-10T11:00:00"},
        {"severity": "info", "title": "Startup ok", "created_at_utc": "2026-04-10T12:00:00"},
    ]
    with patch("src.core.weekly_digest.inbox_service") as mock_inbox:
        mock_inbox.list_items.return_value = open_items
        result = svc._collect_inbox_data()

    assert result["attention_count"] == 2
    assert result["error_count"] == 1
    assert result["warning_count"] == 1
    # info не должен попасть в items
    titles = [it["title"] for it in result["items"]]
    assert "Startup ok" not in titles


# ---------------------------------------------------------------------------
# 7. _render_digest: наличие ключевых секций
# ---------------------------------------------------------------------------


def test_render_digest_contains_sections(svc):
    body = WeeklyDigestService._render_digest(
        ts_now="2026-04-12T10:00:00+00:00",
        swarm_data={
            "total_rounds": 5,
            "by_team": {"traders": 3, "coders": 2},
            "top_artifacts": [
                {"team": "traders", "topic": "BTC", "timestamp_iso": "2026-04-11T08:00:00Z"}
            ],
        },
        cost_data={"cost_week_usd": 0.0042, "calls_count": 12, "total_tokens": 50000},
        inbox_data={
            "attention_count": 1,
            "error_count": 1,
            "warning_count": 0,
            "items": [
                {"severity": "error", "title": "DB down", "created_at_utc": "2026-04-10"},
            ],
        },
    )

    assert "Weekly Digest" in body
    assert "## Swarm" in body
    assert "## Cost" in body
    assert "## Inbox" in body
    assert "5" in body  # total_rounds
    assert "0.0042" in body  # cost_usd
    assert "traders: 3" in body or "traders" in body


# ---------------------------------------------------------------------------
# 8. generate_digest: успешный end-to-end (все зависимости mock)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_digest_ok(svc):
    with (
        patch("src.core.weekly_digest.swarm_artifact_store") as mock_store,
        patch("src.core.weekly_digest.cost_analytics") as mock_ca,
        patch("src.core.weekly_digest.inbox_service") as mock_inbox,
    ):
        mock_store.list_artifacts.return_value = [_make_artifact("traders", "weekly plan")]
        mock_ca._calls = []
        mock_inbox.list_items.return_value = []
        mock_inbox.build_identity.return_value = {}
        mock_inbox.upsert_item.return_value = None

        result = await svc.generate_digest()

    assert result["ok"] is True
    assert result["total_rounds"] == 1
    assert "digest_ts" in result
    mock_inbox.upsert_item.assert_called_once()


# ---------------------------------------------------------------------------
# 9. generate_digest: деградация при ошибке inbox.upsert_item
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_digest_inbox_error(svc):
    with (
        patch("src.core.weekly_digest.swarm_artifact_store") as mock_store,
        patch("src.core.weekly_digest.cost_analytics") as mock_ca,
        patch("src.core.weekly_digest.inbox_service") as mock_inbox,
    ):
        mock_store.list_artifacts.return_value = []
        mock_ca._calls = []
        mock_inbox.list_items.return_value = []
        mock_inbox.build_identity.return_value = {}
        mock_inbox.upsert_item.side_effect = RuntimeError("inbox unavailable")

        result = await svc.generate_digest()

    assert result["ok"] is False
    assert "error" in result
