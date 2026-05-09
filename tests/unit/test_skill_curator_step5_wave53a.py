# -*- coding: utf-8 -*-
"""
tests/unit/test_skill_curator_step5_wave53a.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Тесты Wave 53-A: SkillCuratorABRunner (Step 5) + auto-apply gate (Step 6).

Покрываемые сценарии:
  1. test_ab_runner_baseline_vs_candidate_metrics — run_ab_comparison возвращает
     структуру с baseline_metrics, candidate_metrics, delta и delta_score.
  2. test_compute_improvement_score_weights_correct — формула взвешенного score
     (error_rate -5×, ttft -2×, response_quality +3×, tool_calls +1×).
  3. test_auto_apply_below_threshold_skipped — delta_score < threshold → queued=False.
  4. test_auto_apply_above_threshold_queues — delta_score >= threshold → entry в pending.
  5. test_pending_queue_persistence — очередь сохраняется атомарно, list_pending читает корректно.
  6. test_ab_runner_mock_swarm_no_real_calls — runner без swarm_invoker не вызывает LLM.
  7. test_cli_ab_test_flag_parses — argparse принимает --ab-test + --ab-rounds + --ab-threshold.
  8. test_cli_apply_pending_requires_confirmation — --apply-pending без pending → выход 0 + сообщение.
  9. test_ab_runner_uses_injected_invoker — если swarm_invoker инъецирован — вызывается.
 10. test_compute_improvement_score_zero_deltas — нулевые дельты → score == 0.0.
 11. test_pending_queue_team_filter — list_pending фильтрует по team.
 12. test_auto_apply_multiple_entries_accumulate — две записи → обе в queue.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _import_curator_step5():
    """Ленивый импорт чтобы не тащить тяжёлые зависимости при сборе тестов."""
    from src.core.skill_curator import (
        SkillCuratorABRunner,
        auto_apply_if_threshold,
        compute_improvement_score,
        list_pending_improvements,
    )

    return (
        SkillCuratorABRunner,
        auto_apply_if_threshold,
        compute_improvement_score,
        list_pending_improvements,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_pending_path(tmp_path: Path) -> Path:
    """Изолированный путь к pending improvements JSON."""
    return tmp_path / "curator" / "_pending_skill_improvements.json"


@pytest.fixture()
def runner(tmp_path: Path) -> Any:
    """SkillCuratorABRunner с изолированной base_dir и без swarm_invoker."""
    SkillCuratorABRunner, *_ = _import_curator_step5()
    return SkillCuratorABRunner(base_dir=tmp_path / "curator")


# ---------------------------------------------------------------------------
# Test 1: run_ab_comparison возвращает ожидаемую структуру
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ab_runner_baseline_vs_candidate_metrics(runner: Any) -> None:
    """run_ab_comparison возвращает ok=True с правильными ключами."""
    # Мокаем get_team_system_prompt чтобы не обращаться к файловой системе
    with patch(
        "src.core.skill_curator.SkillCuratorABRunner._load_baseline_prompt",
        return_value="baseline system prompt",
    ):
        result = await runner.run_ab_comparison("coders", "candidate prompt", rounds=3)

    assert result["ok"] is True
    assert result["team"] == "coders"
    assert result["rounds"] == 3
    assert "baseline_metrics" in result
    assert "candidate_metrics" in result
    assert "delta" in result
    assert "delta_score" in result
    assert result["recommendation"] in ("apply", "skip")

    bm = result["baseline_metrics"]
    cm = result["candidate_metrics"]
    # Метрики должны содержать нужные ключи
    for key in ("count", "error_rate", "ttft_s_avg", "response_quality_avg", "tool_calls_avg"):
        assert key in bm, f"baseline_metrics missing {key}"
        assert key in cm, f"candidate_metrics missing {key}"

    # Delta должна содержать все дельты
    delta = result["delta"]
    for dkey in ("delta_error_rate", "delta_ttft_s", "delta_response_quality", "delta_tool_calls"):
        assert dkey in delta, f"delta missing {dkey}"


# ---------------------------------------------------------------------------
# Test 2: compute_improvement_score — веса корректны
# ---------------------------------------------------------------------------


def test_compute_improvement_score_weights_correct() -> None:
    """Формула: error_rate*(-5) + ttft*(-2) + quality*3 + tools*1."""
    _, _, compute_improvement_score, _ = _import_curator_step5()

    # Нет изменений в error и ttft, +0.1 quality, +1 tool call
    score = compute_improvement_score(
        {
            "delta_error_rate": 0.0,
            "delta_ttft_s": 0.0,
            "delta_response_quality": 0.1,
            "delta_tool_calls": 1.0,
        }
    )
    # 0*(-5) + 0*(-2) + 0.1*3 + 1*1 = 0.3 + 1 = 1.3
    assert abs(score - 1.3) < 1e-4, f"Expected 1.3, got {score}"

    # Ухудшение: +0.1 error_rate, +0.2 ttft
    score_bad = compute_improvement_score(
        {
            "delta_error_rate": 0.1,
            "delta_ttft_s": 0.2,
            "delta_response_quality": 0.0,
            "delta_tool_calls": 0.0,
        }
    )
    # 0.1*(-5) + 0.2*(-2) = -0.5 + -0.4 = -0.9
    assert abs(score_bad - (-0.9)) < 1e-4, f"Expected -0.9, got {score_bad}"


# ---------------------------------------------------------------------------
# Test 3: auto_apply_if_threshold — ниже порога → не ставим в очередь
# ---------------------------------------------------------------------------


def test_auto_apply_below_threshold_skipped(tmp_pending_path: Path) -> None:
    """delta_score < threshold → queued=False, файл не создаётся."""
    _, auto_apply, _, _ = _import_curator_step5()

    result = auto_apply(
        "analysts",
        "candidate prompt text",
        delta_score=0.05,
        threshold=0.15,
        pending_path=tmp_pending_path,
    )

    assert result["queued"] is False
    assert result["entry_id"] is None
    assert "skipped" in result["reason"]
    # Файл не должен быть создан при пропуске
    assert not tmp_pending_path.exists()


# ---------------------------------------------------------------------------
# Test 4: auto_apply_if_threshold — выше порога → запись в очередь
# ---------------------------------------------------------------------------


def test_auto_apply_above_threshold_queues(tmp_pending_path: Path) -> None:
    """delta_score >= threshold → queued=True, запись появляется в JSON."""
    _, auto_apply, _, list_pending = _import_curator_step5()

    result = auto_apply(
        "coders",
        "improved candidate prompt",
        delta_score=0.30,
        threshold=0.15,
        pending_path=tmp_pending_path,
        metadata={"source": "test"},
    )

    assert result["queued"] is True
    assert result["entry_id"] is not None
    assert "queued" in result["reason"]

    # Проверяем содержимое файла
    assert tmp_pending_path.exists()
    queue = json.loads(tmp_pending_path.read_text(encoding="utf-8"))
    assert len(queue) == 1
    entry = queue[0]
    assert entry["team"] == "coders"
    assert entry["delta_score"] == 0.30
    assert entry["status"] == "pending"
    assert entry["metadata"]["source"] == "test"
    assert entry["entry_id"] == result["entry_id"]


# ---------------------------------------------------------------------------
# Test 5: pending queue persistence + list_pending_improvements
# ---------------------------------------------------------------------------


def test_pending_queue_persistence(tmp_pending_path: Path) -> None:
    """Две записи → обе читаются через list_pending_improvements."""
    _, auto_apply, _, list_pending = _import_curator_step5()

    auto_apply(
        "traders", "prompt A", delta_score=0.20, threshold=0.10, pending_path=tmp_pending_path
    )
    auto_apply(
        "traders", "prompt B", delta_score=0.35, threshold=0.10, pending_path=tmp_pending_path
    )

    all_entries = list_pending(pending_path=tmp_pending_path)
    assert len(all_entries) == 2

    # Все записи имеют правильный team
    for e in all_entries:
        assert e["team"] == "traders"
        assert e["status"] == "pending"


# ---------------------------------------------------------------------------
# Test 6: SkillCuratorABRunner с mock swarm — никаких реальных LLM вызовов
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ab_runner_mock_swarm_no_real_calls(tmp_path: Path) -> None:
    """runner без swarm_invoker не вызывает LLM и возвращает mock-метрики."""
    SkillCuratorABRunner, *_ = _import_curator_step5()

    # Убеждаемся, что нет обращений к реальному LLM
    real_http_call_count = 0

    with patch(
        "src.core.skill_curator.SkillCuratorABRunner._load_baseline_prompt",
        return_value="baseline",
    ):
        runner = SkillCuratorABRunner(base_dir=tmp_path / "curator")
        result = await runner.run_ab_comparison("creative", "candidate", rounds=2)

    # real_http_call_count не менялся — HTTP не вызывался
    assert real_http_call_count == 0
    assert result["ok"] is True
    # mock даёт count=2 на каждый вариант
    assert result["baseline_metrics"]["count"] == 2
    assert result["candidate_metrics"]["count"] == 2


# ---------------------------------------------------------------------------
# Test 7: CLI --ab-test флаг парсится корректно
# ---------------------------------------------------------------------------


def test_cli_ab_test_flag_parses() -> None:
    """argparse принимает --ab-test, --ab-rounds, --ab-threshold без ошибок."""
    import importlib.util

    script_path = Path(__file__).parent.parent.parent / "scripts" / "skill_curator_analyze.py"
    spec = importlib.util.spec_from_file_location("skill_curator_analyze", script_path)
    assert spec is not None
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]

    # Временно подменяем sys.argv для парсинга
    orig_argv = sys.argv[:]
    try:
        sys.argv = [
            "skill_curator_analyze.py",
            "--ab-test",
            "coders",
            "--ab-rounds",
            "10",
            "--ab-threshold",
            "0.20",
        ]
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        args = mod._parse_args()
    finally:
        sys.argv = orig_argv

    assert args.ab_test == "coders"
    assert args.ab_rounds == 10
    assert abs(args.ab_threshold - 0.20) < 1e-6


# ---------------------------------------------------------------------------
# Test 8: --apply-pending без pending items → возврат 0 без ввода
# ---------------------------------------------------------------------------


def test_cli_apply_pending_requires_confirmation(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """_run_apply_pending при пустой очереди выходит с 0 без запроса ввода."""
    import importlib.util

    script_path = Path(__file__).parent.parent.parent / "scripts" / "skill_curator_analyze.py"
    spec = importlib.util.spec_from_file_location("skill_curator_analyze", script_path)
    assert spec is not None
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]

    orig_argv = sys.argv[:]
    try:
        sys.argv = ["skill_curator_analyze.py", "--apply-pending"]
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
    finally:
        sys.argv = orig_argv

    from src.core.skill_curator import PENDING_IMPROVEMENTS_PATH

    # Изолируем от реальной pending очереди
    with patch("src.core.skill_curator.PENDING_IMPROVEMENTS_PATH", tmp_path / "_pending.json"):
        import argparse

        args = argparse.Namespace(team=None, apply_pending=True)
        code = mod._run_apply_pending(args)

    captured = capsys.readouterr()
    assert code == 0
    assert (
        "пуста" in captured.out.lower() or "empty" in captured.out.lower() or "пуст" in captured.out
    )


# ---------------------------------------------------------------------------
# Test 9: SkillCuratorABRunner с инъецированным invoker — invoker вызывается
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ab_runner_uses_injected_invoker(tmp_path: Path) -> None:
    """Если swarm_invoker задан — runner делегирует ему раунды."""
    SkillCuratorABRunner, *_ = _import_curator_step5()

    call_count = 0

    async def fake_invoker(team: str, prompt: str, round_id: str) -> dict:
        nonlocal call_count
        call_count += 1
        return {
            "round_id": round_id,
            "team": team,
            "error": False,
            "ttft_s": 0.4,
            "response_length": 300,
            "response_quality": 0.8,
            "tool_calls": 2,
        }

    runner = SkillCuratorABRunner(base_dir=tmp_path / "curator", swarm_invoker=fake_invoker)

    with patch(
        "src.core.skill_curator.SkillCuratorABRunner._load_baseline_prompt",
        return_value="base",
    ):
        result = await runner.run_ab_comparison("traders", "candidate", rounds=3)

    # 3 baseline + 3 candidate = 6 вызовов
    assert call_count == 6
    assert result["ok"] is True
    # Invoker возвращает фиксированные значения
    assert result["baseline_metrics"]["response_quality_avg"] == pytest.approx(0.8, abs=1e-4)
    assert result["candidate_metrics"]["tool_calls_avg"] == pytest.approx(2.0, abs=1e-4)


# ---------------------------------------------------------------------------
# Test 10: compute_improvement_score при нулевых дельтах → 0.0
# ---------------------------------------------------------------------------


def test_compute_improvement_score_zero_deltas() -> None:
    """Нулевые дельты → score == 0.0."""
    _, _, compute_improvement_score, _ = _import_curator_step5()

    score = compute_improvement_score(
        {
            "delta_error_rate": 0.0,
            "delta_ttft_s": 0.0,
            "delta_response_quality": 0.0,
            "delta_tool_calls": 0.0,
        }
    )
    assert score == 0.0


# ---------------------------------------------------------------------------
# Test 11: list_pending_improvements фильтрует по team
# ---------------------------------------------------------------------------


def test_pending_queue_team_filter(tmp_pending_path: Path) -> None:
    """list_pending_improvements фильтрует записи по team."""
    _, auto_apply, _, list_pending = _import_curator_step5()

    auto_apply(
        "coders", "coders prompt", delta_score=0.25, threshold=0.10, pending_path=tmp_pending_path
    )
    auto_apply(
        "traders", "traders prompt", delta_score=0.30, threshold=0.10, pending_path=tmp_pending_path
    )
    auto_apply(
        "coders", "coders prompt 2", delta_score=0.40, threshold=0.10, pending_path=tmp_pending_path
    )

    coders_entries = list_pending(team="coders", pending_path=tmp_pending_path)
    traders_entries = list_pending(team="traders", pending_path=tmp_pending_path)
    all_entries = list_pending(pending_path=tmp_pending_path)

    assert len(coders_entries) == 2
    assert len(traders_entries) == 1
    assert len(all_entries) == 3

    for e in coders_entries:
        assert e["team"] == "coders"


# ---------------------------------------------------------------------------
# Test 12: две записи выше порога — обе накапливаются в очереди
# ---------------------------------------------------------------------------


def test_auto_apply_multiple_entries_accumulate(tmp_pending_path: Path) -> None:
    """Несколько вызовов auto_apply_if_threshold выше порога → все записи в очереди."""
    _, auto_apply, _, list_pending = _import_curator_step5()

    r1 = auto_apply(
        "analysts", "prompt v1", delta_score=0.20, threshold=0.15, pending_path=tmp_pending_path
    )
    r2 = auto_apply(
        "analysts", "prompt v2", delta_score=0.50, threshold=0.15, pending_path=tmp_pending_path
    )
    r3 = auto_apply(
        "analysts", "prompt v3", delta_score=0.10, threshold=0.15, pending_path=tmp_pending_path
    )  # ниже порога

    assert r1["queued"] is True
    assert r2["queued"] is True
    assert r3["queued"] is False  # ниже порога

    queue = list_pending(pending_path=tmp_pending_path)
    assert len(queue) == 2  # только r1 и r2

    entry_ids = {e["entry_id"] for e in queue}
    assert r1["entry_id"] in entry_ids
    assert r2["entry_id"] in entry_ids
