# -*- coding: utf-8 -*-
"""
Wave 120: Prometheus метрики для search engine analytics (Brave Search).

Brave Search — платный сервис с per-request fee (~$0.005 / запрос ≈ €0.0046).
Tokens у поиска нет, поэтому Wave 78 token-cost FinOps не покрывает его.
Этот модуль трекает counts (по статусу) и аккумулирует projected cost (EUR).

Если `prometheus_client` отсутствует — все метрики становятся no-op.
"""

from __future__ import annotations

from typing import Any

try:
    from prometheus_client import Counter  # type: ignore[import-not-found]

    _HAS_PROM = True
except Exception:  # pragma: no cover — slim env

    class _Noop:
        def labels(self, *_a: Any, **_kw: Any) -> "_Noop":
            return self

        def inc(self, *_a: Any, **_kw: Any) -> None:
            return None

    Counter = _Noop  # type: ignore[assignment,misc]
    _HAS_PROM = False


# Per-request стоимость Brave Search Web Search API:
#   $5 / 1000 requests = $0.005 / request (Pro tier, 2026-05).
#   Курс EUR/USD ≈ 0.92 → €0.0046 / request.
# Ссылка: https://brave.com/search/api/ (pricing tab).
BRAVE_REQUEST_COST_EUR: float = 0.0046


# Counter: количество search-вызовов по статусу.
# Labels:
#   provider — "brave" сейчас, расширяемо.
#   status   — "ok" / "error" / "timeout".
krab_search_calls_total = Counter(
    "krab_search_calls_total",
    "Количество search-запросов по провайдеру и статусу (Wave 120)",
    ["provider", "status"],
)

# Counter: аккумулированная стоимость search-запросов (EUR).
# Только успешные запросы тарифицируются (errors/timeouts — не списываются).
krab_search_cost_eur_total = Counter(
    "krab_search_cost_eur_total",
    "Накопленная стоимость search-запросов в EUR (Wave 120)",
    ["provider"],
)


def record_search_call(
    provider: str,
    status: str,
    cost_eur: float = 0.0,
) -> None:
    """
    Зарегистрировать search-вызов в Prometheus.

    Args:
        provider: brave / ...
        status: ok / error / timeout
        cost_eur: стоимость запроса; для error/timeout передаём 0.0
                  (биллинг не списывает за провальные запросы).

    Fail-safe: ошибки prometheus_client глотаются — hot-path не должен падать.
    """
    try:
        krab_search_calls_total.labels(provider=provider, status=status).inc()
        if cost_eur > 0:
            krab_search_cost_eur_total.labels(provider=provider).inc(cost_eur)
    except Exception:  # noqa: BLE001
        return


__all__ = [
    "BRAVE_REQUEST_COST_EUR",
    "krab_search_calls_total",
    "krab_search_cost_eur_total",
    "record_search_call",
]
