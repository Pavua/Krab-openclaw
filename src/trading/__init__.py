"""
Торговый пакет Краба.

Здесь живёт изолированный контур виртуальной торговли и риск-контроля: получение
рыночных данных, расчёт сигналов, risk-guard перед исполнением и учёт портфеля.
Пакет специально отделён от Telegram-команд, чтобы сначала безопасно отладить
логику на paper trading, а уже потом подключать уведомления, панель и биржевые
адаптеры.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.trading.risk_guard import (
        MarketDataAdapter,
        OrderIntent,
        PortfolioState,
        PortfolioStateProvider,
        RiskConfig,
        RiskDecision,
        RiskGuard,
        RiskJournal,
        RiskStatus,
    )

__all__ = [
    "MarketDataAdapter",
    "OrderIntent",
    "PortfolioState",
    "PortfolioStateProvider",
    "RiskConfig",
    "RiskDecision",
    "RiskGuard",
    "RiskJournal",
    "RiskStatus",
]


def __getattr__(name: str) -> Any:
    """Ленивый экспорт risk-guard, чтобы `python -m` не ловил runpy warning."""

    if name in __all__:
        from src.trading import risk_guard

        return getattr(risk_guard, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
