# -*- coding: utf-8 -*-
"""
Тесты для src/core/swarm_memory.py — персистентная память свёрма.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.core.swarm_memory import SwarmMemory, SwarmRunRecord


@pytest.fixture()
def tmp_memory(tmp_path: Path) -> SwarmMemory:
    """Создаёт SwarmMemory с временным файлом."""
    return SwarmMemory(state_path=tmp_path / "swarm_memory.json")


class TestSwarmRunRecord:
    def test_from_dict_round_trip(self):
        rec = SwarmRunRecord(
            run_id="traders_1234",
            team="traders",
            topic="BTC анализ",
            result_summary="BTC на уровне поддержки",
            delegations=["coders"],
            duration_sec=12.5,
        )
        d = {
            "run_id": rec.run_id,
            "team": rec.team,
            "topic": rec.topic,
            "result_summary": rec.result_summary,
            "delegations": rec.delegations,
            "created_at": rec.created_at,
            "duration_sec": rec.duration_sec,
            "metadata": rec.metadata,
        }
        restored = SwarmRunRecord.from_dict(d)
        assert restored.run_id == rec.run_id
        assert restored.team == rec.team
        assert restored.delegations == ["coders"]

    def test_from_dict_missing_fields(self):
        rec = SwarmRunRecord.from_dict({})
        assert rec.run_id == ""
        assert rec.delegations == []


class TestSwarmMemorySaveAndLoad:
    def test_save_run_creates_file(self, tmp_memory: SwarmMemory):
        rec = tmp_memory.save_run(
            team="traders",
            topic="BTC",
            result="Анализ BTC завершён",
        )
        assert rec.team == "traders"
        assert rec.run_id.startswith("traders_")
        assert tmp_memory._path.exists()

    def test_save_and_reload(self, tmp_path: Path):
        path = tmp_path / "mem.json"
        mem1 = SwarmMemory(state_path=path)
        mem1.save_run(team="traders", topic="ETH", result="ETH растёт")
        mem1.save_run(team="traders", topic="BTC", result="BTC падает")
        mem1.save_run(team="coders", topic="бот", result="Бот написан")

        # Создаём новый экземпляр — должен загрузить с диска
        mem2 = SwarmMemory(state_path=path)
        assert len(mem2.get_recent("traders", 10)) == 2
        assert len(mem2.get_recent("coders", 10)) == 1

    def test_fifo_trim(self, tmp_memory: SwarmMemory):
        for i in range(60):
            tmp_memory.save_run(team="traders", topic=f"topic_{i}", result=f"result_{i}")
        # _MAX_ENTRIES_PER_TEAM = 50
        records = tmp_memory.get_recent("traders", 100)
        assert len(records) == 50
        # Самый старый сохранённый должен быть topic_10 (60 - 50)
        assert records[0].topic == "topic_10"

    def test_save_clips_long_result(self, tmp_memory: SwarmMemory):
        long_result = "x" * 5000
        rec = tmp_memory.save_run(team="traders", topic="test", result=long_result)
        assert len(rec.result_summary) < 2000
        assert "[...обрезано]" in rec.result_summary


class TestSwarmMemoryRetrieval:
    def test_get_recent_empty(self, tmp_memory: SwarmMemory):
        assert tmp_memory.get_recent("unknown") == []

    def test_get_recent_respects_count(self, tmp_memory: SwarmMemory):
        for i in range(10):
            tmp_memory.save_run(team="traders", topic=f"t{i}", result=f"r{i}")
        assert len(tmp_memory.get_recent("traders", 3)) == 3

    def test_get_context_for_injection_empty(self, tmp_memory: SwarmMemory):
        assert tmp_memory.get_context_for_injection("traders") == ""

    def test_get_context_for_injection_format(self, tmp_memory: SwarmMemory):
        tmp_memory.save_run(team="traders", topic="BTC", result="BTC = 60k", delegations=["coders"])
        ctx = tmp_memory.get_context_for_injection("traders")
        assert "Память команды traders" in ctx
        assert "BTC" in ctx
        assert "coders" in ctx
        assert "Конец памяти" in ctx

    def test_get_team_stats(self, tmp_memory: SwarmMemory):
        tmp_memory.save_run(team="traders", topic="BTC", result="r1", duration_sec=10.0)
        tmp_memory.save_run(team="traders", topic="ETH", result="r2", duration_sec=20.0)
        stats = tmp_memory.get_team_stats("traders")
        assert stats["total_runs"] == 2
        assert stats["avg_duration_sec"] == 15.0

    def test_get_team_stats_empty(self, tmp_memory: SwarmMemory):
        stats = tmp_memory.get_team_stats("unknown")
        assert stats["total_runs"] == 0


class TestSwarmMemoryFormatting:
    def test_format_history_empty(self, tmp_memory: SwarmMemory):
        result = tmp_memory.format_history("traders")
        assert "ещё не запускалась" in result

    def test_format_history_with_data(self, tmp_memory: SwarmMemory):
        tmp_memory.save_run(
            team="traders",
            topic="BTC анализ",
            result="BTC на поддержке",
            delegations=["coders"],
            duration_sec=5.2,
        )
        result = tmp_memory.format_history("traders")
        assert "Память команды traders" in result
        assert "BTC анализ" in result


class TestSwarmMemoryManagement:
    def test_clear_team(self, tmp_memory: SwarmMemory):
        tmp_memory.save_run(team="traders", topic="t1", result="r1")
        tmp_memory.save_run(team="traders", topic="t2", result="r2")
        cleared = tmp_memory.clear_team("traders")
        assert cleared == 2
        assert tmp_memory.get_recent("traders") == []

    def test_clear_team_empty(self, tmp_memory: SwarmMemory):
        assert tmp_memory.clear_team("unknown") == 0

    def test_all_teams(self, tmp_memory: SwarmMemory):
        tmp_memory.save_run(team="traders", topic="t", result="r")
        tmp_memory.save_run(team="coders", topic="t", result="r")
        teams = tmp_memory.all_teams()
        assert set(teams) == {"traders", "coders"}


class TestSwarmMemoryCompression:
    def test_strips_swarm_header(self, tmp_memory: SwarmMemory):
        result = "🐝 **Swarm Room: BTC**\n\nActual content here"
        rec = tmp_memory.save_run(team="traders", topic="BTC", result=result)
        assert not rec.result_summary.startswith("🐝")
        assert "Actual content" in rec.result_summary

    def test_case_insensitive_team(self, tmp_memory: SwarmMemory):
        tmp_memory.save_run(team="Traders", topic="t", result="r")
        assert len(tmp_memory.get_recent("traders")) == 1
        assert len(tmp_memory.get_recent("TRADERS")) == 1  # get_recent normalizes to lower


class TestSwarmMemoryCorruptedFile:
    def test_corrupted_json_recovers(self, tmp_path: Path):
        path = tmp_path / "bad.json"
        path.write_text("not json at all {{{", encoding="utf-8")
        mem = SwarmMemory(state_path=path)
        assert mem._data == {}
        # Должен работать после recovery
        mem.save_run(team="traders", topic="t", result="r")
        assert len(mem.get_recent("traders")) == 1


class TestSwarmMemoryAsyncLoad:
    """Wave 22-H: async-ified load + elapsed_ms instrumentation."""

    def test_load_logs_elapsed_ms(self, tmp_path: Path, capfd: pytest.CaptureFixture[str]):
        """_load логирует elapsed_ms после успешной загрузки.

        structlog в Krab сконфигурирован на ConsoleRenderer → stdout, а не stdlib
        propagation, поэтому caplog не видит события. Проверяем через capfd.
        """
        import json

        path = tmp_path / "mem.json"
        path.write_text(json.dumps({"coders": []}), encoding="utf-8")

        SwarmMemory(state_path=path)

        out = capfd.readouterr().out
        assert "swarm_memory_loaded" in out
        assert "elapsed_ms=" in out

    def test_load_async_completes(self, tmp_path: Path):
        """load_async() перезагружает state через thread."""
        import asyncio
        import json

        path = tmp_path / "mem.json"
        path.write_text(
            json.dumps({"traders": [{"run_id": "x", "team": "traders", "topic": "t",
                                     "result_summary": "r", "delegations": [],
                                     "created_at": "2026-01-01T00:00:00+00:00",
                                     "duration_sec": 0.0, "metadata": {}}]}),
            encoding="utf-8",
        )

        mem = SwarmMemory(state_path=path)
        # Принудительно сбросим кеш и перечитаем async
        mem._data = {}

        asyncio.run(mem.load_async())
        assert "traders" in mem._data
        assert len(mem._data["traders"]) == 1
