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
    assert "Swarm" in body
    assert "Cost" in body
    assert "Inbox" in body
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


# ---------------------------------------------------------------------------
# 10. _collect_bypass_perf: агрегирует top-3 kind/model (tmpdir + JSONL)
# ---------------------------------------------------------------------------


def test_collect_bypass_perf_top3(tmp_path, svc):
    """JSONL с записями разных kind → top_kinds возвращает max 3 элемента."""
    import json as _json

    log = tmp_path / "bypass_perf.jsonl"
    now = time.time()
    # 4 записи cli, 2 vertex, 1 google-direct
    records = (
        [{"ts": now - 60, "kind": "cli", "model": "codex", "duration_sec": 22.0, "success": True}] * 4
        + [{"ts": now - 120, "kind": "vertex", "model": "gemini-pro", "duration_sec": 10.0, "success": True}] * 2
        + [{"ts": now - 180, "kind": "google-direct", "model": "gemini-flash", "duration_sec": 8.0, "success": False}]
    )
    with log.open("w") as fh:
        for r in records:
            fh.write(_json.dumps(r) + "\n")

    with patch("src.core.weekly_digest._BYPASS_PERF_LOG", log):
        result = WeeklyDigestService._collect_bypass_perf(window_days=7)

    assert result["total_calls"] == 7
    assert result["total_failures"] == 1
    assert len(result["top_kinds"]) <= 3
    # cli должен быть первым (4 вызова)
    assert result["top_kinds"][0]["name"] == "cli"
    assert result["top_kinds"][0]["count"] == 4
    assert len(result["top_models"]) <= 3


# ---------------------------------------------------------------------------
# 11. _collect_bypass_perf: пустой файл → нули
# ---------------------------------------------------------------------------


def test_collect_bypass_perf_no_file(tmp_path):
    """Если файла нет — возвращаем нулевую статистику без исключения."""
    nonexistent = tmp_path / "does_not_exist.jsonl"
    with patch("src.core.weekly_digest._BYPASS_PERF_LOG", nonexistent):
        result = WeeklyDigestService._collect_bypass_perf(window_days=7)

    assert result["total_calls"] == 0
    assert result["top_kinds"] == []
    assert result["top_models"] == []


# ---------------------------------------------------------------------------
# 12. _collect_cost_trend: delta% и top_models
# ---------------------------------------------------------------------------


def test_collect_cost_trend_delta_and_top_models(svc):
    """Прошлая неделя $1, эта $1.5 → delta +50%."""
    from src.core.cost_analytics import CallRecord

    now = time.time()
    week_start = now - 7 * 24 * 3600
    # Прошлая неделя (8 дней назад)
    old_call = CallRecord(
        model_id="gemini-pro",
        input_tokens=1000,
        output_tokens=500,
        cost_usd=1.0,
        timestamp=now - 8 * 24 * 3600,
    )
    # Эта неделя (1 час назад)
    new_call = CallRecord(
        model_id="claude-opus",
        input_tokens=2000,
        output_tokens=1000,
        cost_usd=1.5,
        timestamp=now - 3600,
    )

    with patch("src.core.weekly_digest.cost_analytics") as mock_ca:
        mock_ca._calls = [old_call, new_call]
        result = WeeklyDigestService._collect_cost_trend(week_start)

    assert abs(result["this_week_usd"] - 1.5) < 0.001
    assert abs(result["prev_week_usd"] - 1.0) < 0.001
    assert result["delta_pct"] == 50.0
    assert len(result["top_models"]) == 1
    assert result["top_models"][0]["model"] == "claude-opus"


# ---------------------------------------------------------------------------
# 13. _collect_memory_pressure: max swap и alerts_count
# ---------------------------------------------------------------------------


def test_collect_memory_pressure_alerts(tmp_path):
    """JSONL с swap и alerts → max_swap и alerts_count корректны."""
    import json as _json

    log = tmp_path / "coexistence_monitor.log"
    now = time.time()
    rows = [
        {"timestamp": now - 100, "swap_used_gb": 5.2, "combined_rss_gb": 3.1, "alerts": []},
        {"timestamp": now - 200, "swap_used_gb": 14.5, "combined_rss_gb": 4.2, "alerts": ["swap_critical:14.5GB"]},
        {"timestamp": now - 300, "swap_used_gb": 3.0, "combined_rss_gb": 2.0, "alerts": ["swap_warning:3.0GB", "rss_high"]},
        # Старая запись — вне окна 7 дней
        {"timestamp": now - 8 * 86400, "swap_used_gb": 99.0, "combined_rss_gb": 50.0, "alerts": ["old_alert"]},
    ]
    with log.open("w") as fh:
        for r in rows:
            fh.write(_json.dumps(r) + "\n")

    with patch("src.core.weekly_digest._COEXISTENCE_LOG", log):
        result = WeeklyDigestService._collect_memory_pressure(window_days=7)

    assert result["max_swap_gb"] == 14.5
    assert result["max_combined_rss_gb"] == 4.2
    assert result["alerts_count"] == 3  # только 3 alerts в окне (не 4 из старой записи)


# ---------------------------------------------------------------------------
# 14. _render_digest: новые секции bypass + memory + cost trend
# ---------------------------------------------------------------------------


def test_render_digest_extended_sections():
    """Все расширенные секции присутствуют при наличии данных."""
    body = WeeklyDigestService._render_digest(
        ts_now="2026-05-06T12:00:00+00:00",
        swarm_data={"total_rounds": 10, "by_team": {"traders": 10}, "top_artifacts": []},
        cost_data={"cost_week_usd": 2.34, "calls_count": 50, "total_tokens": 100000},
        cost_trend={
            "this_week_usd": 2.34,
            "prev_week_usd": 2.03,
            "delta_pct": 15.3,
            "top_models": [{"model": "gemini-pro", "cost_usd": 2.34, "calls": 50}],
        },
        bypass_data={
            "total_calls": 100,
            "total_failures": 2,
            "top_kinds": [{"name": "cli", "count": 80, "mean_sec": 22.3, "p95_sec": 44.1, "fail": 1}],
            "top_models": [{"name": "codex/gpt-5.5", "count": 80, "mean_sec": 22.3, "p95_sec": 44.1, "fail": 1}],
        },
        memory_data={"alerts_count": 3, "max_swap_gb": 11.2, "max_combined_rss_gb": 4.5},
        inbox_data={"attention_count": 0, "error_count": 0, "warning_count": 0, "items": []},
    )

    assert "Bypass Calls" in body
    assert "cli" in body
    assert "Memory" in body
    assert "11.2" in body  # max swap
    assert "+15.3%" in body  # cost trend
    assert "gemini-pro" in body  # top cost model
