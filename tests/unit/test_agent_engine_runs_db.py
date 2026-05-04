# -*- coding: utf-8 -*-
"""Tests for agent_engine_runs helpers (Wave 17-B, Hermes Phase C).

Покрывает: record_engine_run, get_engine_comparison, list_engine_runs,
table auto-create, db_not_found graceful.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_archive_db(tmp_path, monkeypatch) -> Path:
    """Создаёт временный archive.db и перенаправляет KRAB_ARCHIVE_DB_PATH."""
    db_path = tmp_path / "archive.db"
    # Создаём пустой SQLite файл (таблицы создадутся лениво)
    conn = sqlite3.connect(db_path)
    conn.close()
    monkeypatch.setenv("KRAB_ARCHIVE_DB_PATH", str(db_path))
    return db_path


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_record_engine_run_creates_table(tmp_archive_db):
    """record_engine_run() создаёт таблицу если не существует и возвращает run_id."""
    from src.core.agent_engine_runs import record_engine_run

    run_id = record_engine_run(
        engine="openclaw",
        chat_id="42",
        success=True,
        latency_ms_total=450,
        latency_ms_ttfb=80,
        prompt_tokens=100,
        completion_tokens=200,
    )
    assert run_id is not None
    assert len(run_id) == 36  # UUID4

    # Проверяем что запись существует
    conn = sqlite3.connect(tmp_archive_db)
    row = conn.execute(
        "SELECT engine, success, chat_id FROM agent_engine_runs WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    conn.close()
    assert row is not None
    assert row[0] == "openclaw"
    assert row[1] == 1  # success=True → 1
    assert row[2] == "42"


def test_record_multiple_engines(tmp_archive_db):
    """record_engine_run() для разных engines сохраняет корректно."""
    from src.core.agent_engine_runs import record_engine_run

    record_engine_run(engine="openclaw", success=True, latency_ms_total=300)
    record_engine_run(engine="hermes", success=False, latency_ms_total=500)

    conn = sqlite3.connect(tmp_archive_db)
    rows = conn.execute(
        "SELECT engine, success FROM agent_engine_runs ORDER BY started_at"
    ).fetchall()
    conn.close()
    assert len(rows) == 2
    engines = [r[0] for r in rows]
    assert "openclaw" in engines
    assert "hermes" in engines


def test_get_engine_comparison_aggregates(tmp_archive_db):
    """get_engine_comparison() возвращает агрегаты по engine за window_days."""
    from src.core.agent_engine_runs import get_engine_comparison, record_engine_run

    # Записываем: 2 openclaw (1 успешный), 1 hermes (успешный)
    record_engine_run(engine="openclaw", success=True, latency_ms_total=400, cost_usd=0.001)
    record_engine_run(engine="openclaw", success=False, latency_ms_total=600)
    record_engine_run(engine="hermes", success=True, latency_ms_total=200, cost_usd=0.002)

    result = get_engine_comparison(window_days=7)
    assert "engines" in result
    assert "openclaw" in result["engines"]
    assert "hermes" in result["engines"]

    oc = result["engines"]["openclaw"]
    assert oc["runs"] == 2
    assert oc["success_rate"] == 0.5
    assert oc["avg_latency_ms"] == 500.0

    hr = result["engines"]["hermes"]
    assert hr["runs"] == 1
    assert hr["success_rate"] == 1.0


def test_list_engine_runs_filter(tmp_archive_db):
    """list_engine_runs() фильтрует по engine корректно."""
    from src.core.agent_engine_runs import list_engine_runs, record_engine_run

    record_engine_run(engine="openclaw", success=True)
    record_engine_run(engine="hermes", success=True)
    record_engine_run(engine="openclaw", success=False)

    oc_runs = list_engine_runs(engine="openclaw")
    assert len(oc_runs) == 2
    assert all(r["engine"] == "openclaw" for r in oc_runs)

    all_runs = list_engine_runs()
    assert len(all_runs) == 3


def test_db_not_found_returns_gracefully(tmp_path, monkeypatch):
    """При отсутствии archive.db функции возвращают пустые результаты без raise."""
    monkeypatch.setenv("KRAB_ARCHIVE_DB_PATH", str(tmp_path / "nonexistent.db"))

    from src.core.agent_engine_runs import (
        get_engine_comparison,
        list_engine_runs,
        record_engine_run,
    )

    # record возвращает None (не создаёт DB)
    run_id = record_engine_run(engine="openclaw", success=True)
    assert run_id is None

    # list возвращает пустой список
    runs = list_engine_runs()
    assert runs == []

    # comparison возвращает error dict
    result = get_engine_comparison()
    assert "error" in result
