# Paper Trading Бот Краба

Документ фиксирует первый безопасный контур виртуальной торговли криптой.
Цель: несколько недель торговать виртуальными $10k, собирать статистику,
отлаживать стратегию и только после этого обсуждать реальные биржевые ключи.

Полное ТЗ для команд `Analysts`, `Traders`, `Coders` и `Creative` находится в
[`TRADERS_CODERS_TRADING_TERMINAL_TZ.md`](TRADERS_CODERS_TRADING_TERMINAL_TZ.md).
Целевая архитектура "идеального" trading bot зафиксирована в
[`IDEAL_TRADING_BOT_TZ.md`](IDEAL_TRADING_BOT_TZ.md).
Этот файл остаётся кратким operational summary текущей реализации.

## Что сделано

- Добавлен модуль `src/trading/paper_bot.py`.
- Стартовый капитал: `$10,000` виртуального кэша.
- Источник данных: публичный CoinGecko API, без API-ключей.
- Торгуемые активы первого контура: BTC, ETH, SOL, LINK, AVAX, TON, BNB.
- Стратегия: умеренный моментум без покупки вертикального перегрева.
- Риск-лимиты: кэш-резерв 25%, максимум $850 на одну сделку, лимиты веса по активам.
- Состояние: `data/paper_trading_state.json`.
- Отчёт: `output/paper_trading_report.md`.
- Запуск одним кликом: `start_paper_trading_bot.command`.

## Как проверено

```bash
python3 -m pytest tests/unit/test_paper_trading_bot.py -q
python3 scripts/run_paper_trading_bot.py
```

## Что осталось

- Синхронизировать реализацию с ТЗ `docs/TRADERS_CODERS_TRADING_TERMINAL_TZ.md`.
- Свести `src/trading/paper_bot.py` и `src/skills/paper_trading.py` к одному каноническому движку.
- Добавить backtest на исторических OHLCV.
- Добавить метрики: CAGR, Sharpe, Sortino, max drawdown, win rate, turnover.
- Добавить Telegram-команду `!papertrade status/run`.
- Добавить HTML-панель портфеля и график equity curve.
- Добавить режим `dry-run exchange adapter` через CCXT без реальных ордеров.
- Заложить multi-exchange dry-run: Binance, Bybit, OKX, Coinbase, Kraken, KuCoin, Gate.io.
- Добавить OrderRouter: комиссии, глубина стакана, проскальзывание, статус биржи, расхождение цен.
- После 2-4 недель статистики: ручной risk review перед любыми real-money ключами.

## Текущий вывод

Делать бота актуально, но только как исследовательско-исполнительный контур:
данные, сигналы, журнал решений, риск-лимиты и paper trading. Делать сразу
реального торгового бота нельзя: без backtest, мониторинга и kill-switch это
будет не стратегия, а автоматизация убытков.

Целевой подход — гибридный: paper trading может работать автоматически, dry-run
строит ордерные намерения на реальных биржевых данных, а первый live-этап должен
быть spot-only, микролотный и с ручным подтверждением или очень жёсткими лимитами.
Плечи, margin, perpetuals, DEX swaps и автоматические переводы между биржами не
входят в первый real-money контур.
