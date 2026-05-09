"""
Тесты paper trading бота.

Покрываем не внешний CoinGecko API, а внутреннюю торговую механику: сигналы,
риск-лимиты, сохранение портфеля и отчёт. Это защищает нас от ситуации, когда
бот начинает покупать без кэша, превышать лимиты или терять состояние.
"""

from __future__ import annotations

from pathlib import Path

from src.trading.paper_bot import (
    MarketSnapshot,
    Portfolio,
    build_signals,
    load_portfolio,
    portfolio_value,
    render_report,
    save_portfolio,
    apply_signals,
)


def _snapshots() -> dict[str, MarketSnapshot]:
    """Фикстура рынка с понятным положительным моментумом по BTC."""

    return {
        "BTC": MarketSnapshot("BTC", "bitcoin", 100_000.0, 2.0, 6.0, 1),
        "ETH": MarketSnapshot("ETH", "ethereum", 3_000.0, -2.0, -6.0, 2),
        "SOL": MarketSnapshot("SOL", "solana", 150.0, 0.2, 0.5, 5),
        "LINK": MarketSnapshot("LINK", "chainlink", 20.0, 1.0, 2.0, 15),
        "AVAX": MarketSnapshot("AVAX", "avalanche-2", 30.0, -1.0, -2.0, 20),
        "TON": MarketSnapshot("TON", "the-open-network", 5.0, 0.3, 1.8, 18),
        "BNB": MarketSnapshot("BNB", "binancecoin", 600.0, 0.1, 1.0, 4),
    }


def test_apply_signals_buys_without_touching_cash_reserve() -> None:
    """Покупка ограничена максимальной сделкой и кэш-резервом."""

    portfolio = Portfolio()
    snapshots = _snapshots()
    signals = build_signals(snapshots)

    trades = apply_signals(portfolio, snapshots, signals)

    assert trades
    assert portfolio.cash_usd >= portfolio_value(portfolio, snapshots) * 0.25
    assert all(trade.usd_value <= 850.0 for trade in trades)
    assert portfolio.positions["BTC"].units > 0


def test_save_and_load_portfolio_roundtrip(tmp_path: Path) -> None:
    """JSON-состояние сохраняет кэш, позиции и сделки между запусками."""

    path = tmp_path / "state.json"
    portfolio = Portfolio()
    snapshots = _snapshots()
    trades = apply_signals(portfolio, snapshots, build_signals(snapshots))
    assert trades

    save_portfolio(portfolio, path)
    loaded = load_portfolio(path)

    assert loaded.cash_usd == portfolio.cash_usd
    assert loaded.positions.keys() == portfolio.positions.keys()
    assert len(loaded.trades) == len(portfolio.trades)


def test_render_report_contains_core_sections() -> None:
    """Отчёт содержит портфель, сигналы и предупреждение про paper trading."""

    portfolio = Portfolio()
    snapshots = _snapshots()
    signals = build_signals(snapshots)
    trades = apply_signals(portfolio, snapshots, signals)

    report = render_report(portfolio, snapshots, signals, trades)

    assert "# Paper Trading Краба" in report
    assert "## Позиции" in report
    assert "## Сигналы" in report
    assert "paper trading" in report
