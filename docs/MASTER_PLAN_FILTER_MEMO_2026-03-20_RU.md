# Planning Memo: фильтр research-тезисов для нового master-plan

Этот memo нужен для пересборки master-plan без шума из research-документов.
Он не заменяет канонический `vNext`-план, а отбирает из аналитических материалов
только те тезисы, которые полезны для roadmap, и отдельно выносит то, что нельзя
тащить в продуктовый план как основной контур.

Опорные документы:

- `docs/deep-research-report.md`
- `docs/Оптимизация OpenClaw_ Стратегии и Архитектуры.md`
- `docs/PLAN-Краб+переводчик 12.03.2026.md`
- `docs/17.03.2026/MULTI_ACCOUNT_SWITCHOVER_RU.md`

## 1) Keep

- В новый master-plan нужно занести не идею "найти самую дешёвую модель", а правило:
  стоимость взрывается из-за количества LLM-циклов, tool-loop и длины контекста.
  Значит, roadmap должен включать явный FinOps-контур: сокращение числа дорогих
  вызовов, сокращение их размера и измерение фактических пожирателей бюджета.
- `Foundation Hardening` должен включать cost/usage observability по тем же
  сущностям, которые уже заложены в `Operator Identity Layer`:
  `operator_id`, `account_id`, `team_id`, `channel_id`, `trace_id`.
  Полезные метрики: `contextTokens`, доля tool-results в активном контексте,
  частота compaction, доля `NO_REPLY` housekeeping.
- `Inbox / Escalation Layer` нужно трактовать не только как UX-функцию, но и как
  FinOps-механизм: event-driven пробуждение агента должно вытеснять heartbeat и
  "проверки ради проверок".
- В roadmap нужно закрепить `context hygiene` как отдельный инженерный слой:
  pruning тяжёлых tool-results, настройка compaction, контроль `reserveTokens /
  keepRecentTokens`, pre-compaction memory flush перед свёрткой истории.
- Для voice/translator-трека нужно явно закрепить deterministic-first pipeline:
  `Krab Ear` делает локальные `STT / language detect / diarization / buffering`,
  а LLM включается точечно на semantic translation, терминологию, summaries и
  объяснение сложных фрагментов.
- В model policy нужно закрепить трёхуровневую маршрутизацию `cheap / default / pro`:
  дешёвый или локальный исполнитель для tool-loop и черновых операций, сильная
  облачная модель только как verifier/escalation на коротком brief.
- В assumptions master-plan нужно добавить аппаратную правду для текущего железа:
  на `Mac M4 Max 36GB` near-term baseline это локальный tier уровня `32B Q4` или
  `14B`-класс, но не `70B+` как рабочий baseline с длинным контекстом.
- `Multi-account` нужно оставить только как изоляцию сред:
  общий repo/docs/artifacts, но отдельные runtime/auth/secrets/browser state.
  Это полезно для воспроизводимости, безопасности и truthful handoff.
- Для `Coding/Dev Team` и `Deep Research Team` в master-plan нужно зафиксировать
  дисциплину границ: интерактивная разработка и первичный ресёрч не должны
  превращаться в постоянный 24/7 runtime OpenClaw; Krab должен получать уже
  структурированные brief/task/action контуры.

## 2) Defer

- Конкретный router-движок вроде `ClawRouter` полезен как идея, но не должен
  становиться обязательным deliverable до завершения `Foundation Hardening`,
  телеметрии и реального профилирования нагрузки.
- `Semantic caching` выглядит перспективно, но его стоит поднимать только после
  стабилизации identity/trace/artifact слоя, иначе кэш начнёт возвращать
  "правильные, но не к месту" ответы и ухудшит управляемость.
- `Batch lane / time-shifted processing` для несрочных задач полезен, но это
  следующий шаг после того, как уже собраны надёжные inbox/scheduler semantics.
- Паттерн `orchestrator -> worker` и более сложное `sub-agent` разбиение нужно
  отложить до зрелого `Swarm v2`, когда уже есть artifact handoff, checkpointing,
  quality gates и короткие специализированные промпты вместо монолитного контекста.
- `Cloud GPU` как отдельный fixed-cost inference contour нельзя делать baseline.
  Его стоит рассматривать только после реальных измерений токенной нагрузки и
  только если появится доказанная 24/7 загрузка, оправдывающая MLOps-слой.
- `Gemini API` как дешёвый облачный tier можно держать как будущий официальный
  fallback/default lane, но не как причину сейчас переписывать весь master-plan
  вокруг нового primary. Текущий baseline с `Codex primary` менять рано.
- Специализированное извлечение web-контента через отдельные сервисы/экстракторы
  стоит откладывать до стабилизации `Multimodal Ingest Layer`, чтобы не плодить
  внешние зависимости раньше времени.

## 3) Reject

- Из нового master-plan нужно исключить все серые web/session-backed схемы:
  cookie-bridge, reverse-proxy, эмуляцию API из consumer-подписок, прямое
  использование web-сессий ChatGPT/Gemini/Claude как системного backend.
- Нужно исключить и более "продуктизированную" версию того же риска:
  `Subscription Proxy Pool`, балансировщики Nginx/HAProxy поверх consumer
  подписок и любые идеи бесшовного failover между такими прокси.
- `Antigravity OAuth` и сходные consumer-only интеграции нельзя оставлять в
  master-plan как product track или как допустимый primary provider.
  Максимум — отдельная лабораторная заметка вне основного плана.
- `Multi-account` нельзя описывать как способ расширять квоты, обходить лимиты
  или распределять риск банов между учётками. Это противоречит самому смыслу
  switchover baseline и делает план токсичным.
- Из master-plan нужно выбросить весь darknet / uncensored / Tor-контур:
  `WormGPT`, `Nytheon`, "теневые" провайдеры, обход цензуры через скрытые сети
  и похожие идеи. Это не roadmap, а отдельная зона риска и юридических проблем.
- Нужно исключить Web3/x402/micropayment-routing и сходные экзотические схемы.
  Для текущего проекта это шумный, дорогой в поддержке и не подтверждённый
  контур, который не закрывает текущие bottleneck'и.
- Нельзя переносить в master-plan точные прайс-листы, месячные dollar estimates,
  рейтинги GPU-площадок и сравнения провайдеров как "план работ". В плане должны
  жить только архитектурные выводы, а не быстро стареющие ценовые таблицы.
- Не нужно превращать сравнение `Cursor / Antigravity / Perplexity / Vertex AI`
  в отдельные roadmap-ветки Краба. Это полезная эксплуатационная навигация для
  оператора, но не собственный deliverable проекта.

## 4) Что это меняет в master-plan

- `Foundation Hardening` нужно расширить подзадачами `FinOps + routing truth`:
  cost telemetry, compaction tuning, pruning policy, audit `NO_REPLY`-циклов,
  правила escalation и короткий verifier-brief вместо пересылки полного контекста.
- `Experimental Providers Lab` не должен жить как полноценный roadmap-блок.
  Его нужно либо убрать из основного плана, либо свести к короткому risk-register
  пункту без сроков, процентов готовности и без права быть `primary`.
- В `Multimodal + Voice Foundation` нужно явно записать budget discipline:
  локальная перцепция и preprocessing обязательны, а LLM не считается каналом
  для постоянной прокачки сырого аудиопотока.
- В assumptions master-plan нужно жёстче записать model policy:
  `Codex` остаётся primary для coding/runtime-контуров,
  локальный tier используется как дешёвый executor,
  cloud pro-модель — только verifier/escalation,
  а не универсальный "один мозг на всё".
- В assumptions и acceptance нужно добавить аппаратное ограничение:
  текущий Mac не является основанием планировать `70B+` local-first baseline.
  Иначе в план сразу закладывается заведомо ложное ожидание по latency и памяти.
- `Swarm v2` и `Product Teams` нужно перепривязать к prerequisite'ам:
  сначала routing, context hygiene, artifact handoff и inbox discipline,
  потом уже сложные planner/executor/verifier/critic петли.
- В `Multi-account` части master-plan нужно отдельно прописать запрет на
  quota-arbitrage и требование truthful isolation между учётками.
- В `Coding/Dev Team` и `Deep Research Team` нужно сменить трактовку успеха:
  не "Krab заменяет IDE/исследовательский инструмент", а "Krab оркестрирует,
  принимает brief, хранит trace/artifacts и выполняет действия там, где нужен
  долгоживущий операционный контур".

## Короткий вывод

Из research-документов в новый master-plan стоит брать в первую очередь
`FinOps discipline`, `routing policy`, `context hygiene`, `deterministic voice pipeline`
и `hardware truth`. Серые провайдерские обходы, подписочные прокси, теневые сети,
точечные ценовые таблицы и экзотические платёжные схемы нужно оставлять за
пределами master-plan как рискованный или просто шумный контур.
