# -*- coding: utf-8 -*-
"""
Тесты для workflow_type в swarm_scheduler.py.

Покрывают:
- WorkflowType enum
- RecurringJob.from_dict с workflow_type
- add_job с разными workflow_type
- _execute_research_job / _execute_report_job / _execute_standard_job
- backward compatibility (старые записи без workflow_type)
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.swarm_scheduler import (
    RecurringJob,
    SwarmScheduler,
    WorkflowType,
    parse_interval,
)


# ── WorkflowType enum ────────────────────────────────────────────────────────

class TestWorkflowType:
    def test_values(self):
        assert WorkflowType.STANDARD == "standard"
        assert WorkflowType.RESEARCH == "research"
        assert WorkflowType.REPORT == "report"

    def test_from_string(self):
        assert WorkflowType("standard") == WorkflowType.STANDARD
        assert WorkflowType("research") == WorkflowType.RESEARCH
        assert WorkflowType("report") == WorkflowType.REPORT

    def test_invalid_raises(self):
        with pytest.raises(ValueError):
            WorkflowType("unknown")

    def test_is_str_subclass(self):
        """WorkflowType — str, можно сравнивать со строкой."""
        assert WorkflowType.STANDARD == "standard"


# ── RecurringJob с workflow_type ─────────────────────────────────────────────

class TestRecurringJobWorkflow:
    def test_default_workflow_type(self):
        job = RecurringJob(
            job_id="abc",
            team="traders",
            topic="test",
            interval_sec=3600,
        )
        assert job.workflow_type == WorkflowType.STANDARD

    def test_from_dict_with_workflow_type(self):
        job = RecurringJob.from_dict({
            "job_id": "abc",
            "team": "analysts",
            "topic": "research topic",
            "interval_sec": 7200,
            "workflow_type": "research",
        })
        assert job.workflow_type == WorkflowType.RESEARCH

    def test_from_dict_report(self):
        job = RecurringJob.from_dict({
            "job_id": "xyz",
            "team": "coders",
            "topic": "weekly report",
            "interval_sec": 86400,
            "workflow_type": "report",
        })
        assert job.workflow_type == WorkflowType.REPORT

    def test_from_dict_backward_compat_no_field(self):
        """Старые записи без workflow_type → стандарт."""
        job = RecurringJob.from_dict({
            "job_id": "old",
            "team": "traders",
            "topic": "legacy",
            "interval_sec": 3600,
        })
        assert job.workflow_type == WorkflowType.STANDARD

    def test_from_dict_invalid_workflow_falls_back(self):
        """Невалидный workflow_type → дефолт standard, не падаем."""
        job = RecurringJob.from_dict({
            "job_id": "bad",
            "team": "traders",
            "topic": "test",
            "interval_sec": 3600,
            "workflow_type": "garbage",
        })
        assert job.workflow_type == WorkflowType.STANDARD


# ── SwarmScheduler.add_job с workflow_type ───────────────────────────────────

class TestSwarmSchedulerAddJobWorkflow:
    @pytest.fixture()
    def scheduler(self, tmp_path: Path) -> SwarmScheduler:
        with patch("src.core.swarm_scheduler.config") as mock_config:
            mock_config.SWARM_AUTONOMOUS_ENABLED = True
            return SwarmScheduler(state_path=tmp_path / "jobs.json")

    def _add(self, scheduler, **kwargs):
        with patch("src.core.swarm_scheduler.config") as mock_config:
            mock_config.SWARM_AUTONOMOUS_ENABLED = True
            return scheduler.add_job(**kwargs)

    def test_add_standard_job(self, scheduler):
        job = self._add(scheduler, team="traders", topic="BTC", interval_sec=3600)
        assert job.workflow_type == WorkflowType.STANDARD

    def test_add_research_job(self, scheduler):
        job = self._add(
            scheduler,
            team="analysts",
            topic="крипторынок",
            interval_sec=3600,
            workflow_type=WorkflowType.RESEARCH,
        )
        assert job.workflow_type == WorkflowType.RESEARCH

    def test_add_report_job(self, scheduler):
        job = self._add(
            scheduler,
            team="coders",
            topic="weekly progress",
            interval_sec=86400,
            workflow_type=WorkflowType.REPORT,
        )
        assert job.workflow_type == WorkflowType.REPORT

    def test_add_job_string_workflow(self, scheduler):
        """Можно передать строку вместо enum."""
        job = self._add(
            scheduler,
            team="analysts",
            topic="market",
            interval_sec=3600,
            workflow_type="research",
        )
        assert job.workflow_type == WorkflowType.RESEARCH

    def test_add_job_invalid_workflow_raises(self, scheduler):
        with pytest.raises(ValueError, match="Неизвестный workflow_type"):
            self._add(
                scheduler,
                team="traders",
                topic="test",
                interval_sec=3600,
                workflow_type="bogus",
            )

    def test_workflow_type_persisted(self, tmp_path: Path):
        """workflow_type сохраняется в JSON и восстанавливается."""
        path = tmp_path / "jobs.json"
        with patch("src.core.swarm_scheduler.config") as mock_config:
            mock_config.SWARM_AUTONOMOUS_ENABLED = True
            s1 = SwarmScheduler(state_path=path)
            s1.add_job(
                team="analysts",
                topic="research topic",
                interval_sec=3600,
                workflow_type=WorkflowType.RESEARCH,
            )
            s1.add_job(
                team="traders",
                topic="report topic",
                interval_sec=86400,
                workflow_type=WorkflowType.REPORT,
            )

        s2 = SwarmScheduler(state_path=path)
        jobs = {j.team: j for j in s2.list_jobs()}
        assert jobs["analysts"].workflow_type == WorkflowType.RESEARCH
        assert jobs["traders"].workflow_type == WorkflowType.REPORT


# ── format_jobs показывает workflow_type ─────────────────────────────────────

class TestFormatJobsWorkflow:
    @pytest.fixture()
    def scheduler(self, tmp_path: Path) -> SwarmScheduler:
        with patch("src.core.swarm_scheduler.config") as mock_config:
            mock_config.SWARM_AUTONOMOUS_ENABLED = True
            return SwarmScheduler(state_path=tmp_path / "jobs.json")

    def test_format_shows_research(self, scheduler):
        with patch("src.core.swarm_scheduler.config") as mock_config:
            mock_config.SWARM_AUTONOMOUS_ENABLED = True
            scheduler.add_job(
                team="analysts",
                topic="crypto",
                interval_sec=3600,
                workflow_type=WorkflowType.RESEARCH,
            )
        result = scheduler.format_jobs()
        assert "research" in result

    def test_format_shows_report(self, scheduler):
        with patch("src.core.swarm_scheduler.config") as mock_config:
            mock_config.SWARM_AUTONOMOUS_ENABLED = True
            scheduler.add_job(
                team="coders",
                topic="progress",
                interval_sec=86400,
                workflow_type=WorkflowType.REPORT,
            )
        result = scheduler.format_jobs()
        assert "report" in result


# ── get_status включает workflow_type ────────────────────────────────────────

class TestGetStatusWorkflow:
    def test_status_includes_workflow_type(self, tmp_path: Path):
        with patch("src.core.swarm_scheduler.config") as mock_config:
            mock_config.SWARM_AUTONOMOUS_ENABLED = True
            s = SwarmScheduler(state_path=tmp_path / "jobs.json")
            s.add_job(
                team="analysts",
                topic="test",
                interval_sec=3600,
                workflow_type=WorkflowType.RESEARCH,
            )

        status = s.get_status()
        jobs = status["jobs"]
        assert len(jobs) == 1
        assert jobs[0]["workflow_type"] == WorkflowType.RESEARCH


def _make_swarm_mocks(team_registry: dict, result: str = "result"):
    """Создаёт mock-модули swarm/swarm_bus для патча lazy-imports."""
    mock_room = AsyncMock()
    mock_room.run_round = AsyncMock(return_value=result)

    mock_swarm_mod = MagicMock()
    mock_swarm_mod.AgentRoom = MagicMock(return_value=mock_room)

    mock_bus_mod = MagicMock()
    mock_bus_mod.TEAM_REGISTRY = team_registry
    mock_bus_mod.swarm_bus = MagicMock()

    mock_artifact_mod = MagicMock()

    return mock_room, {
        "src.core.swarm": mock_swarm_mod,
        "src.core.swarm_bus": mock_bus_mod,
        "src.core.swarm_artifact_store": mock_artifact_mod,
    }


# ── _execute_standard_job ────────────────────────────────────────────────────

class TestExecuteStandardJob:
    @pytest.fixture()
    def scheduler_with_mocks(self, tmp_path: Path):
        s = SwarmScheduler(state_path=tmp_path / "jobs.json")
        s._sender = AsyncMock()
        s._owner_chat_id = "123"
        return s

    @pytest.mark.asyncio
    async def test_standard_job_calls_run_round(self, scheduler_with_mocks):
        job = RecurringJob(
            job_id="test1",
            team="traders",
            topic="BTC",
            interval_sec=3600,
            workflow_type=WorkflowType.STANDARD,
        )

        mock_room, mods = _make_swarm_mocks({"traders": ["role1"]}, "standard result")
        scheduler_with_mocks._router_factory = MagicMock()

        with patch.dict("sys.modules", mods):
            result = await scheduler_with_mocks._execute_standard_job(job)

        assert result == "standard result"
        mock_room.run_round.assert_called_once()

    @pytest.mark.asyncio
    async def test_standard_job_missing_team_raises(self, scheduler_with_mocks):
        job = RecurringJob(
            job_id="test2",
            team="nonexistent",
            topic="test",
            interval_sec=3600,
        )
        _, mods = _make_swarm_mocks({})
        scheduler_with_mocks._router_factory = MagicMock()

        with patch.dict("sys.modules", mods):
            with pytest.raises(RuntimeError, match="team_not_found"):
                await scheduler_with_mocks._execute_standard_job(job)


# ── _execute_research_job ────────────────────────────────────────────────────

class TestExecuteResearchJob:
    @pytest.fixture()
    def scheduler_with_mocks(self, tmp_path: Path):
        s = SwarmScheduler(state_path=tmp_path / "jobs.json")
        s._sender = AsyncMock()
        s._owner_chat_id = "123"
        return s

    @pytest.mark.asyncio
    async def test_research_uses_analysts_team(self, scheduler_with_mocks):
        """Research pipeline всегда использует analysts."""
        job = RecurringJob(
            job_id="r1",
            team="traders",  # задано traders, но research перебросит на analysts
            topic="крипто",
            interval_sec=3600,
            workflow_type=WorkflowType.RESEARCH,
        )

        mock_room, mods = _make_swarm_mocks({"analysts": ["analyst_role"]}, "research findings")
        scheduler_with_mocks._router_factory = MagicMock()

        with patch.dict("sys.modules", mods):
            result = await scheduler_with_mocks._execute_research_job(job)

        assert result == "research findings"

    @pytest.mark.asyncio
    async def test_research_prompt_contains_web_search(self, scheduler_with_mocks):
        """Research промпт содержит требование web_search."""
        job = RecurringJob(
            job_id="r2",
            team="analysts",
            topic="AI trends",
            interval_sec=3600,
            workflow_type=WorkflowType.RESEARCH,
        )

        captured_topic = []

        mock_room = AsyncMock()

        async def capture_run_round(topic, *args, **kwargs):
            captured_topic.append(topic)
            return "result"

        mock_room.run_round = capture_run_round

        mock_swarm_mod = MagicMock()
        mock_swarm_mod.AgentRoom = MagicMock(return_value=mock_room)
        mock_bus_mod = MagicMock()
        mock_bus_mod.TEAM_REGISTRY = {"analysts": ["role"]}
        mock_bus_mod.swarm_bus = MagicMock()
        mock_artifact_mod = MagicMock()

        scheduler_with_mocks._router_factory = MagicMock()

        with patch.dict("sys.modules", {
            "src.core.swarm": mock_swarm_mod,
            "src.core.swarm_bus": mock_bus_mod,
            "src.core.swarm_artifact_store": mock_artifact_mod,
        }):
            await scheduler_with_mocks._execute_research_job(job)

        assert captured_topic, "run_round не был вызван"
        assert "web_search" in captured_topic[0]
        assert "AI trends" in captured_topic[0]

    @pytest.mark.asyncio
    async def test_research_no_analysts_team_raises(self, scheduler_with_mocks):
        job = RecurringJob(
            job_id="r3",
            team="traders",
            topic="test",
            interval_sec=3600,
            workflow_type=WorkflowType.RESEARCH,
        )
        _, mods = _make_swarm_mocks({})
        scheduler_with_mocks._router_factory = MagicMock()

        with patch.dict("sys.modules", mods):
            with pytest.raises(RuntimeError, match="team_not_found:analysts"):
                await scheduler_with_mocks._execute_research_job(job)


# ── _execute_report_job ──────────────────────────────────────────────────────

class TestExecuteReportJob:
    @pytest.fixture()
    def scheduler_with_mocks(self, tmp_path: Path):
        s = SwarmScheduler(state_path=tmp_path / "jobs.json")
        s._sender = AsyncMock()
        s._owner_chat_id = "123"
        return s

    @pytest.mark.asyncio
    async def test_report_prompt_contains_structure(self, scheduler_with_mocks):
        """Report промпт требует структурированный отчёт."""
        job = RecurringJob(
            job_id="rep1",
            team="coders",
            topic="sprint progress",
            interval_sec=86400,
            workflow_type=WorkflowType.REPORT,
        )

        captured_topic = []

        mock_room = AsyncMock()

        async def capture_run_round(topic, *args, **kwargs):
            captured_topic.append(topic)
            return "report result"

        mock_room.run_round = capture_run_round

        mock_swarm_mod = MagicMock()
        mock_swarm_mod.AgentRoom = MagicMock(return_value=mock_room)
        mock_bus_mod = MagicMock()
        mock_bus_mod.TEAM_REGISTRY = {"coders": ["coder_role"]}
        mock_bus_mod.swarm_bus = MagicMock()
        mock_artifact_mod = MagicMock()

        scheduler_with_mocks._router_factory = MagicMock()

        with patch.dict("sys.modules", {
            "src.core.swarm": mock_swarm_mod,
            "src.core.swarm_bus": mock_bus_mod,
            "src.core.swarm_artifact_store": mock_artifact_mod,
        }):
            result = await scheduler_with_mocks._execute_report_job(job)

        assert result == "report result"
        assert captured_topic, "run_round не был вызван"
        assert "отчёт" in captured_topic[0].lower() or "report" in captured_topic[0].lower()
        assert "sprint progress" in captured_topic[0]

    @pytest.mark.asyncio
    async def test_report_uses_job_team(self, scheduler_with_mocks):
        """Report использует команду из job, а не analysts."""
        job = RecurringJob(
            job_id="rep2",
            team="traders",
            topic="trading metrics",
            interval_sec=86400,
            workflow_type=WorkflowType.REPORT,
        )

        mock_room, mods = _make_swarm_mocks({"traders": ["trader_role"]}, "traders report")
        scheduler_with_mocks._router_factory = MagicMock()

        with patch.dict("sys.modules", mods):
            result = await scheduler_with_mocks._execute_report_job(job)

        assert result == "traders report"

    @pytest.mark.asyncio
    async def test_report_missing_team_raises(self, scheduler_with_mocks):
        job = RecurringJob(
            job_id="rep3",
            team="nonexistent",
            topic="test",
            interval_sec=3600,
            workflow_type=WorkflowType.REPORT,
        )
        _, mods = _make_swarm_mocks({})
        scheduler_with_mocks._router_factory = MagicMock()

        with patch.dict("sys.modules", mods):
            with pytest.raises(RuntimeError, match="team_not_found:nonexistent"):
                await scheduler_with_mocks._execute_report_job(job)


# ── _execute_job dispatch ────────────────────────────────────────────────────

class TestExecuteJobDispatch:
    """Проверяем что _execute_job вызывает нужный метод по workflow_type."""

    @pytest.fixture()
    def scheduler_with_mocks(self, tmp_path: Path):
        s = SwarmScheduler(state_path=tmp_path / "jobs.json")
        s._sender = AsyncMock()
        s._owner_chat_id = "owner_chat"
        s._router_factory = MagicMock()
        return s

    @pytest.mark.asyncio
    async def test_dispatch_standard(self, scheduler_with_mocks):
        job = RecurringJob(
            job_id="d1", team="traders", topic="BTC", interval_sec=3600,
            workflow_type=WorkflowType.STANDARD,
        )
        scheduler_with_mocks._execute_standard_job = AsyncMock(return_value="std")
        scheduler_with_mocks._execute_research_job = AsyncMock(return_value="res")
        scheduler_with_mocks._execute_report_job = AsyncMock(return_value="rep")

        await scheduler_with_mocks._execute_job(job)

        scheduler_with_mocks._execute_standard_job.assert_awaited_once_with(job)
        scheduler_with_mocks._execute_research_job.assert_not_awaited()
        scheduler_with_mocks._execute_report_job.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_dispatch_research(self, scheduler_with_mocks):
        job = RecurringJob(
            job_id="d2", team="analysts", topic="AI", interval_sec=3600,
            workflow_type=WorkflowType.RESEARCH,
        )
        scheduler_with_mocks._execute_standard_job = AsyncMock(return_value="std")
        scheduler_with_mocks._execute_research_job = AsyncMock(return_value="res")
        scheduler_with_mocks._execute_report_job = AsyncMock(return_value="rep")

        await scheduler_with_mocks._execute_job(job)

        scheduler_with_mocks._execute_research_job.assert_awaited_once_with(job)
        scheduler_with_mocks._execute_standard_job.assert_not_awaited()
        scheduler_with_mocks._execute_report_job.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_dispatch_report(self, scheduler_with_mocks):
        job = RecurringJob(
            job_id="d3", team="coders", topic="sprint", interval_sec=86400,
            workflow_type=WorkflowType.REPORT,
        )
        scheduler_with_mocks._execute_standard_job = AsyncMock(return_value="std")
        scheduler_with_mocks._execute_research_job = AsyncMock(return_value="res")
        scheduler_with_mocks._execute_report_job = AsyncMock(return_value="rep")

        await scheduler_with_mocks._execute_job(job)

        scheduler_with_mocks._execute_report_job.assert_awaited_once_with(job)
        scheduler_with_mocks._execute_standard_job.assert_not_awaited()
        scheduler_with_mocks._execute_research_job.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_dispatch_updates_total_runs(self, scheduler_with_mocks):
        """После успешного dispatch total_runs инкрементируется."""
        job = RecurringJob(
            job_id="d4", team="traders", topic="test", interval_sec=3600,
            workflow_type=WorkflowType.STANDARD,
        )
        scheduler_with_mocks._jobs[job.job_id] = job
        scheduler_with_mocks._execute_standard_job = AsyncMock(return_value="ok")

        await scheduler_with_mocks._execute_job(job)

        assert job.total_runs == 1
        assert job.last_error == ""

    @pytest.mark.asyncio
    async def test_dispatch_sends_message_with_workflow_label(self, scheduler_with_mocks):
        """Заголовок сообщения owner-у содержит label по workflow_type."""
        job = RecurringJob(
            job_id="d5", team="analysts", topic="research_topic", interval_sec=3600,
            workflow_type=WorkflowType.RESEARCH,
        )
        scheduler_with_mocks._jobs[job.job_id] = job
        scheduler_with_mocks._execute_research_job = AsyncMock(return_value="research result")

        await scheduler_with_mocks._execute_job(job)

        scheduler_with_mocks._sender.assert_awaited_once()
        sent_msg = scheduler_with_mocks._sender.call_args[0][1]
        assert "Research" in sent_msg

    @pytest.mark.asyncio
    async def test_dispatch_no_router_factory_sets_error(self, scheduler_with_mocks):
        """Без router_factory — job пишет last_error."""
        scheduler_with_mocks._router_factory = None
        job = RecurringJob(
            job_id="d6", team="traders", topic="test", interval_sec=3600,
        )
        scheduler_with_mocks._jobs[job.job_id] = job

        await scheduler_with_mocks._execute_job(job)

        assert "router_factory_not_bound" in job.last_error
