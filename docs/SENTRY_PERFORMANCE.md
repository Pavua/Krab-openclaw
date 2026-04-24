# Sentry Performance Monitoring — Krab

Дополнение к error-трекингу: latency-метрики для slow transactions.
После активации Sentry собирает не только exceptions, но и P95/P99
длительности ключевых операций.

## Конфигурация

Env-переменные (читаются в `src/bootstrap/sentry_init.py`):

| Переменная | Default | Описание |
|------------|---------|----------|
| `SENTRY_DSN` | — | DSN проекта. Без него Sentry не поднимается. |
| `KRAB_ENV` | `production` | Environment-tag (dev/staging/production). |
| `SENTRY_TRACES_SAMPLE_RATE` | `0.1` (prod), `1.0` (dev) | Доля сессий для tracing. Clamp 0.0..1.0. |
| `SENTRY_PROFILES_SAMPLE_RATE` | `0.1` (prod), `1.0` (dev) | Доля трейсов с profiling. Clamp 0.0..1.0. |

10% sampling в prod — compromise между сигналом и SaaS-квотой.
Для recording более тонких распределений — выставить `1.0` в dev.

## Transactions и spans

### `op="memory.retrieval"` (name=`hybrid_search`)

Обёрнут в `HybridRetriever.search()` (`src/core/memory_retrieval.py`).

Внутренние spans:
- `memory.fts` — BM25 поиск по `messages_fts`
- `memory.vec` — KNN по `vec_chunks` (если sqlite-vec доступен)
- `memory.mmr` — MMR diversity re-ranking (если `KRAB_RAG_MMR_ENABLED=1`)

Tags:
- `chat_id` — ID чата (или `none`)
- `decay_mode` — режим decay (auto/gentle/aggressive/none)
- `mode` — `hybrid` (fts+vec) или `fts` (fallback)

### `op="llm.call"` (name=`openclaw_<model>`)

Обёрнут в `OpenClawClient.send_message_stream()` (`src/openclaw_client.py`).
Транзакция живёт на время async generator — от entry до финального yield.

Tags:
- `chat_id` — ID чата
- `model` — preferred_model или `auto`
- `force_cloud` — `1`/`0`
- `has_images` — `1`/`0` (vision-запрос)

### Gateway health-check

Автоматически покрывается `sentry_sdk.integrations.httpx` если подключена
(не обязательно, P95 gateway-latency уже виден в `llm.call`).

## Чтение в Sentry UI

1. Открыть [Performance tab](https://po-zm.sentry.io/performance/) проекта.
2. Filter → Transaction → `op:memory.retrieval` или `op:llm.call`.
3. Сортировка по `p95(duration)` — сверху самые медленные.

### Полезные фильтры

| Задача | Фильтр |
|--------|--------|
| Slow memory retrieval | `transaction.op:memory.retrieval p95(transaction.duration):>500ms` |
| Slow LLM на конкретной модели | `transaction.op:llm.call model:gemini-3-pro-preview` |
| Только vision-запросы | `transaction.op:llm.call has_images:1` |
| Fallback на FTS | `transaction.op:memory.retrieval mode:fts` |
| Hybrid retrieval | `transaction.op:memory.retrieval mode:hybrid` |

### P95 по фазам retrieval

Span-разбивка в UI показывает, где тратится время:
- `memory.fts` долго → проблема с индексом BM25 / большая БД
- `memory.vec` долго → sqlite-vec KNN на больших `vec_chunks`
- `memory.mmr` долго → on-the-fly encode вместо cached embeddings

## Overhead

- `start_transaction` / `start_span` — O(1). Не блочат event loop.
- Sampling 10% означает, что ~90% запросов вообще не собирают span-дерево.
- Graceful degradation: если `sentry_sdk` не установлен (dev) — runtime
  работает как раньше (see `src/core/sentry_perf.py`).

## Графаны и алерты (TODO)

- P95 `memory.retrieval` > 1s за 5 минут → Slack alert.
- P95 `llm.call` > 30s за 5 минут → Telegram alert через `proactive_watch`.
- Sentry Dashboard виджет: "Top 5 slow transactions last 24h".

Настройка через Sentry UI → Alerts → Create Alert → Metric Alert on
`p95(transaction.duration)`.
