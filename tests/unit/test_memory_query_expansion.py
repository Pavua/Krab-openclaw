"""Тесты для memory_query_expansion."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.core.memory_query_expansion import (
    expand_query,
    merge_results,
    normalize,
    stem_simple,
)

# ---------------------------------------------------------------------------
# normalize
# ---------------------------------------------------------------------------


def test_normalize_yo_to_ye():
    assert normalize("Ёжик") == "ежик"


def test_normalize_lowercases():
    assert normalize("Hello WORLD") == "hello world"


def test_normalize_strips_whitespace():
    assert normalize("  привет  ") == "привет"


# ---------------------------------------------------------------------------
# stem_simple
# ---------------------------------------------------------------------------


def test_stem_simple_strips_endings():
    # "ить" → stripped, остаётся "установ"
    assert stem_simple("установить") == "установ"
    # "ка" не в списке → без изменений
    assert stem_simple("настройка") == "настройка"
    # "ние" (длина 3) в списке → "удаление"[:-3] = "удале"
    assert stem_simple("удаление") == "удале"
    # "ый" suffix: "красивый" → "красив"
    assert stem_simple("красивый") == "красив"


def test_stem_simple_no_change_short_word():
    # Слово слишком короткое → без изменений
    assert stem_simple("ить") == "ить"


# ---------------------------------------------------------------------------
# expand_query
# ---------------------------------------------------------------------------


def test_expand_simple_query():
    """Expand возвращает как минимум оригинальный запрос."""
    result = expand_query("hello world")
    assert result[0] == "hello world"
    assert len(result) >= 1


def test_expand_synonyms_replaced():
    """Синоним 'install' должен породить вариант с 'setup' или 'установить'."""
    result = expand_query("install krab", max_variants=3)
    joined = " ".join(result)
    # Хотя бы один вариант содержит синоним
    assert any(s in joined for s in ("setup", "установить", "установка"))


def test_expand_ru_en_crossover():
    """Запрос на русском порождает EN-вариант через словарь."""
    result = expand_query("установить krab", max_variants=3)
    joined = " ".join(result)
    assert any(s in joined for s in ("install", "setup", "установка"))


def test_expand_max_variants_respected():
    """expand_query не возвращает больше max_variants."""
    for n in (1, 2, 3):
        result = expand_query("установить настройка ошибка запустить", max_variants=n)
        assert len(result) <= n


def test_expand_disabled_by_env(monkeypatch):
    """При QUERY_EXPANSION_ENABLED=false expand возвращает только оригинал."""
    monkeypatch.setenv("QUERY_EXPANSION_ENABLED", "false")
    # Перезагружаем модуль чтобы подхватить новую env
    import src.core.memory_query_expansion as mod

    with patch.object(mod, "QUERY_EXPANSION_ENABLED", False):
        result = mod.expand_query("установить krab", max_variants=3)
    assert result == ["установить krab"]


def test_expand_empty_query():
    """Пустой запрос → список с пустой строкой, без ошибок."""
    result = expand_query("")
    assert result == [""]


def test_expand_no_duplicates():
    """Варианты не дублируют друг друга (в нормализованном виде)."""
    result = expand_query("error fail bug", max_variants=3)
    normalized = [normalize(v) for v in result]
    assert len(normalized) == len(set(normalized))


# ---------------------------------------------------------------------------
# merge_results
# ---------------------------------------------------------------------------


def _make_chunk(chunk_id: str, score: float) -> SimpleNamespace:
    obj = SimpleNamespace()
    obj.chunk_id = chunk_id
    obj.rrf_score = score
    return obj


def test_merge_dedups_by_chunk_id():
    """Дубликаты по chunk_id схлопываются в один результат."""
    r1 = [_make_chunk("a", 0.9), _make_chunk("b", 0.7)]
    r2 = [_make_chunk("a", 0.9), _make_chunk("c", 0.5)]
    merged = merge_results([r1, r2])
    ids = [r.chunk_id for r in merged]
    assert ids.count("a") == 1
    assert set(ids) == {"a", "b", "c"}


def test_merge_boosts_duplicate_scores():
    """chunk_id встречающийся в нескольких списках получает +20% к score."""
    r1 = [_make_chunk("dup", 1.0)]
    r2 = [_make_chunk("dup", 1.0)]
    merged = merge_results([r1, r2])
    dup = next(r for r in merged if r.chunk_id == "dup")
    assert dup.rrf_score == pytest.approx(1.2, rel=1e-6)


def test_merge_sorted_by_score_desc():
    """Результаты отсортированы по rrf_score убывающе."""
    r1 = [_make_chunk("low", 0.1), _make_chunk("high", 0.9)]
    merged = merge_results([r1])
    scores = [r.rrf_score for r in merged]
    assert scores == sorted(scores, reverse=True)


def test_merge_dict_results():
    """merge_results работает и со словарями."""
    r1 = [{"chunk_id": "x", "rrf_score": 0.8}]
    r2 = [{"chunk_id": "x", "rrf_score": 0.8}, {"chunk_id": "y", "rrf_score": 0.3}]
    merged = merge_results([r1, r2])
    ids = [r["chunk_id"] for r in merged]
    assert ids.count("x") == 1
    x_item = next(r for r in merged if r["chunk_id"] == "x")
    assert x_item["rrf_score"] == pytest.approx(0.96, rel=1e-6)


def test_merge_empty_lists():
    """Пустые входные списки → пустой результат."""
    assert merge_results([[], []]) == []
    assert merge_results([]) == []


def test_merge_skips_items_without_chunk_id():
    """Элементы без chunk_id игнорируются."""
    r1 = [SimpleNamespace(rrf_score=0.5)]  # нет chunk_id
    merged = merge_results([r1])
    assert merged == []
