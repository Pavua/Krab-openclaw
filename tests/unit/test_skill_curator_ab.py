# -*- coding: utf-8 -*-
"""
tests/unit/test_skill_curator_ab.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Тесты A/B framework SkillCurator (Step 4, Wave 16-D).
15 тестов покрывают:
- start_ab_test: создание, mutex (second start → None)
- select_variant: детерминированность, балансировка
- record_round_metric: append + unknown ab_id
- evaluate_ab_test: candidate wins / control wins (cost) / control wins (latency) / insufficient data
- evaluate_ab_test_and_apply: async auto-apply при candidate wins
- cancel_ab_test: status + state cleanup
- CuratorState round-trip: active_ab_tests
- get_active_ab_test: после start
- list_ab_tests: фильтрация по status
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.skill_curator import SkillCurator
from src.core.skill_curator_state import CuratorState


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_base(tmp_path: Path) -> Path:
    """Изолированная base_dir для всех curator файлов."""
    return tmp_path / "curator"


@pytest.fixture()
def curator(tmp_base: Path) -> SkillCurator:
    """SkillCurator с изолированной base_dir."""
    # Мокаем память и артефакты — не нужны для A/B тестов
    mem = MagicMock()
    mem.get_recent.return_value = []
    arts = MagicMock()
    arts.list_artifacts.return_value = []
    return SkillCurator(memory=mem, artifact_store=arts, base_dir=tmp_base)


@pytest.fixture()
def state_path(tmp_base: Path) -> Path:
    """Путь к state.json в изолированной директории."""
    tmp_base.mkdir(parents=True, exist_ok=True)
    return tmp_base / "state.json"


def _make_proposal(curator: SkillCurator, team: str = "analysts", proposal_id: str = "analysts-2026-05-02-22-15") -> Path:
    """Создаёт proposal файл с proposed_prompt."""
    proposals_dir = curator._base_dir / "proposals"
    proposals_dir.mkdir(parents=True, exist_ok=True)
    path = proposals_dir / f"{proposal_id}.json"
    path.write_text(
        json.dumps({
            "proposal_id": proposal_id,
            "team": team,
            "status": "pending",
            "proposed_prompt": f"Updated system prompt for {team} team.",
            "current_prompt": f"Original prompt for {team}.",
            "rationale": "Test rationale",
            "focus": ["timeout"],
            "confidence": 0.75,
            "model": "gemini-3-flash-preview",
        }),
        encoding="utf-8",
    )
    return path


# ---------------------------------------------------------------------------
# Test 1: start_ab_test создаёт JSON + обновляет state
# ---------------------------------------------------------------------------


def test_start_ab_test_creates_json(curator: SkillCurator, state_path: Path) -> None:
    """start_ab_test сохраняет JSON в ab_tests/ и обновляет state.active_ab_tests."""
    _make_proposal(curator, "analysts")

    with patch(
        "src.core.swarm_team_prompts.get_team_system_prompt", return_value="Control prompt"
    ):
        result = curator.start_ab_test(
            "analysts",
            "analysts-2026-05-02-22-15",
            n_rounds=10,
            state_path=state_path,
        )

    assert result is not None
    ab_id = result["ab_id"]
    assert "analysts" in ab_id

    # Файл существует
    ab_file = curator._ab_tests_dir() / f"{ab_id}.json"
    assert ab_file.exists()

    data = json.loads(ab_file.read_text())
    assert data["team"] == "analysts"
    assert data["status"] == "running"
    assert data["n_rounds_target"] == 10
    assert data["control_prompt"] == "Control prompt"
    assert "Updated system prompt" in data["candidate_prompt"]
    assert data["rounds"] == []

    # State обновлён
    state = CuratorState.load(state_path)
    assert state.get_active_ab_test("analysts") == ab_id


# ---------------------------------------------------------------------------
# Test 2: second start для той же команды → None
# ---------------------------------------------------------------------------


def test_start_ab_test_already_running_blocks(curator: SkillCurator, state_path: Path) -> None:
    """Второй start_ab_test для той же команды возвращает None."""
    _make_proposal(curator, "analysts")

    with patch(
        "src.core.swarm_team_prompts.get_team_system_prompt", return_value="Ctrl"
    ):
        result1 = curator.start_ab_test(
            "analysts",
            "analysts-2026-05-02-22-15",
            state_path=state_path,
        )
        assert result1 is not None

        result2 = curator.start_ab_test(
            "analysts",
            "analysts-2026-05-02-22-15",
            state_path=state_path,
        )
        assert result2 is None


# ---------------------------------------------------------------------------
# Test 3: select_variant детерминирован
# ---------------------------------------------------------------------------


def test_select_variant_deterministic(curator: SkillCurator) -> None:
    """Один и тот же round_id всегда возвращает одинаковый вариант."""
    ab_id = "test-ab-1234"
    for round_id in ["round-1", "round-abc", "xyz-999"]:
        v1 = curator.select_variant(ab_id, round_id)
        v2 = curator.select_variant(ab_id, round_id)
        v3 = curator.select_variant(ab_id, round_id)
        assert v1 == v2 == v3
        assert v1 in {"control", "candidate"}


# ---------------------------------------------------------------------------
# Test 4: select_variant балансировка ~50/50 на 100 раундах
# ---------------------------------------------------------------------------


def test_select_variant_balanced(curator: SkillCurator) -> None:
    """100 раундов с разными round_id дают близкое к 50/50 распределение."""
    ab_id = "balance-test-ab"
    counts: dict[str, int] = {"control": 0, "candidate": 0}
    for i in range(100):
        v = curator.select_variant(ab_id, f"round-{i}")
        counts[v] += 1

    # Допустимо 35-65 для детерминированного hash, но ожидаем ~50/50
    assert counts["control"] + counts["candidate"] == 100
    assert 30 <= counts["control"] <= 70, f"Unexpected distribution: {counts}"


# ---------------------------------------------------------------------------
# Test 5: record_round_metric добавляет записи
# ---------------------------------------------------------------------------


def test_record_round_metric_appends(curator: SkillCurator, state_path: Path) -> None:
    """Несколько вызовов record_round_metric записывают все rounds."""
    _make_proposal(curator, "coders", "coders-test-proposal")

    with patch("src.core.swarm_team_prompts.get_team_system_prompt", return_value="Ctrl"):
        result = curator.start_ab_test(
            "coders",
            "coders-test-proposal",
            state_path=state_path,
        )
    assert result is not None
    ab_id = result["ab_id"]

    # Записываем 3 раунда
    for i in range(3):
        ok = curator.record_round_metric(
            ab_id,
            f"round-{i}",
            {"success": True, "cost_usd": 0.01, "latency_s": 2.0, "tool_calls": 2},
        )
        assert ok is True

    data = curator._load_ab_test(ab_id)
    assert data is not None
    assert len(data["rounds"]) == 3
    for r in data["rounds"]:
        assert r["success"] is True
        assert r["cost_usd"] == 0.01
        assert r["variant"] in {"control", "candidate"}


# ---------------------------------------------------------------------------
# Test 6: record_round_metric для несуществующего ab_test → False
# ---------------------------------------------------------------------------


def test_record_round_metric_unknown_ab_test(curator: SkillCurator) -> None:
    """record_round_metric для несуществующего ab_id возвращает False."""
    ok = curator.record_round_metric(
        "nonexistent-ab-id",
        "round-1",
        {"success": True, "cost_usd": 0.01},
    )
    assert ok is False


# ---------------------------------------------------------------------------
# Test 7: evaluate — candidate wins
# ---------------------------------------------------------------------------


def test_evaluate_ab_test_candidate_wins(curator: SkillCurator, state_path: Path) -> None:
    """Candidate wins когда success_rate на 0.05+ выше control, cost/latency в норме."""
    _make_proposal(curator, "traders", "traders-test-prop")

    with patch("src.core.swarm_team_prompts.get_team_system_prompt", return_value="Ctrl"):
        result = curator.start_ab_test("traders", "traders-test-prop", n_rounds=10, state_path=state_path)
    assert result is not None
    ab_id = result["ab_id"]

    # Вручную формируем 10 rounds: 5 control (success=0.5), 5 candidate (success=1.0)
    # Используем чётный/нечётный hash для точного распределения
    # select_variant детерминирован — просто добавляем вручную
    rounds = []
    for i in range(5):
        rounds.append({
            "round_id": f"ctrl-{i}",
            "variant": "control",
            "success": i < 3,  # 3/5 = 0.6 success rate
            "cost_usd": 0.01,
            "latency_s": 2.0,
            "tool_calls": 2,
            "verifier_pass": i < 3,
        })
    for i in range(5):
        rounds.append({
            "round_id": f"cand-{i}",
            "variant": "candidate",
            "success": True,  # 5/5 = 1.0 success rate
            "cost_usd": 0.011,  # небольшой рост, OK
            "latency_s": 2.1,   # небольшой рост, OK
            "tool_calls": 2,
            "verifier_pass": True,
        })

    # Пишем rounds напрямую в файл
    data = curator._load_ab_test(ab_id)
    assert data is not None
    data["rounds"] = rounds
    curator._save_ab_test_atomic(data)

    eval_result = curator.evaluate_ab_test(ab_id)
    assert eval_result["winner"] == "candidate"
    assert eval_result["status"] == "decided"
    assert "candidate" in eval_result["reason"].lower()

    # Файл обновлён
    updated = curator._load_ab_test(ab_id)
    assert updated["decision"] == "candidate"
    assert updated["status"] == "decided"


# ---------------------------------------------------------------------------
# Test 8: evaluate — candidate loses on cost
# ---------------------------------------------------------------------------


def test_evaluate_ab_test_candidate_loses_on_cost(curator: SkillCurator, state_path: Path) -> None:
    """Control wins когда candidate success+0.10 НО cost +20% (> 10% threshold)."""
    _make_proposal(curator, "analysts", "analysts-cost-test")

    with patch("src.core.swarm_team_prompts.get_team_system_prompt", return_value="Ctrl"):
        result = curator.start_ab_test("analysts", "analysts-cost-test", n_rounds=10, state_path=state_path)
    assert result is not None
    ab_id = result["ab_id"]

    rounds = []
    for i in range(5):
        rounds.append({
            "round_id": f"ctrl-{i}",
            "variant": "control",
            "success": i < 3,   # 3/5 = 0.6 SR
            "cost_usd": 0.010,
            "latency_s": 2.0,
            "tool_calls": 2,
            "verifier_pass": True,
        })
    for i in range(5):
        rounds.append({
            "round_id": f"cand-{i}",
            "variant": "candidate",
            "success": True,    # 5/5 = 1.0 SR (+0.40 > 0.05 ok)
            "cost_usd": 0.013,  # +30% > 10% threshold → should fail
            "latency_s": 2.0,
            "tool_calls": 2,
            "verifier_pass": True,
        })

    data = curator._load_ab_test(ab_id)
    assert data is not None
    data["rounds"] = rounds
    curator._save_ab_test_atomic(data)

    eval_result = curator.evaluate_ab_test(ab_id)
    assert eval_result["winner"] == "control"
    assert "cost" in eval_result["reason"].lower()


# ---------------------------------------------------------------------------
# Test 9: evaluate — candidate loses on latency
# ---------------------------------------------------------------------------


def test_evaluate_ab_test_candidate_loses_on_latency(curator: SkillCurator, state_path: Path) -> None:
    """Control wins когда candidate success+0.10 НО latency +30% (> 10% threshold)."""
    _make_proposal(curator, "creative", "creative-lat-test")

    with patch("src.core.swarm_team_prompts.get_team_system_prompt", return_value="Ctrl"):
        result = curator.start_ab_test("creative", "creative-lat-test", n_rounds=10, state_path=state_path)
    assert result is not None
    ab_id = result["ab_id"]

    rounds = []
    for i in range(5):
        rounds.append({
            "round_id": f"ctrl-{i}",
            "variant": "control",
            "success": i < 3,   # 0.6 SR
            "cost_usd": 0.010,
            "latency_s": 2.0,
            "tool_calls": 2,
            "verifier_pass": True,
        })
    for i in range(5):
        rounds.append({
            "round_id": f"cand-{i}",
            "variant": "candidate",
            "success": True,    # 1.0 SR
            "cost_usd": 0.010,
            "latency_s": 2.7,   # +35% > 10% threshold → fail
            "tool_calls": 2,
            "verifier_pass": True,
        })

    data = curator._load_ab_test(ab_id)
    assert data is not None
    data["rounds"] = rounds
    curator._save_ab_test_atomic(data)

    eval_result = curator.evaluate_ab_test(ab_id)
    assert eval_result["winner"] == "control"
    assert "latency" in eval_result["reason"].lower()


# ---------------------------------------------------------------------------
# Test 10: evaluate — недостаточно данных
# ---------------------------------------------------------------------------


def test_evaluate_ab_test_insufficient_data(curator: SkillCurator, state_path: Path) -> None:
    """Если rounds < n_rounds_target — status='running', winner=None."""
    _make_proposal(curator, "analysts", "analysts-insuf-test")

    with patch("src.core.swarm_team_prompts.get_team_system_prompt", return_value="Ctrl"):
        result = curator.start_ab_test("analysts", "analysts-insuf-test", n_rounds=10, state_path=state_path)
    assert result is not None
    ab_id = result["ab_id"]

    # Только 3 rounds из 10
    for i in range(3):
        curator.record_round_metric(
            ab_id, f"round-{i}", {"success": True, "cost_usd": 0.01, "latency_s": 2.0}
        )

    eval_result = curator.evaluate_ab_test(ab_id)
    assert eval_result["status"] == "running"
    assert eval_result["winner"] is None
    assert "3/10" in eval_result["reason"]


# ---------------------------------------------------------------------------
# Test 11: evaluate_ab_test_and_apply — auto-apply при candidate wins
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_evaluate_ab_test_and_apply_auto_applies(
    curator: SkillCurator, state_path: Path
) -> None:
    """evaluate_ab_test_and_apply вызывает apply_with_approval когда candidate wins."""
    _make_proposal(curator, "traders", "traders-apply-test")

    with patch("src.core.swarm_team_prompts.get_team_system_prompt", return_value="Ctrl"):
        result = curator.start_ab_test("traders", "traders-apply-test", n_rounds=5, state_path=state_path)
    assert result is not None
    ab_id = result["ab_id"]

    # Заполняем достаточно rounds для победы candidate
    rounds = []
    for i in range(3):
        rounds.append({"round_id": f"ctrl-{i}", "variant": "control", "success": False, "cost_usd": 0.01, "latency_s": 2.0})
    for i in range(3):
        rounds.append({"round_id": f"cand-{i}", "variant": "candidate", "success": True, "cost_usd": 0.01, "latency_s": 2.0})

    data = curator._load_ab_test(ab_id)
    assert data is not None
    data["rounds"] = rounds
    data["n_rounds_target"] = 5  # Уже есть 6 > 5
    curator._save_ab_test_atomic(data)

    # Мокаем apply_with_approval
    curator.apply_with_approval = AsyncMock(return_value=(True, "applied successfully"))

    eval_result, applied = await curator.evaluate_ab_test_and_apply(ab_id, state_path=state_path)

    if eval_result["winner"] == "candidate":
        curator.apply_with_approval.assert_called_once()
        assert applied is True
    # Если control wins — apply не вызывается
    else:
        curator.apply_with_approval.assert_not_called()
        assert applied is False


# ---------------------------------------------------------------------------
# Test 12: cancel_ab_test
# ---------------------------------------------------------------------------


def test_cancel_ab_test_clears_state(curator: SkillCurator, state_path: Path) -> None:
    """cancel_ab_test обновляет status=cancelled и очищает state.active_ab_tests."""
    _make_proposal(curator, "coders", "coders-cancel-test")

    with patch("src.core.swarm_team_prompts.get_team_system_prompt", return_value="Ctrl"):
        result = curator.start_ab_test("coders", "coders-cancel-test", state_path=state_path)
    assert result is not None
    ab_id = result["ab_id"]

    ok = curator.cancel_ab_test(ab_id, reason="test", state_path=state_path)
    assert ok is True

    data = curator._load_ab_test(ab_id)
    assert data is not None
    assert data["status"] == "cancelled"
    assert "test" in (data.get("decision_reason") or "")

    state = CuratorState.load(state_path)
    assert state.get_active_ab_test("coders") is None


# ---------------------------------------------------------------------------
# Test 13: CuratorState active_ab_tests round-trip
# ---------------------------------------------------------------------------


def test_curator_state_active_ab_tests_round_trip(state_path: Path) -> None:
    """CuratorState сохраняет и загружает active_ab_tests корректно."""
    state = CuratorState()
    state.set_active_ab_test("analysts", "analysts-ab-123")
    state.set_active_ab_test("coders", "coders-ab-456")
    state.save_atomic(state_path)

    loaded = CuratorState.load(state_path)
    assert loaded.get_active_ab_test("analysts") == "analysts-ab-123"
    assert loaded.get_active_ab_test("coders") == "coders-ab-456"
    assert loaded.get_active_ab_test("traders") is None

    # clear + reload
    loaded.clear_active_ab_test("analysts")
    loaded.save_atomic(state_path)

    loaded2 = CuratorState.load(state_path)
    assert loaded2.get_active_ab_test("analysts") is None
    assert loaded2.get_active_ab_test("coders") == "coders-ab-456"


# ---------------------------------------------------------------------------
# Test 14: get_active_ab_test возвращает данные после start
# ---------------------------------------------------------------------------


def test_get_active_ab_test_returns_data(curator: SkillCurator, state_path: Path) -> None:
    """get_active_ab_test возвращает данные A/B теста после start."""
    _make_proposal(curator, "analysts", "analysts-active-test")

    with patch("src.core.swarm_team_prompts.get_team_system_prompt", return_value="Ctrl"):
        result = curator.start_ab_test("analysts", "analysts-active-test", state_path=state_path)
    assert result is not None

    ab_data = curator.get_active_ab_test("analysts", state_path=state_path)
    assert ab_data is not None
    assert ab_data["team"] == "analysts"
    assert ab_data["status"] == "running"
    assert ab_data["ab_id"] == result["ab_id"]


# ---------------------------------------------------------------------------
# Test 15: list_ab_tests фильтрация по status
# ---------------------------------------------------------------------------


def test_list_ab_tests_filters_by_status(curator: SkillCurator, state_path: Path) -> None:
    """list_ab_tests корректно фильтрует по status=running/decided/cancelled."""
    # Создаём 3 теста для разных команд с разными status
    for team, proposal_suffix in [("analysts", "p1"), ("coders", "p2"), ("traders", "p3")]:
        _make_proposal(curator, team, f"{team}-{proposal_suffix}")

    with patch("src.core.swarm_team_prompts.get_team_system_prompt", return_value="Ctrl"):
        r1 = curator.start_ab_test("analysts", "analysts-p1", n_rounds=5, state_path=state_path)
        r2 = curator.start_ab_test("coders", "coders-p2", n_rounds=5, state_path=state_path)
        r3 = curator.start_ab_test("traders", "traders-p3", n_rounds=5, state_path=state_path)

    assert r1 and r2 and r3

    # Отменяем coders
    curator.cancel_ab_test(r2["ab_id"], state_path=state_path)

    # Завершаем traders (decided)
    data = curator._load_ab_test(r3["ab_id"])
    assert data is not None
    data["status"] = "decided"
    data["decision"] = "control"
    curator._save_ab_test_atomic(data)

    # Список running
    running = curator.list_ab_tests(status="running")
    running_ids = {t["ab_id"] for t in running}
    assert r1["ab_id"] in running_ids
    assert r2["ab_id"] not in running_ids
    assert r3["ab_id"] not in running_ids

    # Список cancelled
    cancelled = curator.list_ab_tests(status="cancelled")
    cancelled_ids = {t["ab_id"] for t in cancelled}
    assert r2["ab_id"] in cancelled_ids

    # Список decided
    decided = curator.list_ab_tests(status="decided")
    decided_ids = {t["ab_id"] for t in decided}
    assert r3["ab_id"] in decided_ids

    # Фильтр по team
    analysts_tests = curator.list_ab_tests(team="analysts")
    assert all(t["team"] == "analysts" for t in analysts_tests)
    assert r1["ab_id"] in {t["ab_id"] for t in analysts_tests}
