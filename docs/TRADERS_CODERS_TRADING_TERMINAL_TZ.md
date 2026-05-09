# ТЗ: Контур Traders для paper trading и подготовки к real trading

Документ описывает требования команды Traders к разработке торгового контура Краба.
Он нужен Coders как рабочее ТЗ: что реализовать, какие ограничения соблюдать,
как проверить результат и где проходит граница между аналитикой, paper trading и
реальными сделками. Документ связан с текущим заделом `docs/PAPER_TRADING_BOT.md`,
`src/trading/paper_bot.py`, `scripts/run_paper_trading_bot.py` и
`start_paper_trading_bot.command`.

Хлебная крошка для будущих агентов: текущий контур — только crypto. Мультибиржевой
слой нужен уже в архитектуре, но реальные ордера запрещены до отдельного допуска.

## 1. Цель

Сделать для команды Traders полноценный и наглядный инструмент, который:

- показывает рыночную картину по выбранным криптоактивам;
- формирует объяснимые торговые сигналы;
- ведёт paper-портфель с виртуальным капиталом;
- собирает статистику качества стратегии;
- позволяет Traders быстро понять: что куплено, почему, где риск и что делать дальше;
- заранее имеет архитектурную заготовку под real trading, но без возможности случайно отправить реальные ордера до ручного допуска.

Это не финансовый советник и не автопилот прибыли. Это исследовательско-исполнительный контур с жёстким риск-контролем.

Принятый профиль первой линии: crypto-гибрид под несколько бирж. На MVP-этапе
это означает не "торгуем всё", а строим совместимый фундамент: узкий allowlist
активов, несколько объяснимых стратегий, future-proof `ExchangeAdapter` и
жёсткую блокировку live-ордеров.

## 1.1 Целевая гибридная модель

Для Краба оптимален гибридный подход:

- бот автоматически собирает данные, считает сигналы, риск и сценарии;
- paper trading исполняется автоматически, чтобы набрать статистику без эмоций;
- dry-run строит ордерные намерения рядом с реальными стаканами бирж;
- live-режим сначала работает как `live_confirm`, где владелец подтверждает ордер;
- полная автономия допускается только для микролотов и только после статистики.

Это компромисс между скоростью и безопасностью. Traders получают полноценный
инструмент, Coders строят технический фундамент под real trading, но система не
может случайно перейти из аналитики в реальные деньги.

## 2. Роли команд

### Traders

- задают watchlist, риск-профиль и торговые гипотезы;
- читают отчёты, оценивают сигналы, разбирают сделки;
- принимают решение о допуске стратегии к следующему этапу;
- не имеют прямого доступа к реальным API-ключам биржи на этапе paper trading.

### Coders

- реализуют backend, CLI, Telegram-команды, панель, тесты и one-click запуск;
- не меняют риск-лимиты молча: любое ослабление лимитов фиксируется в документации;
- не добавляют real trading без отдельного security/risk gate.

### Analysts

- собирают рыночный факт-лист перед запуском paper trading;
- отделяют факты от гипотез и указывают дату/источник;
- помогают Traders оценивать результат: benchmark, drawdown, качество сигналов;
- не формируют команды исполнения и не обходят risk policy.

### Creative

- проектируют понятные Telegram-статусы и weekly review;
- помогают с HTML-панелью: плотная таблица, equity curve, сигналы, риск;
- делают режим `PAPER MODE` визуально очевидным;
- не добавляют маркетинговые обещания доходности в интерфейс.

### Общий security/risk gate

- Coders отвечают за техническую блокировку live-режима.
- Traders утверждают risk policy и ручной допуск.
- Analysts проверяют, что статистика не выглядит переобученной.
- Creative делает предупреждения о режиме и риске понятными в интерфейсе.
- Real trading блокируется, если нет статистики, лимитов потерь, kill-switch и ручного подтверждения владельца.

## 3. Этапы реализации

### Этап A: Paper Trading MVP

Статус: частично реализован.

Обязательные функции:

- виртуальный стартовый капитал `$10,000`;
- watchlist первого контура: BTC, ETH, SOL, LINK, AVAX, TON, BNB;
- публичный источник цен без API-ключей;
- узкий trading allowlist: `core + selected large caps`, без low-cap токенов;
- Strategy Router для выбора режима `swing/trend/breakout/risk-off/cash`;
- JSON-состояние портфеля;
- Markdown-отчёт после каждого запуска;
- запуск одним кликом через `start_paper_trading_bot.command`;
- unit-тесты на сигналы, лимиты, сохранение и отчёт.

Критерий готовности:

- `python3 -m pytest tests/unit/test_paper_trading_bot.py -q` проходит;
- `.command` файл запускает цикл и сохраняет отчёт;
- отчёт содержит портфель, позиции, сигналы, новые сделки и предупреждение о paper trading.

### Этап B: Traders UX

Нужно добавить:

- Telegram-команды:
  - `!papertrade status` — показать equity, cash, позиции, PnL, последние сделки;
  - `!papertrade run` — вручную запустить цикл;
  - `!papertrade report` — прислать последний отчёт;
  - `!papertrade reset confirm` — сбросить paper-портфель только с явным подтверждением;
  - `!market BTC` — краткий снимок актива;
  - `!watchlist` — показать текущий список активов.
- HTML-панель Traders:
  - equity curve;
  - таблица позиций;
  - журнал сделок;
  - карточки сигналов;
  - блок риск-лимитов;
  - статус источников данных;
  - крупная метка `PAPER MODE`.

Критерий готовности:

- команды отвечают в Telegram без раскрытия ключей и конфигов;
- панель открывается локально и показывает последние данные;
- кнопки/фильтры панели проверены через браузерный smoke-тест.

### Этап C: Backtest и метрики

Нужно добавить:

- загрузку исторических OHLCV;
- backtest по тем же правилам, что использует paper trading;
- комиссии и проскальзывание;
- метрики:
  - total return;
  - CAGR;
  - Sharpe;
  - Sortino;
  - max drawdown;
  - win rate;
  - profit factor;
  - turnover;
  - average holding time;
  - exposure by asset;
  - worst trade;
  - best trade.

Критерий готовности:

- backtest воспроизводим на фиксированном периоде;
- результат сохраняется в `output/trading_backtest_report.md`;
- стратегия не допускается дальше, если max drawdown, turnover или качество сделок выходят за лимиты.

### Этап D: Dry-run биржевой адаптер

Нужно добавить слой `ExchangeAdapter` с режимами:

- `paper` — текущий виртуальный портфель;
- `dry_run_exchange` — получает реальные рыночные данные и строит ордера, но не отправляет их;
- `live_locked` — live-код установлен, но отправка ордеров технически заблокирована;
- `live_enabled` — доступен только после отдельного ручного допуска.

Для dry-run:

- использовать CCXT или официальный SDK выбранной биржи;
- ключи не нужны, если используются только публичные данные;
- все «ордера» пишутся в журнал как намерения, а не реальные сделки.

Биржи первого dry-run контура:

- Binance;
- Bybit;
- OKX;
- Coinbase Advanced;
- Kraken;
- KuCoin;
- Gate.io.

Если по какой-то бирже публичный API нестабилен, Coders не удаляют её из ТЗ
молча, а помечают статус `degraded` и показывают причину в отчёте.

Единый контракт адаптера:

- `get_exchange_status()`;
- `get_markets()`;
- `get_ticker(symbol)`;
- `get_order_book(symbol, depth)`;
- `get_ohlcv(symbol, timeframe, since, limit)`;
- `get_fees(symbol)`;
- `estimate_slippage(order_intent)`;
- `build_order_intent(signal, risk_decision)`;
- `submit_order(order_intent)`.

В режимах `paper`, `dry_run_exchange` и `live_locked` метод `submit_order` обязан
возвращать технический отказ или dry-run запись, но не обращаться к live order API.
Это must-have тест перед любым merge.

Нужен `OrderRouter`, который выбирает площадку по:

- доступности пары;
- глубине стакана;
- комиссии;
- ожидаемому проскальзыванию;
- задержке API;
- статусу биржи;
- расхождению цены с другими источниками;
- allowlist/denylist из risk policy.

Критерий готовности:

- dry-run создаёт ордерные намерения без реального размещения;
- в отчётах явно видно, что реальных сделок нет;
- код не содержит пути, где dry-run может случайно вызвать live order API;
- при расхождении цен между биржами выше лимита новые сделки по активу блокируются.

### Этап E: Real trading gate

Real trading запрещён, пока не выполнены все условия:

- минимум 2-4 недели paper trading без технических сбоев;
- есть backtest и forward-test отчёты;
- max drawdown, дневной убыток и turnover в пределах лимитов;
- есть kill-switch;
- есть allowlist активов;
- есть лимит максимального notional на сделку;
- есть лимит дневного убытка;
- есть журнал всех решений и ордеров;
- ключи биржи хранятся только в безопасном месте и не попадают в логи;
- владелец вручную подтвердил переход.

Первый real trading режим:

- только spot;
- только микролоты;
- только `live_confirm` или `live_micro_auto`;
- без leverage, margin, perpetuals, DEX swaps и переводов между биржами;
- дневной лимит убытка и лимит количества сделок;
- автоматический downgrade в `live_locked` при ошибке исполнения, stale data или
  превышении расхождения цены между биржами.

## 4. Риск-лимиты по умолчанию

Для paper trading:

- стартовый капитал: `$10,000`;
- cash reserve: минимум 25%;
- максимум сделки: `$850`;
- минимум сделки: `$50`;
- максимум веса по BTC: 42%;
- максимум веса по ETH: 28%;
- максимум веса по SOL: 12%;
- максимум веса по LINK: 6%;
- максимум веса по AVAX: 5%;
- максимум веса по TON: 4%;
- максимум веса по BNB: 3%;
- максимум совокупной доли альтов без BTC/ETH: 30%;
- максимум зависимости от одного источника данных: не более 70% критичных решений;
- запрет усреднения в актив, если дневной импульс резко отрицательный и стратегия не объясняет вход;
- запрет покупки вертикального перегрева без отдельного правила.

Для будущего live trading начальные лимиты должны быть существенно ниже paper-лимитов.
Рекомендуемый первый live-контур после допуска: микролоты, не больше 0.25-0.5% капитала на сделку и дневной kill-switch.

## 5. Модель сигналов

Первый контур должен быть объяснимым, а не «магическим».

Минимальный сигнал:

- актив;
- действие: `buy`, `sell`, `trim`, `hold`;
- score;
- confidence;
- target weight;
- причина;
- риск;
- цена;
- дата;
- источник данных.

Допустимые факторы:

- 24h momentum;
- 7d momentum;
- волатильность;
- объём;
- market cap rank;
- drawdown from recent high;
- BTC regime;
- stablecoin/risk-off режим;
- корреляция с BTC/ETH.

ML/LLM можно добавлять только как аналитический слой объяснений, не как единственный источник решения.

Стратегии первой гибридной версии:

- `core_allocation`: медленное распределение между BTC/ETH;
- `trend_momentum`: вход только при подтверждённом режиме и объёме;
- `selective_breakout`: пробой диапазона с фильтром ликвидности;
- `risk_off_cash`: сокращение риска и уход в кэш/stables;
- `event_risk_reducer`: снижение риска перед unlock/listing/regulatory/macro событиями.

Запрещено в MVP:

- плечи;
- шорты;
- grid/martingale;
- автоматическая торговля low-cap токенами;
- автосделки на основе одной новости без подтверждения данными.

## 6. Данные и источники

MVP:

- CoinGecko public API;
- локальное JSON-состояние;
- Markdown-отчёты.

Следующий уровень:

- Binance/Bybit/OKX/Coinbase/Kraken/KuCoin/Gate.io public market data через CCXT;
- OHLCV-кэш в SQLite;
- отдельная таблица сделок;
- отдельная таблица сигналов;
- отдельная таблица equity curve.

Требования:

- при падении источника данных бот не торгует;
- stale data старше заданного TTL запрещает новые сделки;
- в отчёте всегда показывается источник и время данных;
- критичные цены сверяются минимум между двумя источниками, если это доступно.

## 7. Telegram UX

Ответы Traders должны быть короткими и понятными:

- статус портфеля;
- что изменилось;
- какие сделки были бы совершены;
- почему сигнал появился;
- какой риск сейчас главный;
- напоминание, что это не финансовая рекомендация.

Запрещено:

- раскрывать ключи, конфиги, env;
- обещать доходность;
- утверждать, что сделка гарантированно прибыльная;
- подтверждать статус других команд без проверки.

## 8. HTML-панель

Панель должна быть рабочим инструментом, а не лендингом.

Обязательные блоки:

- верхняя строка: режим, время последнего обновления, equity, PnL, cash;
- график equity curve;
- позиции;
- сигналы;
- журнал сделок;
- риск-лимиты;
- источники данных;
- кнопки `Run paper cycle`, `Refresh`, `Open report`.

Требования к интерфейсу:

- компактная таблица, удобная для сканирования;
- без декоративных hero-блоков;
- явная метка `PAPER MODE`;
- состояния загрузки, ошибки, stale data;
- адаптивность для MacBook и мобильного просмотра.

## 9. API-контракт для Coders

Минимальные внутренние функции:

- `run_paper_cycle() -> TradingCycleResult`;
- `load_portfolio() -> Portfolio`;
- `save_portfolio(portfolio) -> None`;
- `fetch_market_snapshots() -> dict[str, MarketSnapshot]`;
- `build_signals(snapshots) -> list[Signal]`;
- `apply_signals(portfolio, snapshots, signals) -> list[Trade]`;
- `render_report(...) -> str`;
- `calculate_metrics(...) -> TradingMetrics`;

Минимальные HTTP endpoints для панели:

- `GET /api/trading/paper/status`;
- `POST /api/trading/paper/run`;
- `GET /api/trading/paper/report`;
- `GET /api/trading/paper/trades`;
- `GET /api/trading/paper/signals`;
- `GET /api/trading/paper/equity`;
- `GET /api/trading/market/{symbol}`.

Endpoints следующего dry-run блока:

- `GET /api/trading/exchanges`;
- `GET /api/trading/exchanges/{exchange}/status`;
- `GET /api/trading/exchanges/{exchange}/orderbook/{symbol}`;
- `GET /api/trading/router/quote/{symbol}`;
- `GET /api/trading/router/disagreements`;
- `GET /api/trading/dry-run/intents`;

Все endpoints должны возвращать режим (`paper`, `dry_run_exchange`, `live_locked`, `live_enabled`) и timestamp данных.

## 10. Логи, аудит и безопасность

Каждый цикл должен писать:

- время запуска;
- источник данных;
- список snapshots;
- список сигналов;
- список применённых сделок;
- equity до/после;
- ошибки;
- режим исполнения.

Запрещено логировать:

- API keys;
- exchange secret;
- session strings;
- токены Telegram/OpenClaw;
- полные env-конфиги.

Нужен kill-switch:

- env/файл-флаг для полного запрета торговли;
- Telegram-команда владельца для остановки;
- защита от live-режима по умолчанию.

## 11. Проверка и приёмка

Coders должны предоставить:

- unit-тесты стратегии и риск-лимитов;
- тест сохранения/загрузки состояния;
- тест отчёта;
- тест stale data;
- тест запрета live order в paper/dry-run;
- smoke-тест Telegram-команд;
- browser smoke-тест HTML-панели;
- `.command` файл для запуска.

Минимальная команда проверки:

```bash
python3 -m pytest tests/unit/test_paper_trading_bot.py -q
python3 scripts/run_paper_trading_bot.py
```

После добавления панели:

```bash
python3 -m pytest tests/unit/test_paper_trading_bot.py -q
npm run test
npm run lint
```

## 12. Definition of Done

Блок считается готовым, если:

- запуск работает одним кликом на macOS;
- Traders видят понятный статус и отчёт;
- риск-лимиты покрыты тестами;
- случайная live-торговля технически невозможна;
- документация обновлена;
- есть понятные артефакты проверки;
- статус в отчёте не смешивает paper и live режимы.

## 13. Текущий статус готовности

Общий контур Traders trading: 18%.

Текущий блок paper trading MVP: 55%.

Готово:

- базовый paper trading модуль;
- виртуальный капитал;
- watchlist;
- риск-лимиты;
- JSON-состояние;
- Markdown-отчёт;
- one-click `.command`;
- unit-тесты базовой механики.

Не готово:

- Telegram-команды;
- HTML-панель;
- backtest;
- метрики качества;
- dry-run exchange adapter;
- kill-switch;
- stale-data guard;
- live trading gate.

## 14. Must-have улучшения

Приоритет 1:

- добавить stale-data guard;
- добавить Telegram-команды `!papertrade status/run/report`;
- добавить equity curve;
- добавить max drawdown и win rate;
- добавить kill-switch даже до live trading.

Приоритет 2:

- SQLite-хранилище сделок и equity;
- backtest на OHLCV;
- отдельный модуль `trading/risk.py`;
- отдельный модуль `trading/adapters/`;
- dashboard API endpoints.

Приоритет 3:

- dry-run CCXT adapter;
- multi-exchange Order Router;
- multi-strategy registry;
- Strategy Router для гибридного crypto-профиля;
- multi-exchange price disagreement guard;
- режим сравнения стратегий;
- weekly Traders review report.

## 15. Решение Traders

Команда Traders согласна с движением к полноценному торговому контуру, но только
через последовательность:

1. paper trading;
2. метрики и backtest;
3. dry-run exchange adapter;
4. ручной risk review;
5. микролотный live trading с kill-switch.

Сразу подключать реальные ордера нельзя. Сейчас оптимальная задача для Coders:
довести paper trading до удобного продукта для ежедневного использования Traders.
Параллельно можно проектировать ExchangeAdapter/OrderRouter, но без подключения
реальных ордеров и без хранения live-ключей.

## 16. Готовые задачи для Swarm

### Coders

```text
!swarm coders реализовать следующий блок trading-контура Краба по docs/TRADERS_CODERS_TRADING_TERMINAL_TZ.md:
сохранить текущий запуск start_paper_trading_bot.command, добавить stale-data guard,
kill-switch, метрики equity/max drawdown/win rate/profit factor, Telegram-команды
!papertrade status/run/report/trades/risk и минимальные API endpoints для HTML-панели.
Заложить интерфейсы Strategy Router и ExchangeAdapter под crypto-гибрид и несколько
бирж, но в MVP оставить только paper/spot, без плечей, шортов и реальных ордеров.
Реальные ордера запрещены. Все docstring и комментарии на русском. После реализации:
pytest, CLI smoke, browser smoke HTML-панели.
```

```text
!swarm coders параллельно подготовить проект ExchangeAdapter/OrderRouter для
multi-exchange dry-run: Binance, Bybit, OKX, Coinbase Advanced, Kraken, KuCoin,
Gate.io. Использовать публичные данные, считать комиссии/проскальзывание,
фиксировать расхождения цен и писать order intents без отправки ордеров.
submit_order в paper/dry_run/live_locked покрыть тестом технической блокировки.
```

### Traders

```text
!swarm traders подготовить первую версию risk policy и правил стратегий для paper trading Краба:
BTC/ETH/SOL/LINK/AVAX/TON/BNB, сценарии рынка на 2-8 недель, risk-on/risk-off режимы,
уровни отмены сценариев, лимиты веса, правила входа/выхода и условия остановки стратегии.
Отдельно описать гибридный Strategy Router: когда включать core allocation,
trend/momentum, selective breakout, risk-off cash и event-risk reducer.
Без обещаний прибыли, с вероятностями и рисками.
```

```text
!swarm traders подготовить crypto-only правила допуска к live_confirm/live_micro:
spot-only, максимальный риск на сделку, дневной kill-switch, лимит количества
сделок, запрет leverage/margin/perps/DEX на первом live-этапе, критерии отката
в live_locked и условия ручного повышения лимитов.
```

### Analysts

```text
!swarm analysts собрать свежий факт-лист для крипторынка перед первой неделей paper trading:
макро, ETF/институционалы, BTC dominance, stablecoin liquidity, funding/open interest,
on-chain/TVL, unlocks, регуляторика и сильные/слабые сектора. Для каждого вывода:
источник, дата, степень уверенности, влияние на риск.
Дополнительно сравнить Binance/Bybit/OKX/Coinbase/Kraken как источники данных:
доступность пар, комиссии, ликвидность, API-стабильность и ограничения для будущего dry-run/live.
```

### Creative

```text
!swarm creative подготовить шаблоны ежедневного и недельного отчёта trading-контура:
короткий Telegram-статус, подробный Markdown-отчёт, блок "почему бот сделал/не сделал сделку",
человеческое объяснение рисков без обещаний доходности.
```

## 17. Шаблоны отчётов для Traders

### Ежедневный Telegram-статус

```text
Paper Trading: {date}
Equity: ${equity} ({pnl_day_pct} за день, {pnl_total_pct} с начала)
Кэш: {cash_pct}
Позиции: {positions_short}
Сделки за сутки: {trades_count}
Главный риск: {main_risk}
Режим: PAPER, реальные ордера выключены.
```

### Недельный review

```text
Неделя: {week_range}
Итог: {total_return_pct}
Max drawdown: {max_drawdown_pct}
Win rate: {win_rate_pct}
Profit factor: {profit_factor}
Лучшее решение: {best_decision}
Худшее решение: {worst_decision}
Что меняем: {rules_to_change}
Решение Traders: продолжить / заморозить / откатить / отправить Coders на доработку.
```

Хлебная крошка для будущих агентов: отчёты должны объяснять не только сделки,
но и пропущенные сделки. Если бот ничего не купил, Traders всё равно должны
понимать, это был сигнал риска, отсутствие преимущества или ошибка данных.
