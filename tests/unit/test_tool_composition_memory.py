# -*- coding: utf-8 -*-
"""
Регрессии `src/core/tool_composition_memory.py` — память по комбинациям tools.

Что тестируем:

1. **record + recommend.** Базовая запись и выдача топ-K по success_rate × freq.
2. **Decay по возрасту.** Старые паттерны теряют вес, свежие догоняют.
3. **Persistence round-trip.** Запись на диск → новый instance читает то же.
4. **Empty store graceful.** recommend() на пустом store возвращает [].
5. **Multi-task isolation.** Паттерны task_class A не лезут в выдачу B.
6. **Idempotency / aggregate.** Повторный record для (class, combo)
   агрегирует, а не создаёт дубликат.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.core.tool_composition_memory import (
    ToolCompositionMemory,
    ToolPattern,
    tool_composition_memory,
)


@pytest.fixture
def store(tmp_path: Path) -> ToolCompositionMemory:
    return ToolCompositionMemory(
        storage_path=tmp_path / "tool_compositions.json",
        enabled=True,
    )


def test_empty_store_returns_empty_list(store: ToolCompositionMemory) -> None:
    assert store.recommend_tools("anything") == []
    assert store.list_patterns() == []
    # Пустой task_class — тоже graceful.
    assert store.recommend_tools("") == []


def test_record_and_recommend_basic(store: ToolCompositionMemory) -> None:
    # web_search — успешен 3/3
    for _ in range(3):
        store.record_session(
            "research",
            ["web_search", "summarize"],
            success=True,
            latency_ms=1500.0,
            cost_usd=0.002,
        )
    # tor_fetch — 2 успеха / 2 fail
    for ok in (True, True, False, False):
        store.record_session("research", ["tor_fetch"], success=ok, latency_ms=3000.0)

    recs = store.recommend_tools("research", top_k=3, min_samples=2)
    # web_search должен быть первым (success_rate 1.0 > 0.5).
    assert recs[0] == ["web_search", "summarize"]
    assert ["tor_fetch"] in recs


def test_min_samples_filter(store: ToolCompositionMemory) -> None:
    store.record_session("code_fix", ["grep"], success=True)
    # min_samples=2 → одиночная запись не попадает.
    assert store.recommend_tools("code_fix", min_samples=2) == []
    # min_samples=1 → попадает.
    assert store.recommend_tools("code_fix", min_samples=1) == [["grep"]]


def test_multi_task_isolation(store: ToolCompositionMemory) -> None:
    store.record_session("alpha", ["a1"], success=True)
    store.record_session("alpha", ["a1"], success=True)
    store.record_session("beta", ["b1"], success=True)
    store.record_session("beta", ["b1"], success=True)

    assert store.recommend_tools("alpha", min_samples=2) == [["a1"]]
    assert store.recommend_tools("beta", min_samples=2) == [["b1"]]
    # И списки паттернов фильтруются:
    alpha_only = store.list_patterns("alpha")
    assert len(alpha_only) == 1 and alpha_only[0]["tool_combination"] == ["a1"]


def test_idempotent_aggregate(store: ToolCompositionMemory) -> None:
    store.record_session("t", ["x"], success=True, latency_ms=100.0)
    store.record_session("t", ["x"], success=True, latency_ms=200.0)
    store.record_session("t", ["x"], success=False, latency_ms=300.0)

    patterns = store.list_patterns("t")
    assert len(patterns) == 1
    p = patterns[0]
    assert p["success_count"] == 2
    assert p["fail_count"] == 1
    # avg = (100+200+300)/3 = 200
    assert p["avg_latency_ms"] == pytest.approx(200.0)


def test_decay_old_patterns_lose_weight(tmp_path: Path) -> None:
    # Подменяем clock чтобы записать «старый» и «свежий» паттерн.
    clock = [datetime(2026, 1, 1, tzinfo=timezone.utc)]
    store = ToolCompositionMemory(
        storage_path=tmp_path / "td.json",
        now_fn=lambda: clock[0],
    )
    # Старый паттерн (записан в январе): много запусков, 100% успех.
    for _ in range(10):
        store.record_session("q", ["old_tool"], success=True)

    # Свежий паттерн (через 4 месяца): 3 запуска, 100% успех.
    clock[0] = datetime(2026, 5, 1, tzinfo=timezone.utc)
    for _ in range(3):
        store.record_session("q", ["new_tool"], success=True)

    # Decay должен поджать old_tool: 4 месяца ~= 120 дней → 0.5^4 ≈ 0.0625.
    # 10*0.0625 = 0.625 effective freq → log1p(0.625) ≈ 0.486
    # против log1p(3) ≈ 1.386 у new_tool. new_tool первым.
    recs = store.recommend_tools("q", top_k=2, min_samples=1)
    assert recs[0] == ["new_tool"]
    assert ["old_tool"] in recs


def test_persistence_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "persist.json"
    s1 = ToolCompositionMemory(storage_path=path)
    s1.record_session("docs", ["read", "summarize"], success=True, latency_ms=500.0, cost_usd=0.01)
    s1.record_session("docs", ["read", "summarize"], success=True, latency_ms=700.0, cost_usd=0.02)

    # Новый instance читает с диска.
    s2 = ToolCompositionMemory(storage_path=path)
    patterns = s2.list_patterns("docs")
    assert len(patterns) == 1
    p = patterns[0]
    assert p["tool_combination"] == ["read", "summarize"]
    assert p["success_count"] == 2
    assert p["avg_latency_ms"] == pytest.approx(600.0)
    assert p["avg_cost_usd"] == pytest.approx(0.015)


def test_corrupt_json_doesnt_crash(tmp_path: Path) -> None:
    path = tmp_path / "corrupt.json"
    path.write_text("{not valid json", encoding="utf-8")
    store = ToolCompositionMemory(storage_path=path)
    # Не упало, store пустой.
    assert store.list_patterns() == []
    # Можно записывать дальше.
    store.record_session("c", ["t"], success=True)
    assert len(store.list_patterns()) == 1


def test_clear(store: ToolCompositionMemory) -> None:
    store.record_session("a", ["x"], success=True)
    store.record_session("b", ["y"], success=True)
    assert store.clear("a") == 1
    assert store.list_patterns("a") == []
    assert len(store.list_patterns("b")) == 1
    assert store.clear() == 1  # remaining b


def test_disabled_flag_skips_recording(tmp_path: Path) -> None:
    store = ToolCompositionMemory(
        storage_path=tmp_path / "off.json",
        enabled=False,
    )
    store.record_session("x", ["y"], success=True)
    assert store.list_patterns() == []
    store.set_enabled(True)
    store.record_session("x", ["y"], success=True)
    assert len(store.list_patterns()) == 1


def test_empty_inputs_silently_ignored(store: ToolCompositionMemory) -> None:
    store.record_session("", ["t"], success=True)
    store.record_session("c", [], success=True)
    store.record_session("c", ["", "  "], success=True)  # все strip → пустые
    assert store.list_patterns() == []


def test_tool_pattern_from_dict_invalid() -> None:
    assert ToolPattern.from_dict({}) is None
    assert ToolPattern.from_dict({"task_class": "x"}) is None
    assert ToolPattern.from_dict({"task_class": "x", "tool_combination": "not-a-list"}) is None
    p = ToolPattern.from_dict({"task_class": "x", "tool_combination": ["a", "b"]})
    assert p is not None
    assert p.tool_combination == ("a", "b")


def test_singleton_exists() -> None:
    # Module-level singleton доступен и не падает на пустых вызовах.
    assert tool_composition_memory.recommend_tools("any") == []


def test_recent_latencies_capped_at_20(store: ToolCompositionMemory) -> None:
    for i in range(30):
        store.record_session("z", ["t"], success=True, latency_ms=float(i + 1))
    p = store.list_patterns("z")[0]
    assert len(p["recent_latencies_ms"]) == 20
    # Последние 20 значений — от 11 до 30.
    assert p["recent_latencies_ms"][0] == pytest.approx(11.0)
    assert p["recent_latencies_ms"][-1] == pytest.approx(30.0)


def test_hard_cutoff_excludes_ancient(tmp_path: Path) -> None:
    clock = [datetime(2025, 1, 1, tzinfo=timezone.utc)]
    store = ToolCompositionMemory(
        storage_path=tmp_path / "h.json",
        now_fn=lambda: clock[0],
    )
    for _ in range(5):
        store.record_session("k", ["ancient"], success=True)
    # Прыгаем через 1 год — _HARD_CUTOFF_DAYS=180.
    clock[0] = clock[0] + timedelta(days=365)
    assert store.recommend_tools("k", min_samples=2) == []
