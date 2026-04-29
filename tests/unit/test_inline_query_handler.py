# -*- coding: utf-8 -*-
"""Тесты для InlineQueryRouter (Idea 3 — inline mode prep)."""

from __future__ import annotations

import pytest

from src.core.inline_query_handler import (
    InlineQueryRouter,
    InlineResult,
    inline_query_router,
)


@pytest.fixture
def router() -> InlineQueryRouter:
    return InlineQueryRouter()


# ─── Базовые свойства результата ─────────────────────────────────────────────


def test_inline_result_snippet_auto_truncates() -> None:
    """snippet должен автоматически обрезаться, если не задан."""
    long_content = "x" * 500
    res = InlineResult(title="t", content=long_content)
    assert len(res.snippet) == 200
    assert res.snippet == "x" * 200


def test_inline_result_explicit_snippet_preserved() -> None:
    res = InlineResult(title="t", content="long content", snippet="short")
    assert res.snippet == "short"


# ─── Маршрут weather ─────────────────────────────────────────────────────────


def test_weather_route_with_city(router: InlineQueryRouter) -> None:
    results = router.route("weather Madrid")
    assert len(results) == 1
    r = results[0]
    assert "Madrid" in r.title
    assert r.meta.get("city") == "Madrid"
    assert r.meta.get("stub") is True


def test_weather_route_empty_city(router: InlineQueryRouter) -> None:
    results = router.route("weather")
    assert len(results) == 1
    assert "укажи" in results[0].title.lower() or "город" in results[0].content.lower()


# ─── Маршрут calc ────────────────────────────────────────────────────────────


def test_calc_route_basic(router: InlineQueryRouter) -> None:
    results = router.route("calc 23*7")
    assert len(results) == 1
    r = results[0]
    assert r.meta.get("result") == 161
    assert "161" in r.title


def test_calc_route_complex_arith(router: InlineQueryRouter) -> None:
    results = router.route("calc (10 + 5) * 2 / 3")
    assert pytest.approx(results[0].meta["result"], rel=1e-6) == 10.0


def test_calc_route_pow(router: InlineQueryRouter) -> None:
    results = router.route("calc 2 ** 10")
    assert results[0].meta["result"] == 1024


def test_calc_route_rejects_names(router: InlineQueryRouter) -> None:
    """Имена/вызовы должны быть отвергнуты — обработчик возвращает error-результат."""
    results = router.route("calc __import__('os').system('ls')")
    assert len(results) == 1
    assert results[0].meta.get("error") is True


def test_calc_route_empty(router: InlineQueryRouter) -> None:
    results = router.route("calc")
    assert "пустое" in results[0].title.lower() or "пустое" in results[0].content.lower()


# ─── Маршрут define ──────────────────────────────────────────────────────────


def test_define_route(router: InlineQueryRouter) -> None:
    results = router.route("define entropy")
    assert len(results) == 1
    assert results[0].meta.get("word") == "entropy"


# ─── Маршрут convert ─────────────────────────────────────────────────────────


def test_convert_length(router: InlineQueryRouter) -> None:
    results = router.route("convert 10 km mi")
    assert len(results) == 1
    assert pytest.approx(results[0].meta["result"], rel=1e-3) == 6.21371


def test_convert_temperature_c_to_f(router: InlineQueryRouter) -> None:
    results = router.route("convert 100 C F")
    assert pytest.approx(results[0].meta["result"], rel=1e-6) == 212.0


def test_convert_unsupported_units(router: InlineQueryRouter) -> None:
    results = router.route("convert 1 foo bar")
    assert results[0].meta.get("error") is True


def test_convert_bad_format(router: InlineQueryRouter) -> None:
    results = router.route("convert 10 km")
    assert "формат" in results[0].title.lower() or "convert" in results[0].title.lower()


# ─── Маршрут currency ────────────────────────────────────────────────────────


def test_currency_three_args(router: InlineQueryRouter) -> None:
    results = router.route("currency 100 USD EUR")
    assert results[0].meta["src"] == "USD"
    assert results[0].meta["dst"] == "EUR"
    assert results[0].meta["amount"] == 100


def test_currency_pair_slash(router: InlineQueryRouter) -> None:
    results = router.route("currency 50 USD/EUR")
    assert results[0].meta["src"] == "USD"
    assert results[0].meta["dst"] == "EUR"


# ─── Граничные случаи ────────────────────────────────────────────────────────


def test_unknown_command(router: InlineQueryRouter) -> None:
    results = router.route("nonsense_xyz hello")
    assert len(results) == 1
    assert results[0].meta.get("unknown") is True


def test_empty_query(router: InlineQueryRouter) -> None:
    results = router.route("")
    assert len(results) == 1
    assert results[0].meta.get("hint") is True


def test_whitespace_only(router: InlineQueryRouter) -> None:
    results = router.route("   ")
    assert results[0].meta.get("hint") is True


def test_supported_commands_list(router: InlineQueryRouter) -> None:
    cmds = router.supported_commands
    assert set(cmds) >= {"weather", "calc", "define", "convert", "currency"}


def test_singleton_instance() -> None:
    """Модульный singleton должен быть доступен."""
    assert isinstance(inline_query_router, InlineQueryRouter)
    results = inline_query_router.route("calc 1+1")
    assert results[0].meta["result"] == 2


# ─── Multi-result способность ────────────────────────────────────────────────


def test_route_returns_list(router: InlineQueryRouter) -> None:
    """Контракт: всегда возвращает list[InlineResult] (для будущих multi-result)."""
    results = router.route("weather Berlin")
    assert isinstance(results, list)
    assert all(isinstance(r, InlineResult) for r in results)
