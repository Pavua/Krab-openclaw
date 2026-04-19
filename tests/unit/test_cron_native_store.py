"""Тесты для src/core/cron_native_store.py."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

import src.core.cron_native_store as store


@pytest.fixture(autouse=True)
def tmp_store(tmp_path: Path):
    """Изолируем хранилище во временной директории."""
    path = tmp_path / "cron_native_jobs.json"
    store.configure_default_path(path)
    yield path
    # Сбрасываем на дефолт после теста
    store.configure_default_path(store._DEFAULT_PATH)


# ---------------------------------------------------------------------------
# add / list
# ---------------------------------------------------------------------------


def test_add_and_list():
    """add_job создаёт job, list_jobs его видит."""
    job_id = store.add_job(cron_spec="0 10 * * *", prompt="Тест промпт")
    jobs = store.list_jobs()
    assert len(jobs) == 1
    assert jobs[0]["id"] == job_id
    assert jobs[0]["cron_spec"] == "0 10 * * *"
    assert jobs[0]["prompt"] == "Тест промпт"
    assert jobs[0]["enabled"] is True
    assert jobs[0]["run_count"] == 0


def test_add_multiple():
    """Несколько jobs сохраняются корректно."""
    id1 = store.add_job("0 9 * * *", "Утро")
    id2 = store.add_job("0 21 * * *", "Вечер")
    jobs = store.list_jobs()
    assert len(jobs) == 2
    ids = {j["id"] for j in jobs}
    assert id1 in ids
    assert id2 in ids


def test_add_with_explicit_id():
    """add_job принимает явный job_id."""
    jid = store.add_job("0 12 * * *", "Полдень", job_id="my-job-01")
    assert jid == "my-job-01"
    jobs = store.list_jobs()
    assert jobs[0]["id"] == "my-job-01"


# ---------------------------------------------------------------------------
# persist across reloads
# ---------------------------------------------------------------------------


def test_persist_across_reloads(tmp_path: Path):
    """Jobs сохраняются в JSON и восстанавливаются после перезагрузки."""
    path = tmp_path / "cron_persist.json"
    store.configure_default_path(path)

    jid = store.add_job("*/30 * * * *", "Каждые 30 мин")

    # Симулируем "перезапуск" — просто вызываем list_jobs (читает файл заново)
    loaded = store.list_jobs()
    assert len(loaded) == 1
    assert loaded[0]["id"] == jid

    # Проверяем, что файл существует и корректен
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["version"] == 1
    assert raw["jobs"][0]["id"] == jid


# ---------------------------------------------------------------------------
# remove
# ---------------------------------------------------------------------------


def test_remove_existing():
    """remove_job возвращает True и job исчезает из списка."""
    jid = store.add_job("0 * * * *", "Каждый час")
    ok = store.remove_job(jid)
    assert ok is True
    assert store.list_jobs() == []


def test_remove_nonexistent():
    """remove_job возвращает False для неизвестного id."""
    ok = store.remove_job("nonexistent-id")
    assert ok is False


# ---------------------------------------------------------------------------
# toggle
# ---------------------------------------------------------------------------


def test_toggle_disable_enable():
    """toggle_job корректно переключает enabled."""
    jid = store.add_job("0 0 * * *", "Полночь")
    # Выключаем
    ok = store.toggle_job(jid, enabled=False)
    assert ok is True
    jobs = store.list_jobs()
    assert jobs[0]["enabled"] is False
    # Включаем обратно
    store.toggle_job(jid, enabled=True)
    jobs = store.list_jobs()
    assert jobs[0]["enabled"] is True


def test_toggle_nonexistent():
    """toggle_job возвращает False для несуществующего job."""
    ok = store.toggle_job("no-such-id", enabled=True)
    assert ok is False


# ---------------------------------------------------------------------------
# next_due
# ---------------------------------------------------------------------------


def test_next_due_hourly():
    """next_due для '0 * * * *' возвращает следующий час."""
    # Фиксируем "now" = 2026-04-18 10:30 UTC
    now = datetime(2026, 4, 18, 10, 30, 0, tzinfo=timezone.utc)
    job = {"cron_spec": "0 * * * *", "prompt": "x"}
    ts = store.next_due(job, now=now)
    assert ts is not None
    due = datetime.fromtimestamp(ts, tz=timezone.utc)
    # Должен быть 11:00
    assert due.hour == 11
    assert due.minute == 0


def test_next_due_daily_at_10():
    """next_due для '0 10 * * *' — следующие 10:00."""
    # now = 2026-04-18 11:00 UTC (после 10:00)
    now = datetime(2026, 4, 18, 11, 0, 0, tzinfo=timezone.utc)
    job = {"cron_spec": "0 10 * * *", "prompt": "x"}
    ts = store.next_due(job, now=now)
    assert ts is not None
    due = datetime.fromtimestamp(ts, tz=timezone.utc)
    # Должен быть следующий день 10:00
    assert due.hour == 10
    assert due.minute == 0
    assert due.day == 19  # следующий день


def test_next_due_every_30_minutes():
    """next_due для '*/30 * * * *' — следующие 30 мин."""
    now = datetime(2026, 4, 18, 10, 5, 0, tzinfo=timezone.utc)
    job = {"cron_spec": "*/30 * * * *", "prompt": "x"}
    ts = store.next_due(job, now=now)
    assert ts is not None
    due = datetime.fromtimestamp(ts, tz=timezone.utc)
    assert due.hour == 10
    assert due.minute == 30


def test_next_due_invalid_spec():
    """next_due для невалидного spec возвращает None."""
    job = {"cron_spec": "not-a-cron", "prompt": "x"}
    ts = store.next_due(job)
    assert ts is None


def test_next_due_empty_spec():
    """next_due для пустого spec возвращает None."""
    job = {"cron_spec": "", "prompt": "x"}
    ts = store.next_due(job)
    assert ts is None


def test_next_due_before_daily_time():
    """next_due для '0 10 * * *' до наступления 10:00 — сегодня."""
    now = datetime(2026, 4, 18, 9, 0, 0, tzinfo=timezone.utc)
    job = {"cron_spec": "0 10 * * *", "prompt": "x"}
    ts = store.next_due(job, now=now)
    assert ts is not None
    due = datetime.fromtimestamp(ts, tz=timezone.utc)
    assert due.hour == 10
    assert due.day == 18  # сегодня


# ---------------------------------------------------------------------------
# mark_run
# ---------------------------------------------------------------------------


def test_mark_run_increments_count():
    """mark_run обновляет last_run_at и run_count."""
    jid = store.add_job("0 * * * *", "hourly")
    store.mark_run(jid)
    jobs = store.list_jobs()
    assert jobs[0]["run_count"] == 1
    assert jobs[0]["last_run_at"] is not None


def test_load_empty_on_missing_file():
    """list_jobs() возвращает [] если файл отсутствует (нет краша)."""
    # tmp_store fixture создаёт путь но не файл
    result = store.list_jobs()
    assert result == []


def test_load_corrupt_json(tmp_path: Path):
    """list_jobs() не падает на битом JSON."""
    path = tmp_path / "corrupt.json"
    path.write_text("{{invalid json}}", encoding="utf-8")
    store.configure_default_path(path)
    result = store.list_jobs()
    assert result == []
