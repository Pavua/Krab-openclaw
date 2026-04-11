# -*- coding: utf-8 -*-
"""
Тесты для src/core/swarm_artifact_store.py — файловое хранилище swarm-артефактов.

Покрываем: save_round_artifact, list_artifacts (фильтр по team, лимит),
get_artifact, cleanup_old, round-trip сохранения, edge cases.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from src.core.swarm_artifact_store import SwarmArtifactStore


def _write_artifact(base_dir: Path, filename: str, team: str, mtime: float) -> Path:
    """Вспомогательная функция: пишет минимальный артефакт с заданным mtime."""
    p = base_dir / filename
    payload = {"team": team, "topic": "t", "result": "r", "delegations": [], "duration_sec": 0.0, "timestamp": int(mtime)}
    p.write_text(json.dumps(payload), encoding="utf-8")
    os.utime(p, (mtime, mtime))
    return p


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture()
def store(tmp_path: Path) -> SwarmArtifactStore:
    """Изолированный store в tmp_path."""
    return SwarmArtifactStore(base_dir=tmp_path / "artifacts")


# ------------------------------------------------------------------
# save_round_artifact
# ------------------------------------------------------------------


class TestSaveRoundArtifact:
    def test_returns_path_object(self, store: SwarmArtifactStore) -> None:
        """save_round_artifact возвращает Path."""
        result = store.save_round_artifact(team="coders", topic="task", result="done")
        assert isinstance(result, Path)

    def test_file_is_created(self, store: SwarmArtifactStore) -> None:
        """Файл должен существовать после сохранения."""
        path = store.save_round_artifact(team="coders", topic="task", result="done")
        assert path.exists()

    def test_payload_fields_persisted(self, store: SwarmArtifactStore) -> None:
        """Все ключевые поля корректно записываются в JSON."""
        path = store.save_round_artifact(
            team="traders",
            topic="BTC анализ",
            result="Результат",
            delegations=["role_a", "role_b"],
            verification={"score": 9},
            duration_sec=3.14,
        )
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["team"] == "traders"
        assert data["topic"] == "BTC анализ"
        assert data["result"] == "Результат"
        assert data["delegations"] == ["role_a", "role_b"]
        assert data["verification"] == {"score": 9}
        assert data["duration_sec"] == 3.14

    def test_empty_delegations_default(self, store: SwarmArtifactStore) -> None:
        """delegations=None → пустой список в файле."""
        path = store.save_round_artifact(team="analysts", topic="t", result="r")
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["delegations"] == []

    def test_topic_truncated_at_200(self, store: SwarmArtifactStore) -> None:
        """Топик обрезается до 200 символов."""
        long_topic = "X" * 300
        path = store.save_round_artifact(team="coders", topic=long_topic, result="r")
        data = json.loads(path.read_text(encoding="utf-8"))
        assert len(data["topic"]) == 200

    def test_result_truncated_at_5000(self, store: SwarmArtifactStore) -> None:
        """Результат обрезается до 5000 символов."""
        long_result = "Y" * 6000
        path = store.save_round_artifact(team="coders", topic="t", result=long_result)
        data = json.loads(path.read_text(encoding="utf-8"))
        assert len(data["result"]) == 5000

    def test_filename_contains_safe_team(self, store: SwarmArtifactStore) -> None:
        """Имя файла содержит нормализованное название команды."""
        path = store.save_round_artifact(team="MY/Team", topic="t", result="r")
        assert "my_team" in path.name

    def test_timestamp_iso_present(self, store: SwarmArtifactStore) -> None:
        """Поле timestamp_iso в формате ISO 8601."""
        path = store.save_round_artifact(team="coders", topic="t", result="r")
        data = json.loads(path.read_text(encoding="utf-8"))
        assert "T" in data["timestamp_iso"]
        assert "Z" in data["timestamp_iso"]


# ------------------------------------------------------------------
# list_artifacts
# ------------------------------------------------------------------


class TestListArtifacts:
    def test_empty_store_returns_empty_list(self, store: SwarmArtifactStore) -> None:
        """Новый store — пустой список."""
        assert store.list_artifacts() == []

    def test_returns_all_artifacts(self, store: SwarmArtifactStore) -> None:
        """Без фильтров возвращает все сохранённые артефакты."""
        store.save_round_artifact(team="coders", topic="t1", result="r1")
        store.save_round_artifact(team="traders", topic="t2", result="r2")
        results = store.list_artifacts()
        assert len(results) == 2

    def test_filter_by_team(self, store: SwarmArtifactStore) -> None:
        """Фильтр team возвращает только нужную команду."""
        store.save_round_artifact(team="coders", topic="t1", result="r1")
        store.save_round_artifact(team="traders", topic="t2", result="r2")
        coders = store.list_artifacts(team="coders")
        assert len(coders) == 1
        assert coders[0]["team"] == "coders"

    def test_team_filter_case_insensitive(self, store: SwarmArtifactStore) -> None:
        """Фильтр team нечувствителен к регистру."""
        store.save_round_artifact(team="Traders", topic="t", result="r")
        results = store.list_artifacts(team="traders")
        assert len(results) == 1

    def test_limit_respected(self, store: SwarmArtifactStore, tmp_path: Path) -> None:
        """limit ограничивает количество результатов."""
        base = tmp_path / "limit_arts"
        base.mkdir()
        s = SwarmArtifactStore(base_dir=base)
        base_ts = 1_700_000_000.0
        for i in range(5):
            _write_artifact(base, f"coders_{int(base_ts) + i}.json", "coders", base_ts + i)
        results = s.list_artifacts(limit=3)
        assert len(results) == 3

    def test_path_field_injected(self, store: SwarmArtifactStore) -> None:
        """Каждый элемент списка содержит поле _path."""
        store.save_round_artifact(team="coders", topic="t", result="r")
        results = store.list_artifacts()
        assert "_path" in results[0]


# ------------------------------------------------------------------
# get_artifact
# ------------------------------------------------------------------


class TestGetArtifact:
    def test_get_existing_by_filename(self, store: SwarmArtifactStore) -> None:
        """get_artifact возвращает данные по имени файла."""
        path = store.save_round_artifact(team="analysts", topic="research", result="conclusion")
        data = store.get_artifact(path.name)
        assert data is not None
        assert data["team"] == "analysts"

    def test_get_nonexistent_returns_none(self, store: SwarmArtifactStore) -> None:
        """get_artifact возвращает None для несуществующего файла."""
        assert store.get_artifact("ghost_artifact.json") is None

    def test_get_corrupted_returns_none(self, store: SwarmArtifactStore, tmp_path: Path) -> None:
        """get_artifact возвращает None при битом JSON."""
        bad_dir = tmp_path / "bad_artifacts"
        bad_dir.mkdir()
        bad_file = bad_dir / "broken.json"
        bad_file.write_text("not json {{{", encoding="utf-8")
        s = SwarmArtifactStore(base_dir=bad_dir)
        assert s.get_artifact("broken.json") is None


# ------------------------------------------------------------------
# cleanup_old
# ------------------------------------------------------------------


class TestCleanupOld:
    def test_no_cleanup_needed(self, store: SwarmArtifactStore) -> None:
        """Если файлов меньше лимита, cleanup_old возвращает 0."""
        store.save_round_artifact(team="coders", topic="t", result="r")
        removed = store.cleanup_old(max_files=10)
        assert removed == 0

    def test_excess_files_removed(self, tmp_path: Path) -> None:
        """Лишние файлы удаляются, остаётся ровно max_files."""
        base = tmp_path / "excess_arts"
        base.mkdir()
        s = SwarmArtifactStore(base_dir=base)
        base_ts = 1_700_000_000.0
        for i in range(8):
            _write_artifact(base, f"coders_{int(base_ts) + i}.json", "coders", base_ts + i)
        removed = s.cleanup_old(max_files=5)
        assert removed == 3
        remaining = list(base.glob("*.json"))
        assert len(remaining) == 5

    def test_oldest_files_removed(self, tmp_path: Path) -> None:
        """Удаляются самые старые файлы (по mtime)."""
        base = tmp_path / "old_arts"
        base.mkdir()
        s = SwarmArtifactStore(base_dir=base)
        base_ts = 1_700_000_000.0
        paths = []
        for i in range(4):
            p = _write_artifact(base, f"coders_{int(base_ts) + i}.json", "coders", base_ts + i)
            paths.append(p)
        # Оставляем только 2 — должны удалиться первые 2 (старейшие)
        s.cleanup_old(max_files=2)
        assert not paths[0].exists()
        assert not paths[1].exists()
        assert paths[2].exists()
        assert paths[3].exists()


# ------------------------------------------------------------------
# Round-trip: save → list → get
# ------------------------------------------------------------------


class TestRoundTrip:
    def test_save_list_get_roundtrip(self, store: SwarmArtifactStore) -> None:
        """Полный цикл: сохранить, найти в списке, получить по имени."""
        store.save_round_artifact(
            team="creative",
            topic="Story generation",
            result="Once upon a time...",
            delegations=["writer", "editor"],
            verification={"approved": True},
            duration_sec=12.5,
        )
        listed = store.list_artifacts(team="creative")
        assert len(listed) == 1
        filename = Path(listed[0]["_path"]).name
        got = store.get_artifact(filename)
        assert got is not None
        assert got["team"] == "creative"
        assert got["topic"] == "Story generation"
        assert got["delegations"] == ["writer", "editor"]
        assert got["verification"] == {"approved": True}
        assert got["duration_sec"] == 12.5
