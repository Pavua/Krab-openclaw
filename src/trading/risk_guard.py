"""
Risk-guard модуль для торгового контура Краба.

Модуль стоит синхронным шлюзом перед исполнением ордера: стратегия передаёт
намерение открыть/изменить позицию, а risk-guard возвращает решение
`allow`, `reduce`, `block` или `kill_switch`. Он не отправляет реальные ордера
и не знает секретов биржи: рыночные данные и состояние портфеля приходят через
узкие async-интерфейсы, поэтому этот слой можно безопасно тестировать отдельно
от exchange adapter.

Связь с остальным проектом: `paper_bot.py` отвечает за виртуальную стратегию,
а этот модуль даёт reusable риск-ядро для будущего торгового терминала, Telegram
команд и web-панели.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol

Decision = Literal["allow", "reduce", "block", "kill_switch"]
Side = Literal["long", "short"]

DEFAULT_JOURNAL_JSONL = Path("data/risk_guard_journal.jsonl")
DEFAULT_JOURNAL_SQLITE = Path("data/risk_guard_journal.sqlite3")


def utc_now() -> datetime:
    """Возвращает timezone-aware UTC timestamp для единых audit-записей."""

    return datetime.now(UTC)


def iso_now() -> str:
    """Возвращает UTC timestamp без микросекунд: удобно читать в JSONL."""

    return utc_now().replace(microsecond=0).isoformat()


@dataclass(frozen=True)
class OrderIntent:
    """Намерение стратегии до применения риск-лимитов."""

    symbol: str
    side: Side
    requested_notional: float
    requested_leverage: float
    price: float | None = None
    stop_price: float | None = None
    strategy_id: str = "unknown"
    reduce_only: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Ticker:
    """Минимальная котировка, достаточная для риск-решения."""

    symbol: str
    price: float
    timestamp: datetime = field(default_factory=utc_now)


@dataclass(frozen=True)
class IntradayStats:
    """Внутридневная амплитуда и диапазон цены по инструменту."""

    symbol: str
    low: float
    high: float
    amplitude_pct: float
    timestamp: datetime = field(default_factory=utc_now)


@dataclass(frozen=True)
class MarketSnapshot:
    """Снимок рынка, который попадает в решение и журнал."""

    symbol: str
    price: float
    btc_price: float
    intraday_amplitude_pct: float
    timestamp: str


@dataclass(frozen=True)
class PositionState:
    """Открытая позиция по символу, если она есть."""

    symbol: str
    side: Side
    notional: float
    leverage: float


@dataclass(frozen=True)
class PortfolioState:
    """Состояние портфеля, нужное для риск-ограничений."""

    equity: float
    daily_pnl_pct: float
    open_exposure: dict[str, float] = field(default_factory=dict)
    positions: dict[str, PositionState] = field(default_factory=dict)


@dataclass(frozen=True)
class RiskConfig:
    """Конфигурация правил без хардкода внутри движка."""

    btc_symbol: str = "BTC/USDT"
    btc_long_block_below: float = 80_100.0
    daily_kill_switch_drawdown_pct: float = 2.5
    risk_per_trade_pct: float = 0.75
    default_stop_distance_pct: float = 3.0
    target_intraday_amplitude_pct: float = 2.0
    sol_symbol: str = "SOL/USDT"
    sol_amplitude_leverage_cut_pct: float = 3.0
    sol_high_vol_leverage_cap: float = 1.0
    min_notional: float = 10.0
    max_snapshot_age_sec: int = 300
    core_symbols: frozenset[str] = frozenset({"BTC/USDT", "ETH/USDT"})
    alt_symbols: frozenset[str] = frozenset({"SOL/USDT", "BNB/USDT", "ADA/USDT", "XRP/USDT"})
    core_risk_budget_pct: float = 70.0
    alt_risk_budget_pct: float = 35.0
    default_risk_budget_pct: float = 20.0
    max_leverage_by_symbol: dict[str, float] = field(
        default_factory=lambda: {
            "BTC/USDT": 3.0,
            "ETH/USDT": 2.0,
            "SOL/USDT": 2.0,
        }
    )

    def bucket_budget_pct(self, symbol: str) -> float:
        """Возвращает бюджет экспозиции для группы актива."""

        if symbol in self.core_symbols:
            return self.core_risk_budget_pct
        if symbol in self.alt_symbols:
            return self.alt_risk_budget_pct
        return self.default_risk_budget_pct


@dataclass(frozen=True)
class RiskDecision:
    """Итоговое решение, которое должен уважать executor."""

    decision: Decision
    symbol: str
    approved_notional: float
    approved_leverage: float
    max_notional: float
    max_leverage: float
    reasons: list[str]
    market: MarketSnapshot
    created_at: str = field(default_factory=iso_now)

    @property
    def blocked(self) -> bool:
        """Удобный флаг для executor-слоя."""

        return self.decision in {"block", "kill_switch"}


@dataclass(frozen=True)
class RiskStatus:
    """Краткий статус risk-guard для панели или Telegram-команды."""

    active: bool
    kill_switch_active: bool
    last_decision: Decision | None
    last_reasons: list[str]
    updated_at: str


class MarketDataAdapter(Protocol):
    """Контракт рыночных данных; реализация может быть ccxt/native/mock."""

    async def get_ticker(self, symbol: str) -> Ticker:
        """Возвращает последнюю цену символа."""

    async def get_intraday_stats(self, symbol: str) -> IntradayStats:
        """Возвращает high/low и внутридневную амплитуду."""


class PortfolioStateProvider(Protocol):
    """Контракт портфеля без привязки к конкретной бирже."""

    async def get_state(self) -> PortfolioState:
        """Возвращает equity, дневной PnL и текущие экспозиции."""


class StaticMarketDataAdapter:
    """Простой adapter для тестов, демо и smoke-запуска без биржевых ключей."""

    def __init__(
        self,
        tickers: dict[str, float],
        amplitudes: dict[str, float] | None = None,
    ) -> None:
        self._tickers = tickers
        self._amplitudes = amplitudes or {}

    async def get_ticker(self, symbol: str) -> Ticker:
        """Берёт цену из локального словаря."""

        try:
            price = self._tickers[symbol]
        except KeyError as exc:
            raise LookupError(f"Нет демо-котировки для {symbol}") from exc
        return Ticker(symbol=symbol, price=float(price))

    async def get_intraday_stats(self, symbol: str) -> IntradayStats:
        """Строит минимальный synthetic диапазон вокруг цены."""

        ticker = await self.get_ticker(symbol)
        amplitude = float(self._amplitudes.get(symbol, 0.0))
        half_range = ticker.price * amplitude / 200.0
        return IntradayStats(
            symbol=symbol,
            low=max(0.0, ticker.price - half_range),
            high=ticker.price + half_range,
            amplitude_pct=amplitude,
        )


class StaticPortfolioStateProvider:
    """Простой provider портфеля для тестов и CLI-демо."""

    def __init__(self, state: PortfolioState) -> None:
        self._state = state

    async def get_state(self) -> PortfolioState:
        """Возвращает заранее заданное состояние."""

        return self._state


class PositionSizer:
    """Считает верхнюю границу размера позиции по риску и волатильности."""

    def __init__(self, config: RiskConfig) -> None:
        self._config = config

    def calculate(
        self,
        intent: OrderIntent,
        portfolio: PortfolioState,
        intraday_amplitude_pct: float,
    ) -> tuple[float, list[str]]:
        """Возвращает max notional и причины ограничений."""

        reasons: list[str] = []
        stop_distance_pct = self._stop_distance_pct(intent)
        risk_cash = portfolio.equity * self._config.risk_per_trade_pct / 100.0
        risk_based_notional = risk_cash / (stop_distance_pct / 100.0)

        volatility_multiplier = self._volatility_multiplier(intraday_amplitude_pct)
        if volatility_multiplier < 1.0:
            reasons.append("VOLATILITY_SIZE_REDUCTION")
        volatility_adjusted = risk_based_notional * volatility_multiplier

        bucket_budget = portfolio.equity * self._config.bucket_budget_pct(intent.symbol) / 100.0
        current_bucket_exposure = self._current_bucket_exposure(intent.symbol, portfolio)
        bucket_remaining = max(0.0, bucket_budget - current_bucket_exposure)
        if bucket_remaining < intent.requested_notional:
            reasons.append("RISK_BUCKET_LIMIT")

        max_notional = max(0.0, min(volatility_adjusted, bucket_remaining))
        if max_notional < intent.requested_notional:
            reasons.append("POSITION_SIZE_LIMIT")
        return max_notional, reasons

    def _stop_distance_pct(self, intent: OrderIntent) -> float:
        """Берёт фактический стоп, если стратегия его передала."""

        if intent.price and intent.stop_price and intent.price > 0:
            raw_distance = abs(intent.price - intent.stop_price) / intent.price * 100.0
            if raw_distance > 0:
                return raw_distance
        return self._config.default_stop_distance_pct

    def _volatility_multiplier(self, intraday_amplitude_pct: float) -> float:
        """Чем выше амплитуда, тем меньше допустимый размер."""

        if intraday_amplitude_pct <= self._config.target_intraday_amplitude_pct:
            return 1.0
        multiplier = self._config.target_intraday_amplitude_pct / intraday_amplitude_pct
        return max(0.25, min(1.0, multiplier))

    def _current_bucket_exposure(self, symbol: str, portfolio: PortfolioState) -> float:
        """Суммирует текущую экспозицию по той же группе риска."""

        if symbol in self._config.core_symbols:
            bucket = self._config.core_symbols
        elif symbol in self._config.alt_symbols:
            bucket = self._config.alt_symbols
        else:
            bucket = frozenset({symbol})
        return sum(float(portfolio.open_exposure.get(item, 0.0)) for item in bucket)


class RiskJournal:
    """Append-only журнал решений: JSONL для глазами, SQLite для запросов."""

    def __init__(
        self,
        jsonl_path: Path = DEFAULT_JOURNAL_JSONL,
        sqlite_path: Path = DEFAULT_JOURNAL_SQLITE,
    ) -> None:
        self._jsonl_path = jsonl_path
        self._sqlite_path = sqlite_path
        self._initialized = False

    async def write(
        self,
        intent: OrderIntent,
        decision: RiskDecision,
        portfolio: PortfolioState,
    ) -> None:
        """Пишет решение в оба storage; I/O вынесен в thread, чтобы не блокировать loop."""

        await asyncio.to_thread(self._write_sync, intent, decision, portfolio)

    def _write_sync(
        self,
        intent: OrderIntent,
        decision: RiskDecision,
        portfolio: PortfolioState,
    ) -> None:
        """Синхронная часть записи, удобная для SQLite."""

        self._ensure_initialized()
        payload = {
            "intent": asdict(intent),
            "decision": asdict(decision),
            "portfolio": asdict(portfolio),
        }

        self._jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        with self._jsonl_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")

        with sqlite3.connect(self._sqlite_path) as conn:
            conn.execute(
                """
                INSERT INTO risk_guard_decisions (
                    created_at, symbol, decision, approved_notional,
                    approved_leverage, reasons_json, payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    decision.created_at,
                    decision.symbol,
                    decision.decision,
                    decision.approved_notional,
                    decision.approved_leverage,
                    json.dumps(decision.reasons, ensure_ascii=False),
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                ),
            )

    def _ensure_initialized(self) -> None:
        """Создаёт SQLite-таблицу один раз на процесс."""

        if self._initialized:
            return
        self._sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self._sqlite_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS risk_guard_decisions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    approved_notional REAL NOT NULL,
                    approved_leverage REAL NOT NULL,
                    reasons_json TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_risk_guard_decisions_created_at
                ON risk_guard_decisions(created_at)
                """
            )
        self._initialized = True


class RiskGuard:
    """Главный шлюз, через который должен проходить каждый новый ордер."""

    def __init__(
        self,
        market_data: MarketDataAdapter,
        portfolio_provider: PortfolioStateProvider,
        *,
        config: RiskConfig | None = None,
        journal: RiskJournal | None = None,
    ) -> None:
        self._market_data = market_data
        self._portfolio_provider = portfolio_provider
        self._config = config or RiskConfig()
        self._journal = journal
        self._sizer = PositionSizer(self._config)
        self._kill_switch_active = False
        self._last_decision: RiskDecision | None = None

    async def validate(self, intent: OrderIntent) -> RiskDecision:
        """Проверяет намерение и возвращает финальное решение для executor."""

        self._validate_intent_shape(intent)
        try:
            portfolio, ticker, btc_ticker, intraday = await asyncio.gather(
                self._portfolio_provider.get_state(),
                self._market_data.get_ticker(intent.symbol),
                self._market_data.get_ticker(self._config.btc_symbol),
                self._market_data.get_intraday_stats(intent.symbol),
            )
        except Exception as exc:
            decision = self._build_failsafe_decision(
                intent, f"MARKET_OR_PORTFOLIO_DATA_ERROR:{exc}"
            )
            self._last_decision = decision
            return decision

        market = MarketSnapshot(
            symbol=intent.symbol,
            price=float(intent.price or ticker.price),
            btc_price=float(btc_ticker.price),
            intraday_amplitude_pct=float(intraday.amplitude_pct),
            timestamp=iso_now(),
        )
        reasons: list[str] = []

        if intent.reduce_only:
            decision = self._decision(
                intent=intent,
                market=market,
                decision="allow",
                max_notional=intent.requested_notional,
                max_leverage=intent.requested_leverage,
                reasons=["REDUCE_ONLY_RISK_EXIT"],
            )
            await self._record(intent, decision, portfolio)
            return decision

        if self._is_stale(ticker.timestamp) or self._is_stale(btc_ticker.timestamp):
            decision = self._decision(
                intent=intent,
                market=market,
                decision="block",
                max_notional=0.0,
                max_leverage=0.0,
                reasons=["STALE_MARKET_DATA"],
            )
            await self._record(intent, decision, portfolio)
            return decision

        if (
            self._kill_switch_active
            or portfolio.daily_pnl_pct <= -self._config.daily_kill_switch_drawdown_pct
        ):
            self._kill_switch_active = True
            decision = self._decision(
                intent=intent,
                market=market,
                decision="kill_switch",
                max_notional=0.0,
                max_leverage=0.0,
                reasons=["DAILY_DRAWDOWN_KILL_SWITCH"],
            )
            await self._record(intent, decision, portfolio)
            return decision

        if (
            intent.side == "long"
            and self._is_alt_symbol(intent.symbol)
            and btc_ticker.price < self._config.btc_long_block_below
        ):
            reasons.append("BTC_BELOW_LONG_THRESHOLD")

        max_leverage, leverage_reasons = self._max_leverage(intent, intraday)
        reasons.extend(leverage_reasons)
        max_notional, size_reasons = self._sizer.calculate(
            intent, portfolio, intraday.amplitude_pct
        )
        reasons.extend(size_reasons)

        if intent.requested_notional < self._config.min_notional:
            reasons.append("REQUESTED_NOTIONAL_BELOW_MIN")
        if max_notional < self._config.min_notional:
            reasons.append("MAX_NOTIONAL_BELOW_MIN")

        if "BTC_BELOW_LONG_THRESHOLD" in reasons or "MAX_NOTIONAL_BELOW_MIN" in reasons:
            raw_decision: Decision = "block"
        elif max_notional < intent.requested_notional or max_leverage < intent.requested_leverage:
            raw_decision = "reduce"
        else:
            raw_decision = "allow"

        decision = self._decision(
            intent=intent,
            market=market,
            decision=raw_decision,
            max_notional=max_notional,
            max_leverage=max_leverage,
            reasons=self._dedupe(reasons),
        )
        await self._record(intent, decision, portfolio)
        return decision

    async def current_status(self) -> RiskStatus:
        """Возвращает текущий статус без похода на биржу."""

        return RiskStatus(
            active=True,
            kill_switch_active=self._kill_switch_active,
            last_decision=self._last_decision.decision if self._last_decision else None,
            last_reasons=self._last_decision.reasons if self._last_decision else [],
            updated_at=iso_now(),
        )

    def reset_daily_state(self) -> None:
        """Сбрасывает локальный kill-switch после ручной дневной ротации."""

        self._kill_switch_active = False

    def _validate_intent_shape(self, intent: OrderIntent) -> None:
        """Отсекает явно некорректные намерения до внешнего I/O."""

        if intent.requested_notional <= 0:
            raise ValueError("requested_notional должен быть положительным")
        if intent.requested_leverage <= 0:
            raise ValueError("requested_leverage должен быть положительным")
        if intent.price is not None and intent.price <= 0:
            raise ValueError("price должен быть положительным")

    def _max_leverage(
        self,
        intent: OrderIntent,
        intraday: IntradayStats,
    ) -> tuple[float, list[str]]:
        """Считает верхнюю границу плеча по символу и волатильности."""

        base_cap = self._config.max_leverage_by_symbol.get(intent.symbol, 1.0)
        reasons: list[str] = []
        if (
            intent.symbol == self._config.sol_symbol
            and intraday.amplitude_pct > self._config.sol_amplitude_leverage_cut_pct
        ):
            base_cap = min(base_cap, self._config.sol_high_vol_leverage_cap)
            reasons.append("SOL_INTRADAY_AMPLITUDE_GT_3")
        if base_cap < intent.requested_leverage:
            reasons.append("LEVERAGE_LIMIT")
        return base_cap, reasons

    def _is_alt_symbol(self, symbol: str) -> bool:
        """BTC-фильтр режет новые long только по альтам, не по самому BTC/ETH."""

        return symbol not in self._config.core_symbols

    def _decision(
        self,
        *,
        intent: OrderIntent,
        market: MarketSnapshot,
        decision: Decision,
        max_notional: float,
        max_leverage: float,
        reasons: list[str],
    ) -> RiskDecision:
        """Нормализует численные поля решения."""

        if decision in {"block", "kill_switch"}:
            approved_notional = 0.0
            approved_leverage = 0.0
        else:
            approved_notional = min(intent.requested_notional, max_notional)
            approved_leverage = min(intent.requested_leverage, max_leverage)

        return RiskDecision(
            decision=decision,
            symbol=intent.symbol,
            approved_notional=round(approved_notional, 8),
            approved_leverage=round(approved_leverage, 8),
            max_notional=round(max_notional, 8),
            max_leverage=round(max_leverage, 8),
            reasons=reasons,
            market=market,
        )

    def _build_failsafe_decision(self, intent: OrderIntent, reason: str) -> RiskDecision:
        """При сбое данных блокируем ордер: торговый бот должен fail closed."""

        market = MarketSnapshot(
            symbol=intent.symbol,
            price=float(intent.price or 0.0),
            btc_price=0.0,
            intraday_amplitude_pct=0.0,
            timestamp=iso_now(),
        )
        return self._decision(
            intent=intent,
            market=market,
            decision="block",
            max_notional=0.0,
            max_leverage=0.0,
            reasons=[reason],
        )

    async def _record(
        self,
        intent: OrderIntent,
        decision: RiskDecision,
        portfolio: PortfolioState,
    ) -> None:
        """Сохраняет последнее решение и пишет journal, если он подключён."""

        self._last_decision = decision
        if self._journal is not None:
            await self._journal.write(intent, decision, portfolio)

    def _is_stale(self, timestamp: datetime) -> bool:
        """Проверяет, не устарела ли котировка."""

        return (utc_now() - timestamp).total_seconds() > self._config.max_snapshot_age_sec

    @staticmethod
    def _dedupe(items: list[str]) -> list[str]:
        """Убирает повторы причин, сохраняя порядок."""

        seen: set[str] = set()
        result: list[str] = []
        for item in items:
            if item in seen:
                continue
            seen.add(item)
            result.append(item)
        return result


async def run_demo() -> RiskDecision:
    """Запускает безопасный демо-прогон без подключения к бирже."""

    market = StaticMarketDataAdapter(
        tickers={"BTC/USDT": 80_740.0, "SOL/USDT": 93.4},
        amplitudes={"SOL/USDT": 3.4},
    )
    portfolio = StaticPortfolioStateProvider(
        PortfolioState(
            equity=10_000.0,
            daily_pnl_pct=-0.4,
            open_exposure={"BTC/USDT": 2_000.0, "ETH/USDT": 1_000.0},
        )
    )
    guard = RiskGuard(market, portfolio, journal=RiskJournal())
    return await guard.validate(
        OrderIntent(
            symbol="SOL/USDT",
            side="long",
            requested_notional=1_000.0,
            requested_leverage=2.0,
            price=93.4,
            stop_price=90.0,
            strategy_id="demo",
        )
    )


def _build_parser() -> argparse.ArgumentParser:
    """CLI нужен для smoke-проверки модуля одним запуском."""

    parser = argparse.ArgumentParser(description="Безопасный demo-запуск risk-guard Краба")
    parser.add_argument("--demo", action="store_true", help="запустить synthetic SOL/USDT проверку")
    return parser


def main() -> None:
    """Точка входа `python -m src.trading.risk_guard --demo`."""

    parser = _build_parser()
    args = parser.parse_args()
    if not args.demo:
        parser.print_help()
        return

    decision = asyncio.run(run_demo())
    print(json.dumps(asdict(decision), ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
