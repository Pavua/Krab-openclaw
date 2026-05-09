# ТЗ: Идеальный crypto trading bot Краба

Этот документ описывает целевую архитектуру trading bot для команды Traders.
Он нужен как верхнеуровневое ТЗ для Coders, Analysts и Traders: не просто
автоматизировать сделки, а построить систему, которая сначала доказывает наличие
торгового преимущества на данных, затем проверяет его в paper trading, потом в
dry-run и только после этого допускается к микролотной real trading. Документ
связан с `docs/TRADERS_CODERS_TRADING_TERMINAL_TZ.md` и
`docs/PAPER_TRADING_BOT.md`, которые описывают ближайший MVP-контур.

Хлебная крошка для будущих агентов: это ТЗ сознательно ограничено крипторынком.
Акции, форекс и сырьевые рынки не входят в текущий контур, чтобы не смешивать
разные часы торгов, типы данных, комиссии, регуляторные ограничения и риск-модели.

## 1. Главный принцип

Бот не должен обещать прибыль. Бот должен:

- искать измеримое торговое преимущество;
- проверять гипотезы на истории и live-paper данных;
- строго ограничивать риск;
- объяснять каждое действие;
- уметь не торговать, если преимущества нет;
- технически запрещать real trading до прохождения risk gate.

Цель формулируется не как "всегда торговать в плюс", а как "не запускать
стратегию в реальные деньги, пока она не показала устойчивое преимущество после
комиссий, проскальзывания, задержек, просадок и стресс-сценариев".

## 2. Что значит "торгует в плюс"

Для допуска стратегии к следующему этапу нужны измеримые критерии:

- положительный expectancy после комиссий и проскальзывания;
- profit factor выше `1.2` на backtest и forward-test;
- max drawdown в пределах утверждённого лимита;
- отсутствие прибыли только за счёт одной случайной сделки;
- стабильность результата на разных рыночных режимах;
- понятная причина, почему edge должен существовать дальше;
- качество не ухудшается резко при небольшом изменении параметров;
- стратегия умеет оставаться в кэше во время плохого режима.

Хлебная крошка для будущих агентов: если стратегия "заработала" только на одном
периоде, одном активе или одном удачном пампе, это не edge, а кандидат на
переобучение.

## 3. Целевая архитектура

### 3.0 Гибридная модель управления

Оптимальная модель для Краба — гибридная, а не полностью автономная с первого
дня. Бот должен совмещать автоматический расчёт сигналов, жёсткий риск-контроль
и понятный ручной контроль Traders.

Режимы автономности:

- `advisory`: бот только анализирует рынок и предлагает действия;
- `paper_auto`: бот сам исполняет виртуальные сделки в paper-портфеле;
- `dry_run_auto`: бот строит реальные ордерные намерения без отправки на биржу;
- `live_confirm`: бот готовит ордер, но ждёт ручное подтверждение владельца;
- `live_micro_auto`: бот исполняет микролоты только внутри утверждённой стратегии;
- `live_scaled`: отдельный будущий режим после длительной статистики и review.

Правило допуска: новый уровень автономности включается только после отчёта с
метриками, ручного решения Traders и технической проверки Coders. Даже в
`live_micro_auto` Risk Engine и Kill-Switch имеют право остановить всё без
дополнительного согласования.

Практический вывод: "бот торгует в плюс" достигается не магией стратегии, а
тем, что слабые режимы, плохие данные, переобученные гипотезы и превышение риска
останавливают торговлю раньше, чем они становятся реальным убытком.

Принятое решение после обсуждения: строим crypto-гибрид с поддержкой нескольких
бирж. В первой версии гибрид означает несколько проверяемых стратегий под разные
режимы рынка, а не бесконтрольную торговлю всеми инструментами. Первая рабочая
фаза остаётся `spot/paper`, без плечей, без шортов и без реальных ордеров.

### 3.1 Data Layer

Обязанности:

- получать OHLCV, стакан, funding, open interest, volatility, market cap,
  dominance, stablecoin liquidity и on-chain/DeFi метрики;
- кешировать данные в SQLite или Postgres;
- хранить timestamp, источник и TTL для каждого набора данных;
- запрещать торговые решения при stale data;
- сравнивать несколько источников для критичных цен.

MVP-источники:

- CoinGecko для публичных цен;
- Binance/Bybit/OKX public market data через CCXT;
- локальный JSON/SQLite для состояния.

Продвинутые источники:

- funding и open interest;
- DeFiLlama TVL;
- unlock calendar;
- новости и risk events как аналитический слой, а не как автотриггер сделки.

### 3.1.1 Multi-Exchange Layer

Бот должен проектироваться как мультибиржевой с первого dry-run этапа, даже если
MVP начинает с одного публичного источника. Это нужно не для усложнения, а для
устойчивости: разные биржи имеют разные комиссии, ликвидность, лимиты API,
доступность пар, funding и качество исполнения.

Биржи первого полноценного crypto-контура:

- Binance;
- Bybit;
- OKX;
- Coinbase Advanced;
- Kraken;
- KuCoin;
- Gate.io.

Расширение после MVP:

- Bitget;
- MEXC;
- Bitfinex;
- Hyperliquid для отдельного perp/DEX-контура;
- DEX-источники через 0x/1inch/ParaSwap только после отдельного security review.

Единый контракт адаптера:

```text
ExchangeAdapter:
  get_exchange_status() -> ExchangeStatus
  get_markets() -> MarketCatalog
  get_ticker(symbol) -> Ticker
  get_order_book(symbol, depth) -> OrderBook
  get_ohlcv(symbol, timeframe, since, limit) -> Candle[]
  get_fees(symbol) -> FeeSchedule
  estimate_slippage(order_intent) -> SlippageEstimate
  build_order_intent(signal, risk_decision) -> OrderIntent
  submit_order(order_intent) -> ExecutionResult
```

Для режимов `paper`, `dry_run_exchange` и `live_locked` метод `submit_order`
должен быть технически заблокирован или заменён на запись намерения. Coders
обязаны покрыть это тестом, чтобы dry-run не мог случайно вызвать live API.

Order Router выбирает биржу не по названию, а по условиям:

- доступность нужной пары;
- глубина стакана и ожидаемое проскальзывание;
- комиссия maker/taker;
- задержка и стабильность API;
- лимиты аккаунта;
- текущий статус биржи;
- разница цены между площадками;
- ограничение на конкретную биржу в risk policy.

Если данные бирж расходятся сильнее заданного порога, бот не усредняет цену
молча, а ставит `market_data_disagreement` и блокирует новые сделки по активу.

### 3.1.2 Asset Universe

Активы делятся на уровни допуска:

- `core`: BTC, ETH;
- `large_caps`: SOL, BNB, XRP, ADA, AVAX, LINK, TON, DOGE, TRX, DOT, MATIC/POL;
- `watchlist_research`: перспективные активы только после отдельного анализа;
- `blocked`: низкая ликвидность, сомнительные токены, сильные unlock/event risks.

Для первого production-like paper trading разрешён только `core + selected
large_caps`. Любой новый актив добавляется через запись в changelog стратегии:
почему добавлен, какие риски, какой max weight, какой источник данных.

Хлебная крошка: широкий crypto-universe нужен для анализа возможностей, но
торговый allowlist должен быть узким. Если бот торгует всё подряд, он почти
наверняка торгует шум, корреляцию с BTC и чужую ликвидность.

### 3.2 Strategy Layer

Стратегии должны быть плагинами с единым контрактом:

```text
StrategyInput -> Signal[] -> RiskCheckedOrderIntent[]
```

Минимальный набор стратегий:

- trend-following: работа по устойчивому импульсу;
- mean-reversion: вход после перегиба и подтверждения восстановления;
- breakout: вход после выхода из диапазона с фильтром объёма;
- risk-off allocator: сокращение риска при плохом режиме рынка;
- cash strategy: осознанное бездействие, когда edge недостаточен.

Гибридный набор стратегий для crypto:

- core allocation: медленная аллокация BTC/ETH как базового риска;
- trend/momentum: работа по импульсу только при подтверждённом режиме;
- mean-reversion: ограниченный вход после сильного отклонения без ловли ножей;
- breakout: пробой диапазона с фильтром объёма и ликвидности;
- funding/carry: осторожная работа с funding, basis и perp-спредами;
- market-making lite: только paper/dry-run до отдельной проверки исполнения;
- DeFi yield monitor: аналитика доходности без автоперевода средств на первом этапе;
- event-risk reducer: снижение риска перед unlock, судом, листингом или macro event.

Запрещено в первой версии гибрида:

- плечи;
- шорты;
- grid/martingale;
- усреднение убыточной позиции без отдельного правила;
- торговля low-cap токенами;
- автоматический вход по новостям без подтверждения рыночными данными;
- стратегии, которые нельзя объяснить Traders одной короткой причиной.

Хлебная крошка: стратегии должны конкурировать за риск-бюджет, а не складывать
сигналы в одну сторону без учёта корреляции. Если BTC падает, пять "разных"
лонг-сигналов по альтам часто являются одним и тем же риском.

Каждая стратегия обязана возвращать:

- действие: `buy`, `sell`, `trim`, `hold`, `block`;
- score;
- confidence;
- ожидаемый риск;
- причину входа или отказа от входа;
- условия отмены сценария;
- предполагаемый горизонт сделки;
- параметры stop/take/rebalance, если применимо.

### 3.2.1 Strategy Router

Strategy Router решает, какие стратегии имеют право дать сигнал в текущем режиме:

- `risk-on`: разрешены core allocation, trend/momentum и selective breakout;
- `neutral`: разрешены core allocation и ограниченный trend/momentum, breakout
  только с повышенным score threshold;
- `risk-off`: разрешены risk-off allocator, trim/rebalance и cash strategy;
- `high-volatility`: новые входы уменьшаются или блокируются, кроме заранее
  описанных core mean-reversion сценариев;
- `event-risk`: новые входы по затронутым активам запрещены до обновления фактов.

Если стратегии конфликтуют, бот не обязан выбирать самый агрессивный сигнал. По
умолчанию применяется консервативное правило: снизить размер, оставить `hold` или
отправить конфликт в Traders review.

### 3.2.2 Портфельные режимы

Нужны минимум три профиля:

- `capital_preservation`: 40-70% cash/stables, только core и сильные large caps;
- `balanced_growth`: основной paper-профиль, умеренная доля альтов;
- `opportunity_mode`: временно повышенный риск только после сильного market regime
  score и без нарушения max drawdown/cash reserve.

Переключение профиля должно версионироваться и попадать в отчёт. Нельзя менять
профиль задним числом, чтобы улучшить статистику.

### 3.3 Regime Engine

Бот должен отдельно определять рыночный режим:

- risk-on;
- neutral;
- risk-off;
- high-volatility;
- BTC-led;
- alt-season candidate;
- liquidity contraction;
- event-risk.

Режим влияет на лимиты, а не просто выводится в отчёт. Например, в risk-off бот
снижает максимальный размер сделки, повышает cash reserve и блокирует покупку
слабых альтов.

### 3.4 Risk Engine

Risk Engine имеет право veto на любую сделку.

Обязательные лимиты:

- max position size;
- max asset weight;
- max exchange exposure;
- max sector/correlation exposure;
- max daily loss;
- max weekly loss;
- max drawdown;
- min cash reserve;
- max turnover;
- cooldown после серии убыточных сделок;
- запрет торговли при stale data;
- запрет торговли при недоступном kill-switch;
- запрет торговли при неизвестном режиме исполнения.

Дополнительные лимиты для гибридного crypto-контура:

- max altcoin exposure;
- max single-exchange data dependency;
- max correlated beta to BTC;
- min 24h volume threshold;
- min orderbook depth threshold для будущего dry-run/live;
- event-risk lock перед unlock, listing/delisting, major legal/regulatory event;
- запрет входа, если stop distance требует слишком большой риск на сделку.

Нужны разные профили:

- `paper_default`;
- `paper_aggressive_research`;
- `dry_run_conservative`;
- `live_micro`;
- `live_locked`.

Первый live-профиль после допуска: только микролоты, лимит на сделку `0.25-0.5%`
капитала, дневной kill-switch и ручное подтверждение повышения лимитов.

### 3.5 Execution Layer

Режимы исполнения:

- `paper`: виртуальные сделки;
- `dry_run_exchange`: реальные рыночные данные, но ордера только как намерения;
- `live_locked`: live-код установлен, отправка ордеров заблокирована;
- `live_micro`: реальные микролоты после ручного допуска;
- `live_scaled`: только после отдельного review и доказанной стабильности.

Типы исполнения:

- spot buy/sell;
- spot rebalance;
- perp dry-run;
- perp live только после отдельного допуска;
- DEX swap dry-run;
- DEX live запрещён до отдельного security review;
- transfer/rebalance между биржами запрещён для автоматического режима MVP.

На первом live-этапе разрешён только spot. Perpetuals, leverage, margin,
автоматические переводы между биржами и DeFi-подписи считаются повышенным
риском и включаются отдельными gate-документами.

Для каждого ордера хранить:

- стратегия;
- сигнал;
- risk decision;
- режим исполнения;
- intended order;
- simulated fill или реальный fill;
- комиссия;
- проскальзывание;
- итоговый PnL;
- ссылка на отчёт цикла.

### 3.6 Monitoring и Kill-Switch

Обязательные механизмы:

- глобальный kill-switch через файл-флаг/env;
- Telegram-команда владельца для остановки;
- автоматический halt при превышении лимитов;
- alert при ошибке данных;
- alert при расхождении цены между источниками;
- alert при серии отказов API;
- отдельный статус "бот жив, но торговля остановлена".

Бот должен быть хорошим именно в остановке. Плохой бот ищет сделку всегда;
рабочий бот спокойно пишет `hold` и объясняет почему.

## 4. Research Pipeline

Каждая стратегия проходит один и тот же путь:

1. Гипотеза: почему edge может существовать.
2. Данные: какие источники нужны и где риск искажения.
3. Backtest: фиксированный период, комиссии, проскальзывание.
4. Walk-forward: параметры выбираются на одном периоде, проверяются на другом.
5. Stress test: плохие режимы, гэпы, падение ликвидности.
6. Paper trading: минимум 2-4 недели без реальных ордеров.
7. Review: Traders + Analysts проверяют метрики и сделки.
8. Dry-run exchange: ордерные намерения рядом с реальным рынком.
9. Live micro: только после ручного допуска.
10. Scale review: повышение лимитов только после новой статистики.

Запрещено:

- подбирать параметры до красивого графика без out-of-sample проверки;
- удалять плохие сделки из статистики;
- менять правила стратегии без версии и changelog;
- смешивать результаты разных версий стратегии в одном отчёте.

## 5. Метрики качества

Минимальный отчёт стратегии:

- total return;
- CAGR;
- Sharpe;
- Sortino;
- max drawdown;
- Calmar;
- win rate;
- average win / average loss;
- profit factor;
- expectancy per trade;
- turnover;
- exposure;
- average holding time;
- fees;
- slippage;
- worst trade;
- best trade;
- longest losing streak;
- time in market;
- return vs buy-and-hold BTC/ETH;
- correlation to BTC;
- performance by market regime.

Решение по стратегии принимается не по одному числу PnL, а по набору метрик.

## 6. UX для Traders

Traders должны видеть не только "купить/продать", а полную картину:

- текущий режим рынка;
- активные стратегии;
- equity curve;
- drawdown;
- позиции и веса;
- открытые риски;
- последние сигналы;
- отклонённые сделки и причина veto;
- сделки за день;
- PnL по стратегии;
- статус источников данных;
- статус kill-switch;
- крупная метка режима: `PAPER`, `DRY RUN`, `LIVE LOCKED`, `LIVE MICRO`.

Telegram-команды:

- `!trade status`;
- `!trade risk`;
- `!trade strategies`;
- `!trade signals`;
- `!trade veto`;
- `!trade report`;
- `!trade halt confirm`;
- `!trade resume paper confirm`;
- `!strategy enable <name> paper confirm`;
- `!strategy disable <name> confirm`.

HTML-панель:

- компактный trading cockpit;
- без лендинга и декоративных hero-блоков;
- таблицы, графики, фильтры, статусы;
- браузерный smoke-тест после изменений.

## 7. API-контракты для Coders

Внутренние интерфейсы:

```text
load_market_data(request) -> MarketDataBundle
detect_market_regime(bundle) -> MarketRegime
run_strategy(strategy_id, bundle, portfolio, regime) -> Signal[]
check_risk(signal, portfolio, regime, policy) -> RiskDecision
build_order_intent(signal, risk_decision) -> OrderIntent
execute_order_intent(intent, mode) -> ExecutionResult
record_cycle(cycle_result) -> None
calculate_strategy_metrics(strategy_id, period) -> StrategyMetrics
render_traders_report(cycle_result) -> Report
```

HTTP endpoints:

- `GET /api/trading/status`;
- `GET /api/trading/regime`;
- `GET /api/trading/portfolio`;
- `GET /api/trading/positions`;
- `GET /api/trading/signals`;
- `GET /api/trading/veto`;
- `GET /api/trading/orders`;
- `GET /api/trading/trades`;
- `GET /api/trading/equity`;
- `GET /api/trading/metrics`;
- `POST /api/trading/paper/run`;
- `POST /api/trading/halt`;
- `POST /api/trading/strategies/{id}/enable`;
- `POST /api/trading/strategies/{id}/disable`.

Все ответы должны содержать:

- `mode`;
- `timestamp`;
- `data_freshness`;
- `source`;
- `risk_status`;
- `warnings`.

## 8. Этапы реализации

### Этап 1: Paper MVP hardening

- stale-data guard;
- kill-switch;
- нормальные метрики;
- единый движок вместо дублей;
- Telegram `status/run/report/risk`;
- HTML cockpit v1.

Готовность блока после реализации: примерно `70%` от paper-контура.

### Этап 2: Backtest Engine

- OHLCV-кэш;
- комиссии и проскальзывание;
- benchmark BTC/ETH;
- отчёт по режимам рынка;
- воспроизводимые сценарии.

### Этап 3: Strategy Registry

- несколько стратегий как плагины;
- versioning стратегий;
- сравнение стратегий;
- включение/отключение только через allowlist.

### Этап 4: Dry-run Exchange

- CCXT/public market data для Binance, Bybit, OKX, Coinbase, Kraken, KuCoin и Gate.io;
- ордерные намерения без отправки;
- сверка симуляции с реальным стаканом;
- выбор лучшей биржи через Order Router;
- блокировка торговли при расхождении цен между биржами;
- audit log.

### Этап 5: Live Micro Gate

- отдельный security review;
- хранение ключей вне логов и отчётов;
- live-адаптер заблокирован по умолчанию;
- микролоты только spot;
- дневной kill-switch;
- ручной post-trade review.

### Этап 6: Hybrid Live Control

- режим `live_confirm` для ручного подтверждения ордеров из Telegram/панели;
- лимит автономных сделок в день;
- запрет новых сделок после ручного override без повторного risk check;
- сравнение фактического исполнения с dry-run оценкой;
- автоматический downgrade в `live_locked` при ухудшении качества исполнения.

## 9. Definition of Done для "идеального" контура

Контур можно считать зрелым только если:

- есть минимум 2-4 недели paper статистики;
- есть backtest и walk-forward;
- есть dry-run на реальных данных;
- все сделки объяснимы;
- risk veto покрыт тестами;
- stale data блокирует торговлю;
- kill-switch проверен;
- live mode нельзя включить случайно;
- Traders видят понятный cockpit;
- Analysts могут проверить источники и метрики;
- Coders имеют воспроизводимый test/smoke набор;
- документация содержит текущие лимиты, версии стратегий и результаты проверок.

## 10. Готовые задачи для команд

### Coders

```text
!swarm coders взять docs/IDEAL_TRADING_BOT_TZ.md как целевую архитектуру, а
docs/TRADERS_CODERS_TRADING_TERMINAL_TZ.md как ближайший MVP. Реализовать Этап 1:
stale-data guard, kill-switch, metrics engine, единый paper engine, Telegram
commands status/run/report/risk и HTML cockpit v1. Real orders запрещены.
Docstring и комментарии на русском. Проверка: pytest, CLI smoke, browser smoke.
```

Дополнительная задача для мультибиржевого dry-run:

```text
!swarm coders спроектировать ExchangeAdapter и Order Router по docs/IDEAL_TRADING_BOT_TZ.md:
Binance, Bybit, OKX, Coinbase Advanced, Kraken, KuCoin, Gate.io через CCXT/public
data. Реальные ордера запрещены, submit_order в paper/dry-run/live_locked должен
быть покрыт тестом на техническую блокировку. Добавить оценку комиссий,
проскальзывания, статуса биржи и расхождения цен между источниками.
```

### Traders

```text
!swarm traders подготовить первую risk policy для Strategy Layer:
режимы рынка, лимиты по активам, условия входа/выхода, veto-правила, cooldown,
условия остановки стратегии и критерии допуска к dry-run. Не обещать доходность,
фиксировать только гипотезы, вероятности и риски.
```

### Analysts

```text
!swarm analysts подготовить research template для стратегий:
гипотеза edge, источники данных, период backtest, out-of-sample период,
stress scenarios, benchmark, признаки переобучения и итоговый verdict.
```

### Creative

```text
!swarm creative подготовить UX-макет Traders cockpit:
верхняя строка режима, risk status, equity/drawdown, позиции, сигналы, veto,
журнал сделок, статус данных, kill-switch. Интерфейс рабочий, плотный, без
маркетинговых обещаний доходности.
```

## 11. Решение Traders

Идеальный бот для Краба — это не "машина гарантированной прибыли", а система
дисциплины: данные, гипотезы, тесты, риск, объяснимость и право не торговать.
Главный фокус для Coders сейчас: довести paper trading до состояния, где Traders
ежедневно видят качество решений, ошибки, упущенные сделки и реальные метрики.

Текущая оценка:

- общий целевой trading bot: `14%`;
- paper trading MVP: `55%`;
- готовность к multi-exchange dry-run: `8%`;
- готовность к real trading: `0%`.
