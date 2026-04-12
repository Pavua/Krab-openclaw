# -*- coding: utf-8 -*-
"""
Расширенные тесты для SwarmArtifactStore.
Покрывает: save/list/cleanup артефактов, save_report, фильтрация по команде,
лимиты, метаданные, обрезка длинных полей, get_artifact.
"""

import itertools
import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from src.core.swarm_artifact_store import SwarmArtifactStore

# Счётчик для генерации уникальных timestamp в тестах
_ts_counter = itertools.count(1_700_000_000)


def _unique_ts():
    """Возвращает уникальный монотонный timestamp."""
    return next(_ts_counter)


@pytest.fixture
def store(tmp_path):
    """Хранилище с временной директорией — изолировано от реального ~/.openclaw."""
    return SwarmArtifactStore(base_dir=tmp_path / "artifacts")


def save_artifact(store, *, team, topic, result, **kwargs):
    """Сохраняет артефакт с гарантированно уникальным именем файла."""
    with patch("src.core.swarm_artifact_store.time") as m:
        m.time.return_value = _unique_ts()
        m.strftime = time.strftime
        m.gmtime = time.gmtime
        return store.save_round_artifact(team=team, topic=topic, result=result, **kwargs)


# --- save_round_artifact ---


def test_save_creates_file(store):
    """Сохранение артефакта создаёт JSON-файл."""
    path = save_artifact(store, team="coders", topic="test topic", result="ok")
    assert path.exists()


def test_save_returns_path_with_team_prefix(store):
    """Имя файла содержит имя команды."""
    path = save_artifact(store, team="traders", topic="btc", result="up")
    assert path.name.startswith("traders_")


def test_save_artifact_content(store):
    """Содержимое сохранённого JSON корректно."""
    path = save_artifact(
        store,
        team="analysts",
        topic="market analysis",
        result="bullish",
        delegations=["role_a", "role_b"],
        duration_sec=3.14,
    )
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["team"] == "analysts"
    assert data["topic"] == "market analysis"
    assert data["result"] == "bullish"
    assert data["delegations"] == ["role_a", "role_b"]
    assert data["duration_sec"] == 3.14
    assert "timestamp" in data
    assert "timestamp_iso" in data


def test_save_truncates_long_result(store):
    """Результат длиннее 5000 символов обрезается."""
    long_result = "x" * 10_000
    path = save_artifact(store, team="creative", topic="t", result=long_result)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert len(data["result"]) == 5000


def test_save_truncates_long_topic(store):
    """Топик длиннее 200 символов обрезается."""
    long_topic = "t" * 500
    path = save_artifact(store, team="coders", topic=long_topic, result="r")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert len(data["topic"]) == 200


def test_save_with_verification(store):
    """Поле verification сохраняется корректно."""
    verification = {"passed": True, "score": 0.95, "notes": "все хорошо"}
    path = save_artifact(
        store, team="analysts", topic="check", result="done", verification=verification
    )
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["verification"] == verification


def test_save_team_name_sanitized(store):
    """Слэши в имени команды заменяются на подчёркивания."""
    path = save_artifact(store, team="a/b/c", topic="t", result="r")
    assert "/" not in path.name


# --- list_artifacts ---


def test_list_returns_all_artifacts(store):
    """list_artifacts возвращает все сохранённые артефакты."""
    save_artifact(store, team="coders", topic="t1", result="r1")
    save_artifact(store, team="traders", topic="t2", result="r2")
    artifacts = store.list_artifacts()
    assert len(artifacts) == 2


def test_list_filters_by_team(store):
    """Фильтрация по команде возвращает только нужные артефакты."""
    save_artifact(store, team="coders", topic="t1", result="r1")
    save_artifact(store, team="traders", topic="t2", result="r2")
    save_artifact(store, team="coders", topic="t3", result="r3")
    result = store.list_artifacts(team="coders")
    assert len(result) == 2
    assert all(a["team"] == "coders" for a in result)


def test_list_respects_limit(store):
    """Параметр limit ограничивает количество результатов."""
    for i in range(10):
        save_artifact(store, team="analysts", topic=f"t{i}", result=f"r{i}")
    result = store.list_artifacts(limit=3)
    assert len(result) == 3


def test_list_contains_path_key(store):
    """Каждый элемент списка содержит ключ _path."""
    save_artifact(store, team="coders", topic="t", result="r")
    artifacts = store.list_artifacts()
    assert "_path" in artifacts[0]


# --- get_artifact ---


def test_get_artifact_by_filename(store):
    """get_artifact читает артефакт по имени файла."""
    path = save_artifact(store, team="coders", topic="read test", result="found")
    data = store.get_artifact(path.name)
    assert data is not None
    assert data["result"] == "found"


def test_get_artifact_missing_returns_none(store):
    """get_artifact возвращает None для несуществующего файла."""
    assert store.get_artifact("nonexistent_99999.json") is None


# --- cleanup_old ---


def test_cleanup_removes_excess_files(store):
    """cleanup_old удаляет файлы сверх max_files."""
    for i in range(15):
        save_artifact(store, team="coders", topic=f"t{i}", result=f"r{i}")
    removed = store.cleanup_old(max_files=10)
    assert removed == 5
    remaining = list(store._base_dir.glob("*.json"))
    assert len(remaining) == 10


def test_cleanup_no_excess(store):
    """cleanup_old не удаляет ничего если файлов меньше лимита."""
    save_artifact(store, team="coders", topic="t", result="r")
    removed = store.cleanup_old(max_files=50)
    assert removed == 0


# --- save_report ---


def test_save_report_creates_markdown(store, tmp_path):
    """save_report создаёт .md файл с нужным содержимым."""
    report_dir = tmp_path / "reports"
    path = store.save_report(
        team="analysts",
        topic="weekly summary",
        result="everything is fine",
        report_dir=report_dir,
    )
    assert path.exists()
    assert path.suffix == ".md"
    content = path.read_text(encoding="utf-8")
    assert "weekly summary" in content
    assert "analysts" in content
    assert "everything is fine" in content


def test_save_report_filename_contains_team_and_topic(store, tmp_path):
    """Имя файла отчёта содержит имя команды и топик."""
    report_dir = tmp_path / "reports2"
    path = store.save_report(
        team="traders",
        topic="crypto report",
        result="data",
        report_dir=report_dir,
    )
    assert "traders" in path.name
    assert "crypto" in path.name
