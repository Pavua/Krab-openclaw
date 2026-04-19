# -*- coding: utf-8 -*-
"""
Тесты для src/core/openclaw_task_poller.py.

Проверяем чтение runs.sqlite, liveness ping и форматирование прогресса для Telegram.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

# --- Хелперы: мини-схема task_runs, совместимая с поллером ---

# Поллер читает только колонки task_id, status, label, progress_summary, last_event_at;
# дополнительные колонки добавлены ради полноты, но не обязательны.
SCHEMA = """
CREATE TABLE IF NOT EXISTS task_runs (
    task_id TEXT PRIMARY KEY,
    status TEXT,
    label TEXT,
    progress_summary TEXT,
    last_event_at INTEGER
);
"""


@pytest.fixture
def mock_runs_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Создаёт tmp sqlite-базу и подставляет её путь в модуль."""
    db_path = tmp_path / "runs.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()
    monkeypatch.setattr("src.core.openclaw_task_poller.RUNS_DB_PATH", db_path)
    return db_path


def _insert_task(
    db_path: Path,
    *,
    task_id: str = "t1",
    status: str = "running",
    label: str = "ai_query",
    progress_summary: str = "",
    last_event_at: int | None = None,
) -> None:
    if last_event_at is None:
        last_event_at = int(time.time() * 1000)
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO task_runs (task_id, status, label, progress_summary, last_event_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (task_id, status, label, progress_summary, last_event_at),
    )
    conn.commit()
    conn.close()


# --- poll_active_tasks ---


def test_poll_active_tasks_reads_sqlite(mock_runs_db: Path) -> None:
    """Основной happy-path — читаем задачу и получаем TaskState."""
    now_ms = int(time.time() * 1000)
    _insert_task(
        mock_runs_db,
        task_id="run1",
        status="running",
        label="agent_query",
        progress_summary="web_search(query='hi')",
        last_event_at=now_ms,
    )
    from src.core.openclaw_task_poller import poll_active_tasks

    tasks = poll_active_tasks()
    assert len(tasks) == 1
    assert tasks[0].task_id == "run1"
    assert tasks[0].status == "running"
    assert tasks[0].label == "agent_query"
    assert "web_search" in tasks[0].progress_summary
    assert tasks[0].is_stale is False


def test_poll_active_tasks_empty_when_no_db(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Нет файла runs.sqlite → пустой список."""
    monkeypatch.setattr(
        "src.core.openclaw_task_poller.RUNS_DB_PATH", tmp_path / "nonexistent.sqlite"
    )
    from src.core.openclaw_task_poller import poll_active_tasks

    assert poll_active_tasks() == []


def test_poll_active_tasks_skips_terminal_states(mock_runs_db: Path) -> None:
    """Succeeded/failed задачи не должны попадать в active."""
    now_ms = int(time.time() * 1000)
    _insert_task(mock_runs_db, task_id="done1", status="succeeded", last_event_at=now_ms)
    _insert_task(mock_runs_db, task_id="fail1", status="failed", last_event_at=now_ms)
    _insert_task(mock_runs_db, task_id="run1", status="running", last_event_at=now_ms)
    from src.core.openclaw_task_poller import poll_active_tasks

    tasks = poll_active_tasks()
    ids = {t.task_id for t in tasks}
    assert ids == {"run1"}


def test_poll_active_tasks_marks_stale(mock_runs_db: Path) -> None:
    """Задача с last_event_at старше STALE_THRESHOLD_SEC — is_stale=True."""
    old_ms = int(time.time() * 1000) - 100_000  # 100 сек назад, > 90 сек порога
    _insert_task(mock_runs_db, task_id="stale1", status="running", last_event_at=old_ms)
    from src.core.openclaw_task_poller import poll_active_tasks

    tasks = poll_active_tasks()
    assert tasks[0].is_stale is True


def test_poll_active_tasks_handles_db_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Повреждённая БД (не sqlite) → возвращает [] без исключений."""
    bad_db = tmp_path / "corrupt.sqlite"
    bad_db.write_text("not a real sqlite file")
    monkeypatch.setattr("src.core.openclaw_task_poller.RUNS_DB_PATH", bad_db)
    from src.core.openclaw_task_poller import poll_active_tasks

    # Не должно бросать, только вернуть []
    result = poll_active_tasks()
    assert result == []


def test_poll_active_tasks_null_fields(mock_runs_db: Path) -> None:
    """NULL в label/progress_summary → пустые строки в TaskState."""
    now_ms = int(time.time() * 1000)
    conn = sqlite3.connect(str(mock_runs_db))
    conn.execute(
        "INSERT INTO task_runs (task_id, status, label, progress_summary, last_event_at) "
        "VALUES (?, ?, NULL, NULL, ?)",
        ("null1", "running", now_ms),
    )
    conn.commit()
    conn.close()
    from src.core.openclaw_task_poller import poll_active_tasks

    tasks = poll_active_tasks()
    assert tasks[0].task_id == "null1"
    assert tasks[0].label == ""
    assert tasks[0].progress_summary == ""


# --- poll_gateway_liveness ---


def test_poll_gateway_liveness_missing_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Нет файла БД → (False, 'runs.sqlite not found')."""
    monkeypatch.setattr("src.core.openclaw_task_poller.RUNS_DB_PATH", tmp_path / "nope.sqlite")
    from src.core.openclaw_task_poller import poll_gateway_liveness

    alive, reason = poll_gateway_liveness()
    assert alive is False
    assert "not found" in reason


def test_poll_gateway_liveness_empty_db(mock_runs_db: Path) -> None:
    """БД есть, задач нет → (True, 'no_tasks')."""
    from src.core.openclaw_task_poller import poll_gateway_liveness

    alive, reason = poll_gateway_liveness()
    assert alive is True
    assert reason == "no_tasks"


def test_poll_gateway_liveness_recent_activity(mock_runs_db: Path) -> None:
    """Свежее last_event_at (< часа) → alive + 'last_activity_Ns_ago'."""
    now_ms = int(time.time() * 1000)
    _insert_task(mock_runs_db, task_id="x", status="running", last_event_at=now_ms)
    from src.core.openclaw_task_poller import poll_gateway_liveness

    alive, reason = poll_gateway_liveness()
    assert alive is True
    assert "last_activity" in reason


def test_poll_gateway_liveness_idle(mock_runs_db: Path) -> None:
    """Старая активность (> часа) → (True, 'idle_Ns')."""
    old_ms = int(time.time() * 1000) - 4_000_000  # ~66 минут назад
    _insert_task(mock_runs_db, task_id="x", status="succeeded", last_event_at=old_ms)
    from src.core.openclaw_task_poller import poll_gateway_liveness

    alive, reason = poll_gateway_liveness()
    assert alive is True
    assert reason.startswith("idle_")


def test_poll_gateway_liveness_handles_corruption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Битая БД → (False, <error message>)."""
    bad = tmp_path / "bad.sqlite"
    bad.write_text("garbage")
    monkeypatch.setattr("src.core.openclaw_task_poller.RUNS_DB_PATH", bad)
    from src.core.openclaw_task_poller import poll_gateway_liveness

    alive, reason = poll_gateway_liveness()
    assert alive is False
    assert reason  # непустая причина


# --- check_gateway_http_alive (async) ---


async def test_check_gateway_http_alive_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mock httpx → 200 OK → True."""
    mock_response = MagicMock()
    mock_response.status_code = 200

    mock_client_cm = MagicMock()
    mock_client_cm.__aenter__ = AsyncMock(return_value=mock_client_cm)
    mock_client_cm.__aexit__ = AsyncMock(return_value=None)
    mock_client_cm.get = AsyncMock(return_value=mock_response)

    monkeypatch.setattr("httpx.AsyncClient", lambda **kw: mock_client_cm)

    from src.core.openclaw_task_poller import check_gateway_http_alive

    assert await check_gateway_http_alive() is True


async def test_check_gateway_http_alive_non_200(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mock httpx → 500 → False."""
    mock_response = MagicMock()
    mock_response.status_code = 500

    mock_client_cm = MagicMock()
    mock_client_cm.__aenter__ = AsyncMock(return_value=mock_client_cm)
    mock_client_cm.__aexit__ = AsyncMock(return_value=None)
    mock_client_cm.get = AsyncMock(return_value=mock_response)

    monkeypatch.setattr("httpx.AsyncClient", lambda **kw: mock_client_cm)

    from src.core.openclaw_task_poller import check_gateway_http_alive

    assert await check_gateway_http_alive() is False


async def test_check_gateway_http_alive_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """httpx таймаут → False, без исключений."""
    import httpx

    mock_client_cm = MagicMock()
    mock_client_cm.__aenter__ = AsyncMock(return_value=mock_client_cm)
    mock_client_cm.__aexit__ = AsyncMock(return_value=None)
    mock_client_cm.get = AsyncMock(side_effect=httpx.TimeoutException("boom"))

    monkeypatch.setattr("httpx.AsyncClient", lambda **kw: mock_client_cm)

    from src.core.openclaw_task_poller import check_gateway_http_alive

    assert await check_gateway_http_alive() is False


async def test_check_gateway_http_alive_connect_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """ConnectError → False."""
    import httpx

    mock_client_cm = MagicMock()
    mock_client_cm.__aenter__ = AsyncMock(return_value=mock_client_cm)
    mock_client_cm.__aexit__ = AsyncMock(return_value=None)
    mock_client_cm.get = AsyncMock(side_effect=httpx.ConnectError("refused"))

    monkeypatch.setattr("httpx.AsyncClient", lambda **kw: mock_client_cm)

    from src.core.openclaw_task_poller import check_gateway_http_alive

    assert await check_gateway_http_alive() is False


# --- format_task_progress_for_telegram ---


def test_format_task_progress_empty_list() -> None:
    """Пустой список → пустая строка."""
    from src.core.openclaw_task_poller import format_task_progress_for_telegram

    assert format_task_progress_for_telegram([]) == ""


def test_format_task_progress_running_icon(mock_runs_db: Path) -> None:
    """Running (не stale) → icon 🔄."""
    now_ms = int(time.time() * 1000)
    _insert_task(
        mock_runs_db,
        task_id="r1",
        status="running",
        label="ask",
        progress_summary="tool_call web_search",
        last_event_at=now_ms,
    )
    from src.core.openclaw_task_poller import (
        format_task_progress_for_telegram,
        poll_active_tasks,
    )

    text = format_task_progress_for_telegram(poll_active_tasks())
    assert "🔄" in text
    assert "ask" in text
    assert "web_search" in text


def test_format_task_progress_queued_icon(mock_runs_db: Path) -> None:
    """Queued → ⏳."""
    now_ms = int(time.time() * 1000)
    _insert_task(mock_runs_db, task_id="q1", status="queued", label="pending", last_event_at=now_ms)
    from src.core.openclaw_task_poller import (
        format_task_progress_for_telegram,
        poll_active_tasks,
    )

    text = format_task_progress_for_telegram(poll_active_tasks())
    assert "⏳" in text


def test_format_task_progress_stale_icon(mock_runs_db: Path) -> None:
    """Stale задача → ⚠️ + 'Нет активности ... сек'."""
    old_ms = int(time.time() * 1000) - 120_000  # 120 сек назад
    _insert_task(
        mock_runs_db,
        task_id="s1",
        status="running",
        label="hung",
        progress_summary="waiting",
        last_event_at=old_ms,
    )
    from src.core.openclaw_task_poller import (
        format_task_progress_for_telegram,
        poll_active_tasks,
    )

    text = format_task_progress_for_telegram(poll_active_tasks())
    assert "⚠️" in text
    assert "Нет активности" in text


def test_format_task_progress_limits_to_three(mock_runs_db: Path) -> None:
    """При > 3 задач форматируем только первые 3."""
    now_ms = int(time.time() * 1000)
    for i in range(5):
        _insert_task(
            mock_runs_db,
            task_id=f"t{i}",
            status="running",
            label=f"label_{i}",
            progress_summary=f"step_{i}",
            last_event_at=now_ms - i * 100,
        )
    from src.core.openclaw_task_poller import (
        format_task_progress_for_telegram,
        poll_active_tasks,
    )

    tasks = poll_active_tasks()
    assert len(tasks) == 5
    text = format_task_progress_for_telegram(tasks)
    # в тексте должно быть ≤ 3 label'ов из {label_0..label_4}
    present = sum(1 for i in range(5) if f"label_{i}" in text)
    assert present <= 3


def test_format_task_progress_truncates_long_summary(mock_runs_db: Path) -> None:
    """Summary > 80 символов → обрезается + добавляется '…'."""
    long_text = "A" * 200
    _insert_task(
        mock_runs_db,
        task_id="long1",
        status="running",
        progress_summary=long_text,
        last_event_at=int(time.time() * 1000),
    )
    from src.core.openclaw_task_poller import (
        format_task_progress_for_telegram,
        poll_active_tasks,
    )

    text = format_task_progress_for_telegram(poll_active_tasks())
    assert "A" * 80 in text
    assert "A" * 81 not in text  # лишнее обрезано
    assert "…" in text  # ellipsis marker


def test_format_task_progress_default_label(mock_runs_db: Path) -> None:
    """Пустой label → 'задача' по дефолту."""
    now_ms = int(time.time() * 1000)
    conn = sqlite3.connect(str(mock_runs_db))
    conn.execute(
        "INSERT INTO task_runs (task_id, status, label, progress_summary, last_event_at) "
        "VALUES (?, ?, ?, ?, ?)",
        ("nolabel", "running", "", "", now_ms),
    )
    conn.commit()
    conn.close()
    from src.core.openclaw_task_poller import (
        format_task_progress_for_telegram,
        poll_active_tasks,
    )

    text = format_task_progress_for_telegram(poll_active_tasks())
    assert "задача" in text


# --- check_tasks_hung ---


def test_check_tasks_hung_empty_list() -> None:
    """Пустой список → None."""
    from src.core.openclaw_task_poller import check_tasks_hung

    assert check_tasks_hung([]) is None


def test_check_tasks_hung_no_running(mock_runs_db: Path) -> None:
    """Есть queued, но нет running → None (проверяем только running)."""
    now_ms = int(time.time() * 1000)
    _insert_task(mock_runs_db, task_id="q1", status="queued", last_event_at=now_ms)
    from src.core.openclaw_task_poller import check_tasks_hung, poll_active_tasks

    assert check_tasks_hung(poll_active_tasks()) is None


def test_check_tasks_hung_all_stale_returns_max(mock_runs_db: Path) -> None:
    """Все running зависли > hung_threshold_sec → возвращаем max stale_sec."""
    # 400 сек и 500 сек назад, порог 180 сек
    now_ms = int(time.time() * 1000)
    _insert_task(mock_runs_db, task_id="h1", status="running", last_event_at=now_ms - 400_000)
    _insert_task(mock_runs_db, task_id="h2", status="running", last_event_at=now_ms - 500_000)
    from src.core.openclaw_task_poller import check_tasks_hung, poll_active_tasks

    result = check_tasks_hung(poll_active_tasks(), hung_threshold_sec=180.0)
    assert result is not None
    # max должен быть ~500 сек
    assert 490 <= result <= 510


def test_check_tasks_hung_some_fresh_returns_none(mock_runs_db: Path) -> None:
    """Одна running свежая — не считаем всё зависшим."""
    now_ms = int(time.time() * 1000)
    _insert_task(mock_runs_db, task_id="fresh", status="running", last_event_at=now_ms)
    _insert_task(mock_runs_db, task_id="old", status="running", last_event_at=now_ms - 400_000)
    from src.core.openclaw_task_poller import check_tasks_hung, poll_active_tasks

    assert check_tasks_hung(poll_active_tasks(), hung_threshold_sec=180.0) is None


def test_check_tasks_hung_custom_threshold(mock_runs_db: Path) -> None:
    """Порог можно передать через kwarg."""
    now_ms = int(time.time() * 1000)
    _insert_task(mock_runs_db, task_id="x", status="running", last_event_at=now_ms - 50_000)
    from src.core.openclaw_task_poller import check_tasks_hung, poll_active_tasks

    tasks = poll_active_tasks()
    # 50 сек < 180 → None
    assert check_tasks_hung(tasks, hung_threshold_sec=180.0) is None
    # 50 сек > 30 → detect hung
    result = check_tasks_hung(tasks, hung_threshold_sec=30.0)
    assert result is not None
    assert result > 30


# --- TaskState dataclass sanity ---


def test_task_state_is_frozen() -> None:
    """TaskState — frozen dataclass, нельзя менять поля."""
    from src.core.openclaw_task_poller import TaskState

    t = TaskState(
        task_id="x",
        status="running",
        label="lbl",
        progress_summary="p",
        last_event_at_ms=0,
        is_stale=False,
    )
    with pytest.raises(Exception):  # FrozenInstanceError
        t.task_id = "y"  # type: ignore[misc]
