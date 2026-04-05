# -*- coding: utf-8 -*-
"""
Тесты для src/core/swarm_scheduler.py — рекуррентный планировщик свёрма.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from src.core.swarm_scheduler import (
    RecurringJob,
    SwarmScheduler,
    parse_interval,
)


class TestParseInterval:
    def test_minutes(self):
        assert parse_interval("30m") == 1800
        assert parse_interval("30min") == 1800
        assert parse_interval("30мин") == 1800

    def test_hours(self):
        assert parse_interval("4h") == 14400
        assert parse_interval("1ч") == 3600
        assert parse_interval("2час") == 7200

    def test_days(self):
        assert parse_interval("1d") == 86400
        assert parse_interval("2д") == 172800

    def test_minimum_interval(self):
        with pytest.raises(ValueError, match="Минимальный интервал"):
            parse_interval("1m")  # 60 сек < 300 мин

    def test_invalid_format(self):
        with pytest.raises(ValueError):
            parse_interval("abc")

    def test_unknown_unit(self):
        with pytest.raises(ValueError):
            parse_interval("5xyz")


class TestRecurringJob:
    def test_from_dict(self):
        job = RecurringJob.from_dict({
            "job_id": "abc123",
            "team": "traders",
            "topic": "BTC",
            "interval_sec": 3600,
            "total_runs": 5,
        })
        assert job.job_id == "abc123"
        assert job.team == "traders"
        assert job.total_runs == 5

    def test_from_dict_defaults(self):
        job = RecurringJob.from_dict({})
        assert job.job_id == ""
        assert job.enabled is True
        assert job.total_runs == 0


class TestSwarmSchedulerPersistence:
    @pytest.fixture()
    def scheduler(self, tmp_path: Path) -> SwarmScheduler:
        with patch("src.core.swarm_scheduler.config") as mock_config:
            mock_config.SWARM_AUTONOMOUS_ENABLED = True
            return SwarmScheduler(state_path=tmp_path / "jobs.json")

    def test_add_job(self, scheduler: SwarmScheduler):
        with patch("src.core.swarm_scheduler.config") as mock_config:
            mock_config.SWARM_AUTONOMOUS_ENABLED = True
            job = scheduler.add_job(team="traders", topic="BTC анализ", interval_sec=3600)
        assert job.team == "traders"
        assert job.topic == "BTC анализ"
        assert job.interval_sec == 3600
        assert scheduler._path.exists()

    def test_add_job_disabled(self, tmp_path: Path):
        with patch("src.core.swarm_scheduler.config") as mock_config:
            mock_config.SWARM_AUTONOMOUS_ENABLED = False
            sched = SwarmScheduler(state_path=tmp_path / "jobs.json")
            with pytest.raises(RuntimeError, match="выключены"):
                sched.add_job(team="traders", topic="test", interval_sec=3600)

    def test_add_job_max_limit(self, scheduler: SwarmScheduler):
        with patch("src.core.swarm_scheduler.config") as mock_config:
            mock_config.SWARM_AUTONOMOUS_ENABLED = True
            for i in range(10):
                scheduler.add_job(team="traders", topic=f"t{i}", interval_sec=3600)
            with pytest.raises(RuntimeError, match="Максимум"):
                scheduler.add_job(team="traders", topic="t11", interval_sec=3600)

    def test_remove_job(self, scheduler: SwarmScheduler):
        with patch("src.core.swarm_scheduler.config") as mock_config:
            mock_config.SWARM_AUTONOMOUS_ENABLED = True
            job = scheduler.add_job(team="traders", topic="test", interval_sec=3600)
        assert scheduler.remove_job(job.job_id) is True
        assert scheduler.remove_job(job.job_id) is False  # already removed

    def test_list_jobs(self, scheduler: SwarmScheduler):
        with patch("src.core.swarm_scheduler.config") as mock_config:
            mock_config.SWARM_AUTONOMOUS_ENABLED = True
            scheduler.add_job(team="traders", topic="BTC", interval_sec=3600)
            scheduler.add_job(team="coders", topic="бот", interval_sec=7200)
        jobs = scheduler.list_jobs()
        assert len(jobs) == 2
        assert {j.team for j in jobs} == {"traders", "coders"}

    def test_save_and_reload(self, tmp_path: Path):
        path = tmp_path / "jobs.json"
        with patch("src.core.swarm_scheduler.config") as mock_config:
            mock_config.SWARM_AUTONOMOUS_ENABLED = True
            s1 = SwarmScheduler(state_path=path)
            s1.add_job(team="traders", topic="BTC", interval_sec=3600)
            s1.add_job(team="analysts", topic="ETH", interval_sec=7200)

        s2 = SwarmScheduler(state_path=path)
        assert len(s2.list_jobs()) == 2

    def test_format_jobs_empty(self, scheduler: SwarmScheduler):
        result = scheduler.format_jobs()
        assert "Нет запланированных" in result

    def test_format_jobs_with_data(self, scheduler: SwarmScheduler):
        with patch("src.core.swarm_scheduler.config") as mock_config:
            mock_config.SWARM_AUTONOMOUS_ENABLED = True
            scheduler.add_job(team="traders", topic="BTC", interval_sec=14400)
        result = scheduler.format_jobs()
        assert "traders" in result
        assert "BTC" in result

    def test_get_status(self, scheduler: SwarmScheduler):
        status = scheduler.get_status()
        assert "total_jobs" in status
        assert "jobs" in status

    def test_minimum_interval_enforced(self, scheduler: SwarmScheduler):
        with patch("src.core.swarm_scheduler.config") as mock_config:
            mock_config.SWARM_AUTONOMOUS_ENABLED = True
            job = scheduler.add_job(team="traders", topic="test", interval_sec=10)
        # Should be bumped to _MIN_INTERVAL_SEC (300)
        assert job.interval_sec == 300


class TestSwarmSchedulerCorrupted:
    def test_corrupted_file_recovers(self, tmp_path: Path):
        path = tmp_path / "bad.json"
        path.write_text("not json", encoding="utf-8")
        sched = SwarmScheduler(state_path=path)
        assert sched.list_jobs() == []
