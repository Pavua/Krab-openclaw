# -*- coding: utf-8 -*-
"""Unit-тесты для src/core/prompt_ab_testing.ABTester."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from src.core.prompt_ab_testing import ABTester


def _make_tester(tmp_path: Path) -> ABTester:
    return ABTester(storage_path=tmp_path / "ab_experiments.json")


def test_register_and_pick_variant(tmp_path: Path) -> None:
    """Регистрация эксперимента и выбор варианта возвращают prompt-text."""
    tester = _make_tester(tmp_path)
    tester.register_experiment(
        "tone",
        variants={"A": "polite prompt", "B": "crisp prompt"},
        traffic_split={"A": 0.5, "B": 0.5},
    )
    variant, text = tester.pick_variant("tone", user_id="u1")
    assert variant in {"A", "B"}
    assert text in {"polite prompt", "crisp prompt"}
    # Перерегистрация с новыми вариантами не должна падать.
    tester.register_experiment("tone", variants={"A": "v1", "C": "v3"})
    assert sorted(tester.list_experiments()) == ["tone"]


def test_sticky_assignment_is_deterministic(tmp_path: Path) -> None:
    """Один user_id → стабильный variant между вызовами."""
    tester = _make_tester(tmp_path)
    tester.register_experiment(
        "sticky",
        variants={"A": "a", "B": "b"},
        traffic_split={"A": 0.5, "B": 0.5},
    )
    first, _ = tester.pick_variant("sticky", user_id=12345)
    for _ in range(20):
        again, _ = tester.pick_variant("sticky", user_id=12345)
        assert again == first


def test_traffic_split_converges_to_ratio(tmp_path: Path) -> None:
    """Распределение по большой выборке user_id ≈ заданному split."""
    tester = _make_tester(tmp_path)
    tester.register_experiment(
        "split",
        variants={"A": "a", "B": "b"},
        traffic_split={"A": 0.7, "B": 0.3},
    )
    counts: Counter[str] = Counter()
    sample_size = 5000
    for i in range(sample_size):
        variant, _ = tester.pick_variant("split", user_id=i)
        counts[variant] += 1
    share_a = counts["A"] / sample_size
    # 5000 испытаний → стандартное отклонение ~0.0065 для p=0.7.
    # Ослабляем порог до 0.03 чтобы тест не флапал на новых hash-функциях.
    assert abs(share_a - 0.7) < 0.03, f"share_a={share_a}, counts={counts}"


def test_record_outcome_and_persist(tmp_path: Path) -> None:
    """record_outcome обновляет агрегаты и сразу persist'ит на диск."""
    storage = tmp_path / "ab_experiments.json"
    tester = ABTester(storage_path=storage)
    tester.register_experiment("recall", variants={"A": "a", "B": "b"})
    tester.record_outcome("recall", "A", user_id=1, success=True, score=0.9)
    tester.record_outcome("recall", "A", user_id=2, success=False, score=0.4)
    tester.record_outcome("recall", "B", user_id=3, success=True, score=0.6)

    raw = json.loads(storage.read_text(encoding="utf-8"))
    assert raw["recall"]["outcomes"]["A"]["count"] == 2
    assert raw["recall"]["outcomes"]["A"]["success"] == 1
    assert raw["recall"]["outcomes"]["B"]["count"] == 1

    # Неизвестный variant → KeyError, неизвестный experiment → KeyError.
    with pytest.raises(KeyError):
        tester.record_outcome("recall", "Z", user_id=1, success=True, score=0.0)
    with pytest.raises(KeyError):
        tester.record_outcome("missing", "A", user_id=1, success=True, score=0.0)


def test_get_stats_computes_mean_and_ci(tmp_path: Path) -> None:
    """Stats: sample_size, mean, stddev и 95% CI вычисляются ожидаемо."""
    tester = _make_tester(tmp_path)
    tester.register_experiment("stats", variants={"A": "a", "B": "b"})
    # Variant A: scores [1.0, 0.5, 0.0] → mean 0.5, stddev sqrt(1/6/3*?)
    for i, sc in enumerate([1.0, 0.5, 0.0]):
        tester.record_outcome("stats", "A", user_id=i, success=sc > 0.4, score=sc)
    # Variant B: один observed → CI = None (нужно ≥2).
    tester.record_outcome("stats", "B", user_id=99, success=True, score=0.7)

    snapshot = tester.get_stats("stats")
    assert snapshot.experiment == "stats"
    assert snapshot.total_samples == 4
    by_name = {v.variant: v for v in snapshot.variants}
    assert by_name["A"].sample_size == 3
    assert by_name["A"].success_count == 2
    assert abs(by_name["A"].mean_score - 0.5) < 1e-9
    assert by_name["A"].stddev_score > 0
    assert by_name["A"].confidence_interval_95 is not None
    low, high = by_name["A"].confidence_interval_95
    assert low < by_name["A"].mean_score < high
    # Variant B — single sample → CI отсутствует.
    assert by_name["B"].sample_size == 1
    assert by_name["B"].confidence_interval_95 is None


def test_multi_experiment_isolation(tmp_path: Path) -> None:
    """Разные эксперименты не делят outcomes и variant-переключения."""
    tester = _make_tester(tmp_path)
    tester.register_experiment("exp1", variants={"A": "a1", "B": "b1"})
    tester.register_experiment("exp2", variants={"A": "a2", "B": "b2"})
    tester.record_outcome("exp1", "A", user_id=1, success=True, score=1.0)
    tester.record_outcome("exp1", "A", user_id=2, success=True, score=1.0)
    # exp2 пуст.
    s1 = tester.get_stats("exp1")
    s2 = tester.get_stats("exp2")
    assert s1.total_samples == 2
    assert s2.total_samples == 0
    # Sticky assignment не должен «протекать»: одинаковый user_id может
    # попасть в разные varianты в разных экспериментах. Конкретные значения
    # зависят от hash, проверяем хотя бы что вызовы не падают и возвращают
    # известные variant'ы.
    v1, _ = tester.pick_variant("exp1", user_id=42)
    v2, _ = tester.pick_variant("exp2", user_id=42)
    assert v1 in {"A", "B"}
    assert v2 in {"A", "B"}


def test_register_validates_split(tmp_path: Path) -> None:
    """Невалидный split (сумма != 1, неизвестные ключи, отрицательные) → ValueError."""
    tester = _make_tester(tmp_path)
    with pytest.raises(ValueError):
        tester.register_experiment(
            "bad",
            variants={"A": "a", "B": "b"},
            traffic_split={"A": 0.4, "B": 0.4},
        )
    with pytest.raises(ValueError):
        tester.register_experiment(
            "bad",
            variants={"A": "a", "B": "b"},
            traffic_split={"A": 1.0, "C": 0.0},
        )
    with pytest.raises(ValueError):
        tester.register_experiment(
            "bad",
            variants={"A": "a", "B": "b"},
            traffic_split={"A": -0.1, "B": 1.1},
        )
    with pytest.raises(ValueError):
        tester.register_experiment("bad", variants={})
