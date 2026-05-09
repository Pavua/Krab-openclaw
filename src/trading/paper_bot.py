"""
Paper trading бот для крипторынка.

Модуль нужен, чтобы Краб мог вести виртуальный портфель на $10k без API-ключей
биржи и без риска реальных ордеров. Сейчас бот берёт публичные цены CoinGecko,
строит консервативный сигнал по моментуму и просадке, применяет риск-лимиты,
записывает сделки в JSON-состояние и формирует человекочитаемый отчёт.

Связь с остальным проектом намеренно слабая: Telegram/панель смогут вызывать
этот модуль как обычный Python API или CLI, а будущий биржевой адаптер заменит
только слой котировок/исполнения.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import httpx

COINGECKO_PRICE_URL = "https://api.coingecko.com/api/v3/simple/price"
COINGECKO_MARKET_URL = "https://api.coingecko.com/api/v3/coins/markets"

DEFAULT_STATE_PATH = Path("data/paper_trading_state.json")
DEFAULT_REPORT_PATH = Path("output/paper_trading_report.md")


@dataclass(frozen=True)
class AssetConfig:
    """Описывает монету, которую бот имеет право торговать."""

    symbol: str
    coingecko_id: str
    max_weight: float


@dataclass
class Position:
    """Позиция в виртуальном портфеле."""

    units: float = 0.0
    avg_price: float = 0.0


@dataclass
class Trade:
    """Запись одной виртуальной сделки для последующего аудита."""

    timestamp: str
    action: str
    symbol: str
    price: float
    units: float
    usd_value: float
    reason: str


@dataclass
class Portfolio:
    """Состояние paper-портфеля, которое сохраняется между запусками."""

    cash_usd: float = 10_000.0
    positions: dict[str, Position] = field(default_factory=dict)
    trades: list[Trade] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: now_iso())
    updated_at: str = field(default_factory=lambda: now_iso())


@dataclass(frozen=True)
class MarketSnapshot:
    """Текущий рыночный снимок по одной монете."""

    symbol: str
    coingecko_id: str
    price: float
    change_24h: float
    change_7d: float
    market_cap_rank: int | None


@dataclass(frozen=True)
class Signal:
    """Решение стратегии по одной монете."""

    symbol: str
    action: str
    score: float
    target_weight: float
    reason: str


DEFAULT_ASSETS: tuple[AssetConfig, ...] = (
    AssetConfig("BTC", "bitcoin", 0.42),
    AssetConfig("ETH", "ethereum", 0.28),
    AssetConfig("SOL", "solana", 0.12),
    AssetConfig("LINK", "chainlink", 0.06),
    AssetConfig("AVAX", "avalanche-2", 0.05),
    AssetConfig("TON", "the-open-network", 0.04),
    AssetConfig("BNB", "binancecoin", 0.03),
)


def now_iso() -> str:
    """Возвращает UTC timestamp в формате, удобном для JSON и отчётов."""

    return datetime.now(UTC).replace(microsecond=0).isoformat()


def load_portfolio(path: Path = DEFAULT_STATE_PATH) -> Portfolio:
    """Загружает портфель или создаёт новый с виртуальными $10k."""

    if not path.exists():
        return Portfolio()

    raw = json.loads(path.read_text(encoding="utf-8"))
    positions = {
        symbol: Position(**position_raw)
        for symbol, position_raw in raw.get("positions", {}).items()
    }
    trades = [Trade(**trade_raw) for trade_raw in raw.get("trades", [])]
    return Portfolio(
        cash_usd=float(raw.get("cash_usd", 10_000.0)),
        positions=positions,
        trades=trades,
        created_at=str(raw.get("created_at") or now_iso()),
        updated_at=str(raw.get("updated_at") or now_iso()),
    )


def save_portfolio(portfolio: Portfolio, path: Path = DEFAULT_STATE_PATH) -> None:
    """Сохраняет портфель в JSON, чтобы несколько недель paper trading не терялись."""

    path.parent.mkdir(parents=True, exist_ok=True)
    payload = asdict(portfolio)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


async def fetch_market_snapshots(
    assets: tuple[AssetConfig, ...] = DEFAULT_ASSETS,
    *,
    timeout_sec: float = 12.0,
) -> dict[str, MarketSnapshot]:
    """Получает цены и краткий моментум по списку разрешённых монет."""

    ids = ",".join(asset.coingecko_id for asset in assets)
    params = {
        "vs_currency": "usd",
        "ids": ids,
        "order": "market_cap_desc",
        "per_page": str(len(assets)),
        "page": "1",
        "sparkline": "false",
        "price_change_percentage": "24h,7d",
    }
    async with httpx.AsyncClient(timeout=timeout_sec) as client:
        response = await client.get(COINGECKO_MARKET_URL, params=params)
        response.raise_for_status()
        rows = response.json()

    by_id = {asset.coingecko_id: asset for asset in assets}
    snapshots: dict[str, MarketSnapshot] = {}
    for row in rows:
        asset = by_id.get(str(row.get("id")))
        if asset is None:
            continue
        snapshots[asset.symbol] = MarketSnapshot(
            symbol=asset.symbol,
            coingecko_id=asset.coingecko_id,
            price=float(row["current_price"]),
            change_24h=float(row.get("price_change_percentage_24h_in_currency") or 0.0),
            change_7d=float(row.get("price_change_percentage_7d_in_currency") or 0.0),
            market_cap_rank=row.get("market_cap_rank"),
        )

    missing = sorted({asset.symbol for asset in assets} - set(snapshots))
    if missing:
        raise RuntimeError(f"CoinGecko не вернул данные по: {', '.join(missing)}")
    return snapshots


def portfolio_value(portfolio: Portfolio, snapshots: dict[str, MarketSnapshot]) -> float:
    """Считает полную стоимость портфеля в USD по текущим ценам."""

    value = portfolio.cash_usd
    for symbol, position in portfolio.positions.items():
        snapshot = snapshots.get(symbol)
        if snapshot is None:
            continue
        value += position.units * snapshot.price
    return value


def build_signals(
    snapshots: dict[str, MarketSnapshot],
    assets: tuple[AssetConfig, ...] = DEFAULT_ASSETS,
) -> list[Signal]:
    """Строит консервативные сигналы: покупаем силу без перегрева, режем слабость."""

    signals: list[Signal] = []
    config_by_symbol = {asset.symbol: asset for asset in assets}
    for symbol, snapshot in snapshots.items():
        asset = config_by_symbol[symbol]
        score = snapshot.change_7d * 0.7 + snapshot.change_24h * 0.3

        if snapshot.change_7d < -12.0:
            signals.append(
                Signal(
                    symbol=symbol,
                    action="sell",
                    score=score,
                    target_weight=0.0,
                    reason="защитный выход: 7d просадка ниже -12%",
                )
            )
        elif 1.5 <= score <= 18.0 and snapshot.change_24h > -6.0:
            # Хлебная крошка: не гонимся за вертикальными свечами, чтобы бот не покупал пик.
            target = min(asset.max_weight, max(0.02, score / 100.0))
            signals.append(
                Signal(
                    symbol=symbol,
                    action="buy",
                    score=score,
                    target_weight=target,
                    reason="умеренный положительный моментум без сильного дневного провала",
                )
            )
        elif score < -4.0:
            signals.append(
                Signal(
                    symbol=symbol,
                    action="trim",
                    score=score,
                    target_weight=asset.max_weight * 0.25,
                    reason="слабый моментум, сокращаем риск",
                )
            )
        else:
            signals.append(
                Signal(
                    symbol=symbol,
                    action="hold",
                    score=score,
                    target_weight=0.0,
                    reason="нет преимущества для новой сделки",
                )
            )
    return sorted(signals, key=lambda signal: signal.score, reverse=True)


def apply_signals(
    portfolio: Portfolio,
    snapshots: dict[str, MarketSnapshot],
    signals: list[Signal],
    *,
    max_trade_usd: float = 850.0,
    min_trade_usd: float = 50.0,
    reserve_cash_weight: float = 0.25,
) -> list[Trade]:
    """Исполняет сигналы внутри виртуального портфеля с базовыми риск-лимитами."""

    total_value = portfolio_value(portfolio, snapshots)
    cash_floor = total_value * reserve_cash_weight
    new_trades: list[Trade] = []

    for signal in signals:
        snapshot = snapshots[signal.symbol]
        position = portfolio.positions.setdefault(signal.symbol, Position())
        current_value = position.units * snapshot.price
        target_value = total_value * signal.target_weight

        if signal.action in {"sell", "trim"} and current_value >= min_trade_usd:
            sell_value = (
                current_value if signal.action == "sell" else max(0.0, current_value - target_value)
            )
            sell_value = min(sell_value, current_value, max_trade_usd)
            if sell_value < min_trade_usd:
                continue
            units = sell_value / snapshot.price
            position.units = max(0.0, position.units - units)
            if position.units == 0.0:
                position.avg_price = 0.0
            portfolio.cash_usd += sell_value
            new_trades.append(
                Trade(
                    now_iso(),
                    signal.action,
                    signal.symbol,
                    snapshot.price,
                    units,
                    sell_value,
                    signal.reason,
                )
            )

        if signal.action == "buy":
            available_cash = max(0.0, portfolio.cash_usd - cash_floor)
            buy_value = min(max_trade_usd, available_cash, max(0.0, target_value - current_value))
            if buy_value < min_trade_usd:
                continue
            units = buy_value / snapshot.price
            old_value = position.units * position.avg_price
            position.units += units
            position.avg_price = (old_value + buy_value) / position.units
            portfolio.cash_usd -= buy_value
            new_trades.append(
                Trade(
                    now_iso(), "buy", signal.symbol, snapshot.price, units, buy_value, signal.reason
                )
            )

    portfolio.trades.extend(new_trades)
    portfolio.updated_at = now_iso()
    return new_trades


def render_report(
    portfolio: Portfolio,
    snapshots: dict[str, MarketSnapshot],
    signals: list[Signal],
    trades: list[Trade],
) -> str:
    """Формирует Markdown-отчёт для владельца и будущей Telegram-отправки."""

    total = portfolio_value(portfolio, snapshots)
    lines = [
        "# Paper Trading Краба",
        "",
        f"- Время: `{now_iso()}`",
        f"- Стоимость портфеля: `${total:,.2f}`",
        f"- Кэш: `${portfolio.cash_usd:,.2f}`",
        f"- Сделок за запуск: `{len(trades)}`",
        "",
        "## Позиции",
        "",
        "| Монета | Кол-во | Цена | Стоимость | Вес | PnL от средней |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for symbol, position in sorted(portfolio.positions.items()):
        snapshot = snapshots.get(symbol)
        if snapshot is None or position.units <= 0:
            continue
        value = position.units * snapshot.price
        weight = value / total if total else 0.0
        pnl = ((snapshot.price / position.avg_price) - 1.0) if position.avg_price else 0.0
        lines.append(
            f"| {symbol} | {position.units:.8f} | ${snapshot.price:,.2f} | "
            f"${value:,.2f} | {weight:.1%} | {pnl:+.1%} |"
        )

    lines.extend(
        ["", "## Сигналы", "", "| Монета | Действие | Score | Причина |", "|---|---|---:|---|"]
    )
    for signal in signals:
        lines.append(
            f"| {signal.symbol} | {signal.action} | {signal.score:+.2f} | {signal.reason} |"
        )

    lines.extend(["", "## Новые виртуальные сделки", ""])
    if not trades:
        lines.append("Сделок нет: риск-лимиты или сигналы не дали нормального входа.")
    else:
        lines.append("| Время | Действие | Монета | Сумма | Цена | Причина |")
        lines.append("|---|---|---|---:|---:|---|")
        for trade in trades:
            lines.append(
                f"| {trade.timestamp} | {trade.action} | {trade.symbol} | "
                f"${trade.usd_value:,.2f} | ${trade.price:,.2f} | {trade.reason} |"
            )

    lines.extend(
        [
            "",
            "## Ограничения",
            "",
            "- Это paper trading, не финансовая рекомендация и не обещание прибыли.",
            "- Реальные ордера запрещены до нескольких недель статистики, лимитов потерь и ручного допуска.",
            "- Следующий шаг: добавить backtest, метрики Sharpe/max drawdown и Telegram-уведомления.",
            "",
        ]
    )
    return "\n".join(lines)


async def run_once(
    *,
    state_path: Path = DEFAULT_STATE_PATH,
    report_path: Path = DEFAULT_REPORT_PATH,
) -> str:
    """Один цикл paper trading: данные → сигналы → сделки → отчёт."""

    portfolio = load_portfolio(state_path)
    snapshots = await fetch_market_snapshots()
    signals = build_signals(snapshots)
    trades = apply_signals(portfolio, snapshots, signals)
    save_portfolio(portfolio, state_path)
    report = render_report(portfolio, snapshots, signals, trades)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding="utf-8")
    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Разбирает CLI-аргументы для ручного запуска и `.command` файла."""

    parser = argparse.ArgumentParser(description="Paper trading бот Краба")
    parser.add_argument(
        "--state", type=Path, default=DEFAULT_STATE_PATH, help="JSON-состояние портфеля"
    )
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT_PATH, help="Markdown-отчёт")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI-точка входа."""

    args = parse_args(argv)
    report = asyncio.run(run_once(state_path=args.state, report_path=args.report))
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
