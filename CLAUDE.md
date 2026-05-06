# CLAUDE.md

Контекст для Claude Code при работе с Krab (Краб).
Если этот файл расходится с runtime — верить runtime.

## Что это

Краб — персональный Telegram userbot на MTProto (pyrofork), связанный с OpenClaw Gateway,
owner-панелью на `:8080`, голосовым и browser-контуром, мультиагентным свёрмом,
и набором локальных/облачных AI-провайдеров.

Три контура с разным уровнем полномочий:
- **Telegram userbot** — боевой канал доставки, userbot-команды, ACL
- **Owner panel** `http://127.0.0.1:8080` — health/runtime/routing/ops
- **OpenClaw dashboard** `http://127.0.0.1:18789` — нативный chat/tool/agent

## Язык

Общение с пользователем — **на русском**. Комментарии в коде — на русском (краткие).

## Запуск и остановка

```bash
# Канонические лаунчеры (НЕ Restart Krab.command!)
/Users/pablito/Antigravity_AGENTS/new\ start_krab.command
/Users/pablito/Antigravity_AGENTS/new\ Stop\ Krab.command

# Gateway — НЕ SIGHUP! Использовать:
openclaw gateway

# Тесты
pytest tests/ -q
pytest tests/unit/test_openclaw_client.py -q
ruff check src/ && ruff format src/
```

## Auto-generated reference

- **Endpoints** (~277 routes): [docs/CLAUDE_AUTO_ENDPOINTS.md](docs/CLAUDE_AUTO_ENDPOINTS.md)
- **Handlers** (~172 функций): [docs/CLAUDE_AUTO_HANDLERS.md](docs/CLAUDE_AUTO_HANDLERS.md)
- **Commands** (~185+): [docs/CLAUDE_COMMANDS_REFERENCE.md](docs/CLAUDE_COMMANDS_REFERENCE.md)
- **Prometheus** (11 alerts, 27 metrics): [docs/CLAUDE_AUTO_PROMETHEUS.md](docs/CLAUDE_AUTO_PROMETHEUS.md)
- **Owner Panel API** (детальный): [docs/CLAUDE_OWNER_PANEL_API.md](docs/CLAUDE_OWNER_PANEL_API.md)

Актуальные счётчики:
```bash
# Endpoints (live)
curl -sS http://127.0.0.1:8080/api/endpoints | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d.get('endpoints',[])))"
# Handlers
grep -hE "^async def handle_" src/handlers/commands/*.py src/handlers/command_handlers.py | sort -u | wc -l
# Tests
pytest --collect-only -q 2>&1 | grep "tests collected"
```

## Архитектура (ключевые модули)

```
src/
  userbot_bridge.py     — ядро: Pyrogram MTProto, message processing, background tasks
  openclaw_client.py    — OpenClaw API клиент, tool execution loop, model routing
  mcp_client.py         — MCP relay: tool manifest, call_tool_unified, native tools
  config.py             — все env-переменные и конфигурация
  cache_manager.py      — кэш-слой (TTL, cleanup)
  memory_engine.py      — персистентная память (save/recall)
  model_manager.py      — управление моделями (health_check, format_status)
  search_engine.py      — AI-поиск через Brave + суммаризация
  voice_engine.py       — TTS/STT движок
  web_session.py        — сессии браузера
  core/
    agent_engine_openclaw.py — OpenClawAdapter (реализует AgentEngineClient Protocol, Wave 17-B)
    agent_engine_resolver.py — get_engine_for_route() (chat→room→env priority + health gate, Wave 17-B)
    swarm.py            — AgentRoom: мультиагентные роли, delegation
    swarm_bus.py        — SwarmBus + TEAM_REGISTRY (traders/coders/analysts/creative)
    swarm_memory.py     — персистентная память свёрма (JSON, FIFO 50/team)
    swarm_scheduler.py  — рекуррентный планировщик (!swarm schedule)
    swarm_channels.py   — live broadcast в Telegram группы
    swarm_task_board.py — Kanban-доска задач свёрма (create/assign/done/fail)
    swarm_team_listener.py — автономные DM-обработчики команд
    swarm_team_prompts.py  — промпты ролей команд
    swarm_artifact_store.py — хранилище артефактов свёрма
    swarm_verifier.py   — верификация результатов свёрма
    swarm_research_pipeline.py — Research Pipeline (session 7)
    subprocess_env.py   — clean_subprocess_env() (MallocStackLogging cleanup)
    proactive_watch.py  — фоновый мониторинг runtime state (+ ErrorDigest + Telegram alerts)
    weekly_digest.py    — еженедельный дайджест активности
    observability.py    — метрики, SLA, timeline событий
    cost_analytics.py   — FinOps: расходы по провайдерам
    inbox_service.py    — Inbox items: create/update/stale remediate
    language_detect.py  — определение языка (langdetect)
    translator_engine.py       — движок перевода (bilingual/auto режимы)
    translator_session_state.py — состояние переводческой сессии
    translator_runtime_profile.py — профиль RT-переводчика
    translator_finish_gate.py  — gate для окончания перевода
    translator_live_trial_preflight.py — preflight перед live-trial
    translator_mobile_onboarding.py    — онбординг на мобильном
    voice_gateway_control_plane.py — control plane Voice Gateway
    silence_mode.py     — режим тишины (on/off)
    silence_schedule.py — расписание тишины (session 7)
    memo_service.py     — хранилище заметок !memo (session 7)
    bookmark_service.py — закладки !bookmark/!bm (session 7)
    chat_monitor.py     — мониторинг активности чата (session 7)
    command_aliases.py  — алиасы команд (session 7)
    command_registry.py — реестр команд + метаданные (session 7)
    message_scheduler.py — планировщик отложенных сообщений (session 7)
    telegram_buttons.py — inline-кнопки Telegram (session 7)
    reaction_engine.py  — автоматические реакции (session 7)
    personal_todo.py    — персональный TODO-лист (session 7)
    spam_guard.py       — антиспам защита (session 7)
    spam_filter.py      — фильтр спама
    access_control.py   — ACL и права доступа
    capability_registry.py — реестр возможностей
    chat_ban_cache.py   — кэш банов чата
    chat_capability_cache.py — кэш возможностей чата
    cloud_gateway.py    — CloudGateway клиент
    cloud_key_probe.py  — проверка cloud API-ключей
    ecosystem_health.py — здоровье всей экосистемы
    error_handler.py    — централизованная обработка ошибок
    handoff_auto_export.py — авто-экспорт handoff-нот
    local_health.py     — здоровье локальных сервисов
    lm_studio_auth.py   — авторизация LM Studio
    lm_studio_health.py — мониторинг LM Studio
    logger.py           — настройка логирования
    mcp_registry.py     — реестр MCP серверов
    model_aliases.py    — алиасы моделей
    model_config.py     — конфигурация моделей
    model_router.py     — routing по моделям
    model_types.py      — типы и схемы моделей
    openclaw_runtime_models.py  — runtime модели OpenClaw
    openclaw_runtime_signal_truth.py — истина состояния OpenClaw
    openclaw_secrets_runtime.py — runtime секреты OpenClaw
    openclaw_workspace.py  — workspace OpenClaw
    operator_identity.py   — идентичность оператора
    provider_manager.py    — управление провайдерами
    provisioning_service.py — сервис provisioning
    routing_errors.py   — ошибки routing
    runtime_policy.py   — политика runtime
    scheduler.py        — общий планировщик задач
    shared_repo_switchover.py — переключение shared-репозитория
    shared_worktree_permissions.py — права worktree
    telegram_rate_limiter.py — rate limiter для Telegram API
    auth_recovery_readiness.py — готовность к восстановлению авторизации
    exceptions.py       — кастомные исключения
    sender_context.py   — контекст отправителя (session 17)
    operator_info_guard.py — guard оператора (session 17)
    memory_llm_rerank.py   — LLM rerank результатов памяти (session 17)
    gemini_rerank_provider.py — Gemini rerank провайдер (session 17)
    skill_scope.py      — scope-фильтр навыков (session 17)
    cross_ai_review.py  — кросс-AI ревью ответов (session 17)
    skill_discovery_check.py — проверка доступности навыков (session 17)
    mention_detector.py — детектор упоминаний (session 17)
    fingerprint_http.py — HTTP fingerprinting (session 17)
    human_like.py       — human-like задержки/поведение (session 17)
    stealth_metrics.py  — метрики stealth-режима (session 17)
    memory_retrieval_scores.py — скоры поиска памяти (session 17)
    memory_doctor.py    — диагностика и авторемонт индекса памяти (session 17)
    agent_engine.py     — AgentEngine Protocol (session 35, Wave 16-B)
    agent_engine_router.py — выбор engine (openclaw/hermes/auto) (session 35, Wave 16-B)
    skill_curator.py    — SkillCurator Steps 1-4: analyzer + apply_with_approval + A/B framework (session 33-35)
    chat_response_policy.py — JSON store + ChatMode enum + auto-adjust (Smart Routing, session 26)
    llm_intent_classifier.py — LM Studio HTTP + LRU cache (Smart Routing, session 26)
    feedback_tracker.py — Pyrogram delete/reaction → signals (Smart Routing, session 26)
    trigger_detector.py — async 5-stage orchestrator (Smart Routing, session 26)
  handlers/
    command_handlers.py — 105+ команд, _AgentRoomRouterAdapter; 4430 LOC (Session 28, −77.4% от 19637)
    commands/           — 24 модуля (Waves 1-18 + session 35-38)
  integrations/
    google_genai_direct.py — direct google.genai SDK bypass (Wave 18-B, production-verified)
    tor_bridge.py       — Tor SOCKS5 proxy (httpx + Playwright)
    hermes_acp_bridge.py — Hermes Phase B ACP bridge foundation (session 35, Wave 16-B)
    browser_bridge.py   — CDP подключение к Chrome
    browser_ai_provider.py — AI через браузер
    hammerspoon_bridge.py — HTTP bridge к Hammerspoon :10101
    macos_automation.py — AppleScript/osascript автоматизация
    krab_ear_client.py  — клиент KrabEar (STT диаризация)
    voice_gateway_client.py — клиент Voice Gateway
    voice_gateway_subscriber.py — подписчик Voice Gateway событий
    cli_runner.py       — запуск CLI инструментов (codex/gemini/claude)
  bootstrap/
    session_recovery.py — shared recovery module: attempt_recovery/has_recent_recovery_backup (session 35, Wave 16-N)
  scripts/
    openclaw_runtime_repair.py — recovery chain: validate+probe+sqlite .recover (session 35, Wave 16-J)
  userbot/
    access_control.py   — ACL на уровне userbot
    auto_translate.py   — авто-перевод сообщений
    background_tasks.py — фоновые задачи userbot
    llm_flow.py         — основной LLM flow
    llm_retry.py        — retry логика LLM
    llm_text_processing.py — постобработка LLM ответов
    runtime_status.py   — runtime статус userbot
    session.py          — управление сессиями
    voice_profile.py    — профиль голоса
  skills/
    mercadona.py        — Playwright scraper со stealth
    crypto.py           — крипто утилиты
    imessage.py         — iMessage интеграция
    web_search.py       — веб-поиск
  modules/
    web_app.py          — Owner panel FastAPI (:8080)
    web_app_costs_dashboard.py  — дашборд расходов
    web_app_inbox_dashboard.py  — дашборд inbox
    web_app_landing_page.py     — главная страница
    web_app_stats_dashboard.py  — дашборд статистики
    web_app_swarm_dashboard.py  — дашборд свёрма
    web_router_compat.py        — compat-слой роутера
    perceptor.py        — восприятие медиа (OCR, image analysis)
  web/
    index.html          — главный HTML шаблон
    prototypes/         — Gemini-generated dashboard prototypes
```

## Инфраструктура (LaunchAgents)

| Service | Port | Label |
|---------|------|-------|
| OpenClaw gateway | 18789 | `ai.openclaw.gateway` |
| MCP yung-nagato (kraab) | 8011 | `com.krab.mcp-yung-nagato` |
| MCP p0lrd | 8012 | `com.krab.mcp-p0lrd` |
| MCP Hammerspoon | 8013 | `com.krab.mcp-hammerspoon` |
| Inbox watcher | — | `ai.krab.inbox-watcher` |

MCP серверы — SSE транспорт. Claude Desktop подключается через `npx mcp-remote` proxy.
Plists: `scripts/launchagents/`

## Модели и routing

Runtime truth: `~/.openclaw/agents/main/agent/models.json`

Текущий routing (05.05.2026):
- Primary: `google/gemini-3-pro-preview`
- Translator: `google/gemini-3-flash-preview` (preferred_model для скорости)
- Fallbacks: `gemini-2.5-pro-preview`, `gemini-2.5-flash`, `gemini-3-flash-preview`
- `google-antigravity` — НЕ использовать (квота/бан)
- LM Studio local — автоматический fallback при cloud-failure
- Google direct SDK bypass: `KRAB_GOOGLE_DIRECT_BYPASS_ENABLED=1` (default ON, Wave 18-B)

## Свёрм (Multi-Agent)

Команды в Telegram: `!swarm <team> <topic>`, `!swarm teams`, `!swarm schedule`, `!swarm memory`
Teams: `traders`, `coders`, `analysts`, `creative`
Forum-группа: **Krab Swarm** (chat_id: `-1003703978531`)

Tool access: web_search, tor_fetch (если TOR_ENABLED), peekaboo, все MCP tools.
`SWARM_ROLE_MAX_OUTPUT_TOKENS` default 4096.

## Smart Message Routing (Session 26 — LIVE)

5-stage pipeline: Hard gates → Per-chat policy → Regex filter → LLM classifier → Feedback loop.
Управление: `!chatpolicy [show|set <mode>|threshold|stats|list|reset]`
Env: `KRAB_IMPLICIT_TRIGGER_THRESHOLD`. Spec: `docs/SMART_ROUTING_DESIGN.md`

## Виртуальное окружение

| Путь | Python | Pyrogram | Назначение |
|------|--------|----------|-----------|
| `venv/` | 3.13 | pyrofork 2.3.69 | Runtime, MCP, тесты |

## Ключевые env vars

```bash
KRAB_CODEX_CLI_FIRST_CHUNK_TIMEOUT_SEC=600
KRAB_CODEX_CLI_FALLBACK_MODEL=google/gemini-3.1-pro-preview
KRAB_LLM_IDLE_TIMEOUT_SEC=180        # молчание без tool_calls → kill
KRAB_LLM_HEARTBEAT_INTERVAL_SEC=60   # интервал heartbeat edit
KRAB_GOOGLE_DIRECT_BYPASS_ENABLED=1  # direct google.genai SDK (Wave 18-B)
KRAB_AGENT_ENGINE_DISPATCH_ENABLED=0 # Hermes dispatch (default OFF)
GEMINI_PAID_KEY_ENABLED=1            # paid Gemini активен
LM_STUDIO_NATIVE_REASONING_MODE=medium
KRAB_REASONING_LEVEL=medium
OPENCLAW_REASONING_EFFORT=medium
```

## Правила

- **Не дублируй нативный функционал OpenClaw** если он уже есть
- **Не SIGHUP openclaw** — только `openclaw gateway` для рестарта
- **LM Studio модели** — тестировать ONE AT A TIME (RAM overflow на 36GB M4 Max)
- **Subprocess** — всегда `env=clean_subprocess_env()` для subprocess'ов
- **Handoff** — после изменений обновляй memory и IMPROVEMENTS.md
- **Проверяй после правок**: `pytest tests/ -q`, `ruff check src/`

## Phase 7 статус

- **Phase 7: 100%**, Memory Phase 2: **LIVE** (`KRAB_RAG_PHASE2_ENABLED=1`)
- **12594 тестов collected** (Session 38, 05.05.2026)
- **277 API endpoints** (live: `/api/endpoints`), 172+ handlers, 185+ команд

## Session highlights (последние)

### Session 38 highlights (05.05.2026 — Waves 23-A/B/C, 24-A/B/C/D/E, 25-A/B/D/E/F, 26-A/B, 27-A, 28-A)
- Vertex AI direct bypass (Gemini 8 моделей в global, Anthropic Claude pending quota)
- CLI subprocess bypass для codex-cli/* + google-gemini-cli/* (Wave 22-A finally working после exec fix)
- Multi-account codex rotation (~/.codex_accounts/)
- Graceful shutdown 15s grace + post-doctor primary reapply
- OAuth auto-resync daemon + Krab Ear coexistence monitor LaunchAgents
- `!quota` Telegram command + reconciled_state `/api/model/status`
- Russian "Краб" name detection в sender_context (Wave 25-F)
- Greeting target hint для reply_to (Wave 26-A)
- Implicit question detection 10-min window (Wave 26-B)
- Network resilience с TCP probe + auto-reconnect (Wave 27-A)
- CLAUDE.md split на 5 модульных файлов (Wave 28-A)

### Session 36 highlights (04.05.2026 — Waves 16-P → 18-H, 30+ commits)
- Wave 16-P: code review LOW fixes — HermesACPBridge async singleton, SkillCurator atomicity
- Wave 17-B: **Hermes Phase C live wiring** — `agent_engine_openclaw.py`, `agent_engine_resolver.py`, 3 endpoints, 3 Prometheus метрики; ENV gate `KRAB_AGENT_ENGINE_DISPATCH_ENABLED=0`
- Wave 17-C: убран hardcoded How2AI fallback из `config.py`
- Wave 18-A: session backup retention — 7 категорий, keep_recent=3, max_age_days=14
- Wave 18-B→H: **Google direct SDK bypass** — production verified 5.5s через google_direct channel
- ~68 тестов добавлено; 3× MacBook OOM — Krab пережил (integrity ok, Wave 16-N отработал)

### Session 35 highlights (04.05.2026 — Wave 16 series, 22 commits)
- Production incident: gateway crash loop — `tools.web.search.provider: brave` → плагин исчез → fix: `brave → gemini`
- Wave 16-F: Pyrogram conn invalidate после malformed swallow — `_corrupt_flag` + early raise
- Wave 16-G: reply→audio extraction — `_message_has_reply_audio()` + `_transcribe_audio_message()`
- Wave 16-H: health probe lock-contention → read-only URI + async retry
- Wave 16-I: idle-aware liveness — tool_calls считаются как activity, idle gate 180s
- Wave 16-J: `scripts/openclaw_runtime_repair.py` (520 LOC) — recovery chain
- Wave 16-N: `src/bootstrap/session_recovery.py` — auto-invoke из preflight, idempotency 1h
- 5 subagents (Sonnet) параллельно через worktree isolation, 0 merge conflicts

### Session 33 highlights (02.05.2026 — corruption recurrence + auto-recovery)
- Root cause: broken pages persisted on disk, WAL TRUNCATE на graceful shutdown writes damaged data back
- `_main_session_integrity_preflight` в `_recreate_client` — auto-recovery flow
- Symmetric malformed handling в 4 Pyrogram-методах (update_usernames/peers/state/remove_state)
- sqlite3 .recover восстановило 76 peers, 31 usernames, auth_key preserved

### Session 28 highlights (27-28.04.2026)
- command_handlers.py **19637 → 4430 LOC (−77.4%)**, 18 waves total
- 11212 тестов collected
- `POST /api/inbox/bulk-ack-stale` endpoint

### Session 27 highlights (27-28.04.2026)
- Phase 2 command_handlers split: 15 waves (text_utils/chat/scheduler/voice/memory/social/ai/swarm/translator/system/admin/cli/fileio/group_admin/content)
- 10561 tests passed
- Bug fixes: mention trigger, reply_to context, TTS timeout, media filter video, REACTION_INVALID

### Session 22 highlights (25.04.2026)
- Memory Phase 2 LIVE: hybrid retrieval (FTS5 + vec_chunks RRF + MMR diversity), recall@5 +37.67
- Cron pipeline FIXED end-to-end
- MCP tool expansion: 44 tools

## Статистика тестов

| Сессия | Тестов |
|--------|--------|
| Session 22 | 9991 |
| Session 27 | 10561 passed |
| Session 28 | 11212 collected |
| Session 35 | ~10670+ |
| Session 36 | ~10738+ |
| **Session 38** | **12594 collected** |
| **Session 39 (06.05)** | **12702 collected** (+108: Wave 31 mixin tests + 3 stale fixed) |

## Wave 31 series — Bridge mixin extraction (06.05.2026)

Цель: разнести `userbot_bridge.py` (6860 LOC monolith) на cohesive mixins.

| Wave | Module | Methods | LOC delta | Bridge after |
|---|---|---|---|---|
| 31-A→D | startup_state, callback_handler, network_watchdog, translator_profile, telegram_send_utils, reaction_dispatch, _send_queue | 30+ | -589 | 6271 |
| 31-E | `cron_tasks.py` | 4 (cron prompt+context+send) | -225 | 6046 |
| 31-F | `relay_inbox.py` | 7 + `_RELAY_INTENT_KEYWORDS` | -349 | 5697 |
| 31-G | `swarm_team_clients.py` | 3 (start/stop/init) | -169 | 5528 |
| 31-H | `media_processors.py` | 3 (document/video/frame) + 3 const | -338 | 5190 |
| **31-I** | `background_loops.py` | 2 (idea_features_tick + cmd_usage_save) + skill_curator helper | **-223** | **4967** ✨ |

**Total: -1893 LOC (-27.6%)**, bridge < 5000 LOC впервые.

**KraabUserbot MRO (19 mixins):** LLMTextProcessing → RuntimeStatus → VoiceProfile → AutoTranslate → AccessControl → LLMFlow → BackgroundTasks → Session → StartupState → CallbackHandler → NetworkWatchdog → TranslatorProfile → TelegramSendUtils → ReactionDispatch → CronTask → RelayInbox → SwarmTeamClients → MediaProcessors → BackgroundLoops.

**Pattern:** factory mixin classes в `src/userbot/*Mixin.py`, MRO inheritance, lazy imports внутри hot-path methods, structlog с `module=<mixin_name>` для traceability в production logs.



## LM Studio integration

- Get token: LM Studio app → Settings → Network → API token
- Setup: `venv/bin/python scripts/setup_lm_studio_token.py <token>`
- Check: `venv/bin/python scripts/setup_lm_studio_token.py --check`

## Ссылки

- `docs/SESSION_22_FINAL_REPORT.md` — финальный отчёт сессии 22
- `docs/PHASE2_MIGRATION_GUIDE.md` — Phase 2 activation procedure
- `IMPROVEMENTS.md` — архитектурный бэклог
- `docs/MASTER_PLAN_VNEXT_RU.md` — мастер-план проекта
- `.remember/next_session.md` — handoff следующей сессии
- `docs/SMART_ROUTING_DESIGN.md` — Smart Routing spec (319 LOC)
- Memory: `~/.claude/projects/-Users-pablito-Antigravity-AGENTS-----/memory/`
