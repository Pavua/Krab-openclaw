"""
Тесты risk-guard модуля.

Проверяем только локальные правила и журналирование, без реальных биржевых API.
Такой тестовый слой защищает executor от случайного обхода BTC-фильтра,
kill-switch и лимитов плеча/размера.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from src.trading.risk_guard import (
    OrderIntent,
    PortfolioState,
    RiskGuard,
    RiskJournal,
    StaticMarketDataAdapter,
    StaticPortfolioStateProvider,
)


def _guard(
    *,
    btc_price: float = 80_740.0,
    symbol_price: float = 93.4,
    amplitude_pct: float = 2.0,
    daily_pnl_pct: float = 0.0,
    equity: float = 10_000.0,
    exposure: dict[str, float] | None = None,
) -> RiskGuard:
    """Собирает risk-guard с fake providers."""

    market = StaticMarketDataAdapter(
        tickers={"BTC/USDT": btc_price, "SOL/USDT": symbol_price, "ETH/USDT": 2_329.0},
        amplitudes={"SOL/USDT": amplitude_pct, "ETH/USDT": amplitude_pct},
    )
    portfolio = StaticPortfolioStateProvider(
        PortfolioState(
            equity=equity,
            daily_pnl_pct=daily_pnl_pct,
            open_exposure=exposure or {},
        )
    )
    return RiskGuard(market, portfolio)


@pytest.mark.asyncio
async def test_blocks_new_long_when_btc_below_threshold() -> None:
    """BTC ниже 80.1k запрещает новые long."""

    guard = _guard(btc_price=80_099.0)

    decision = await guard.validate(
        OrderIntent("SOL/USDT", "long", requested_notional=500.0, requested_leverage=1.0)
    )

    assert decision.decision == "block"
    assert decision.approved_notional == 0.0
    assert "BTC_BELOW_LONG_THRESHOLD" in decision.reasons


@pytest.mark.asyncio
async def test_allows_long_when_btc_above_threshold_and_size_inside_limit() -> None:
    """При нормальном BTC и размере внутри лимита ордер проходит."""

    guard = _guard()

    decision = await guard.validate(
        OrderIntent(
            "SOL/USDT",
            "long",
            requested_notional=200.0,
            requested_leverage=1.0,
            price=93.4,
            stop_price=90.0,
        )
    )

    assert decision.decision == "allow"
    assert decision.approved_notional == 200.0
    assert decision.approved_leverage == 1.0


@pytest.mark.asyncio
async def test_reduces_sol_leverage_when_intraday_amplitude_is_high() -> None:
    """SOL amplitude выше 3% режет плечо до защитного cap."""

    guard = _guard(amplitude_pct=3.4)

    decision = await guard.validate(
        OrderIntent("SOL/USDT", "long", requested_notional=300.0, requested_leverage=2.0)
    )

    assert decision.decision == "reduce"
    assert decision.approved_leverage == 1.0
    assert "SOL_INTRADAY_AMPLITUDE_GT_3" in decision.reasons
    assert "LEVERAGE_LIMIT" in decision.reasons


@pytest.mark.asyncio
async def test_daily_drawdown_triggers_kill_switch() -> None:
    """Дневная просадка ниже -2.5% блокирует новые ордера."""

    guard = _guard(daily_pnl_pct=-2.51)

    decision = await guard.validate(
        OrderIntent("ETH/USDT", "long", requested_notional=100.0, requested_leverage=1.0)
    )

    assert decision.decision == "kill_switch"
    assert decision.blocked is True
    assert "DAILY_DRAWDOWN_KILL_SWITCH" in decision.reasons


@pytest.mark.asyncio
async def test_reduces_requested_size_above_calculated_max() -> None:
    """Запрошенный размер выше risk-cap получает reduce, а не allow."""

    guard = _guard(equity=10_000.0)

    decision = await guard.validate(
        OrderIntent(
            "SOL/USDT",
            "long",
            requested_notional=5_000.0,
            requested_leverage=1.0,
            price=93.4,
            stop_price=90.0,
        )
    )

    assert decision.decision == "reduce"
    assert 0.0 < decision.approved_notional < 5_000.0
    assert "POSITION_SIZE_LIMIT" in decision.reasons


@pytest.mark.asyncio
async def test_each_block_is_written_to_jsonl_and_sqlite(tmp_path: Path) -> None:
    """Блокировка оставляет audit-след в JSONL и SQLite."""

    journal = RiskJournal(
        jsonl_path=tmp_path / "risk_guard.jsonl",
        sqlite_path=tmp_path / "risk_guard.sqlite3",
    )
    market = StaticMarketDataAdapter(
        tickers={"BTC/USDT": 80_000.0, "SOL/USDT": 93.4},
        amplitudes={"SOL/USDT": 2.0},
    )
    portfolio = StaticPortfolioStateProvider(PortfolioState(equity=10_000.0, daily_pnl_pct=0.0))
    guard = RiskGuard(market, portfolio, journal=journal)

    decision = await guard.validate(
        OrderIntent("SOL/USDT", "long", requested_notional=100.0, requested_leverage=1.0)
    )

    jsonl_rows = (tmp_path / "risk_guard.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(jsonl_rows) == 1
    assert json.loads(jsonl_rows[0])["decision"]["decision"] == "block"

    with sqlite3.connect(tmp_path / "risk_guard.sqlite3") as conn:
        row = conn.execute("SELECT decision, reasons_json FROM risk_guard_decisions").fetchone()

    assert decision.decision == "block"
    assert row[0] == "block"
    assert "BTC_BELOW_LONG_THRESHOLD" in json.loads(row[1])
