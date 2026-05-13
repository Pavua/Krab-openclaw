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

# Pre-commit (Wave 55-A): после клонирования/первого раза
bash scripts/install_pre_commit.sh
# Hook автоматически: ruff check --fix + ruff format при каждом git commit
```

## Auto-generated reference

- **Endpoints** (~380 routes): [docs/CLAUDE_AUTO_ENDPOINTS.md](docs/CLAUDE_AUTO_ENDPOINTS.md)
- **Handlers** (~181 функций): [docs/CLAUDE_AUTO_HANDLERS.md](docs/CLAUDE_AUTO_HANDLERS.md)
- **Commands** (~162 registered, 172+ с алиасами): [docs/CLAUDE_COMMANDS_REFERENCE.md](docs/CLAUDE_COMMANDS_REFERENCE.md)
- **Prometheus** (42 alerts, 52 metrics): [docs/CLAUDE_AUTO_PROMETHEUS.md](docs/CLAUDE_AUTO_PROMETHEUS.md)
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
    state_snapshots.py  — periodic backups of critical state files (Wave 49-F, session 44)
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
    route_switch_log.py — log model fallback switches (Wave 48-B, session 44)
  bootstrap/
    session_recovery.py — shared recovery module: attempt_recovery/has_recent_recovery_backup (session 35, Wave 16-N)
  scripts/
    openclaw_runtime_repair.py — recovery chain: validate+probe+sqlite .recover (session 35, Wave 16-J)
    agent_tools/krab_*.py      — bash-callable github/cloudflare/sentry/brave для codex agent (Wave 45-C, session 44)
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
    message_catchup.py  — startup catchup для lost messages (Wave 46-A, session 44)
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
| Backup retention sweep (Wave 172) | — | `ai.krab.backup-retention` (daily 03:00) |

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
KRAB_MODEL_FOOTER_ENABLED=1          # 📡 model footer per response (Wave 47-B)
KRAB_STARTUP_CATCHUP_LIMIT=20        # startup history catchup messages (Wave 46-A)
KRAB_STARTUP_CATCHUP_CHATS=          # extra chat IDs to catch up (Wave 48-A)
KRAB_SWARM_GROUP_ID=-1003703978531   # Krab Swarm forum group (Wave 48-A)
KRAB_STATE_SNAPSHOT_INTERVAL_MINUTES=60  # state snapshot interval (Wave 49-F)
KRAB_LONG_CONTEXT_PROVIDER=cloud         # "mlx-local-kv4" → route long-context to local MLX :8088 (Wave 223, opt-in)
MLX_LOCAL_KV4_URL=http://127.0.0.1:8088  # local MLX endpoint override (Wave 223)
KRAB_LONG_CONTEXT_THRESHOLD_TOKENS=8000  # prompt_tokens > N → local (Wave 223)
KRAB_MLX_LOCAL_TASK_TYPES=summarization,rag_retrieval  # task_type whitelist для local (Wave 223)
KRAB_HEARTBEAT_GET_STATE_PROBE_ENABLED=1 # GetState pts probe split-brain (Wave 63-A)
KRAB_HEARTBEAT_GET_STATE_TIMEOUT_SEC=8.0  # GetState invoke timeout (Wave 63-A)
LOCAL_AUTOLOAD_FALLBACK_LIMIT=0      # 0=strict preferred (Wave 62 — было zombie env)
KRAB_ANTHROPIC_VERTEX_DISABLED_MODELS=claude-sonnet-4-5  # preempt no-quota (Wave 65-D)
KRAB_COEXIST_SWAP_WARN_GB=22         # swap warn threshold (Wave 65-E, logged only)
KRAB_COEXIST_SWAP_THRESHOLD_GB=32    # swap critical (Wave 65-E, Telegram alert)
KRAB_GEMINI_RERANK_VERTEX_ENABLED=1    # rerank через Vertex (Wave 66-A)
KRAB_GOOGLE_DIRECT_VERTEX_PREFERRED=1  # google_direct Vertex mode (Wave 66-B)
GEMINI_PAID_KEY_ENABLED=0              # safety belt (Wave 66-B)
KRAB_VERTEX_PROJECT=caramel-anvil-492816-t5  # bonus credits project
KRAB_VERTEX_REGION=global              # Vertex location
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
- **15141/15264 тестов collected** (Session 48, 13.05.2026)
- **318 API endpoints** (live: `/api/endpoints`), 181 handlers, 162 registered commands (172+ с алиасами), 42 alerts / 52 metrics

## Session highlights (последние)

### Session 45 highlights (2026-05-11 → 12 — Waves 62-65, 17 commits, self-healing milestones)

Branch `main`. Все изменения deployed в production. **Самый результативный тур.**

**3 paradigm shifts** — все на pattern «не верить health monitor если outcome stuck»:
- **Wave 64** (commit `4f279cc`) — `journal_mode=WAL → DELETE` + `PRAGMA fullfsync=1` для Pyrogram session.db. Фикс recurring SQLite corruption cluster (Linear AGE-15/12/9 — 3 кластера/2 недели). `_REQUIRED_TABLES += "version"`. Migration автоматическая при `_patched_open`. 22 новых теста.
- **Wave 63-A** (commit `145d6a9`) — `updates.GetState` pts probe + drop 10-min gate. Ловит split-brain detection с 93 мин → **4 мин**. Server pts advanced vs `_last_seen_update_id` frozen = immediate `_try_reconnect_pyrofork`. Env gate `KRAB_HEARTBEAT_GET_STATE_PROBE_ENABLED` (default ON). 21 новый тест.
- **Wave 50-B** (commit `cba58cf`) — OAuth force-refresh через Google endpoint при `expiry_in_min < -60`. Daemon `sync_gemini_oauth_to_openclaw.py` теперь не просто mirror'ит, а реально refresh'ит. Verified live: -1492 → 60 min fresh.

**Wave 62 series (7 commits)** — routing + Sentry hygiene:
- **62-C** (`a62e311`): `is_owner_dm` via ACL — Wave 60-A wiring complete (читает ACL вместо force_cloud proxy)
- **62-D** (`739b8f2`): cloud routing decision bypass local-first heuristic (owner_dm → cloud действительно идёт в cloud)
- **62-E** (`83d0544`): `gemini_rerank_provider`: `gemini-3-pro-preview` → `gemini-2.5-pro` (AI Studio v1beta path). **-9 Sentry events/day** (PYTHON-FASTAPI-7M).
- **62-F** (`7546573`): Sentry benign markers — `load_failed lmstudio`, `codex_quota_exhausted` filtered
- **62-G** (`2c7dc4d`): codex preempt при weekly quota — `is_codex_disabled()` теперь читается в hot path (был dead-letter с Wave 44-V). Save 2-3s/request пока quota не recover. Wave 62-H (`a140ee6`): footer cosmetic "codex weekly quota" вместо "сбоя primary".
- AGE-8 cherry-pick (`d9ba689`): `memory_doctor.run_repairs` regression test (sentinel concurrent task verify event loop не блокируется)

**Wave 65 series (5 commits)** — operational + UX polish:
- **65-A** (`9cbb61d`): `leak_monitor` Chrome filter — 20 false-positives → 1 real (gateway). Chrome browser-bridge OpenClaw spawns matched substring "openclaw" в `--user-data-dir`.
- **65-B** (`9cbb61d`): `nightly-audit` `RunAtLoad=true` — catch-up missed nights после macbook sleep
- **65-C** (`49e6afc`): swarm DM bots распознают owner sender (AGE-16). `build_role_system_prompt(sender=...)` + `is_owner_user_id(sender.id)` injection. **Verified live**: Coders ответил «Создатель» вместо «не могу идентифицировать».
- **65-D** (`148bef9`): `anthropic-vertex/claude-sonnet-4-5` preempt (no quota в GCP project caramel-anvil). **-7 Sentry events/day**. Env override `KRAB_ANTHROPIC_VERTEX_DISABLED_MODELS`.
- **65-E** (`870d36e`): two-tier swap thresholds — `SWAP_WARN_THRESHOLD=22 GB` (logged only) + `SWAP_THRESHOLD=32 GB` (Telegram alert). **-88% Telegram noise** (515/week → ~60/week).

**Operational changes (non-code, 4 шт)**:
- `~/.codex/config.toml`: MCP context7 `type="streamable_http"` discriminator — 5 OpenClaw cron jobs работают снова (8 days down)
- `kraab.session` manually migrated `wal → delete`, fullfsync=1 (500 peers preserved)
- Inbox 40 stale items bulk-acked через `/api/inbox/bulk-ack-stale`
- 23 corrupt session backups archived в `/tmp/krab_session_corrupt_archive_20260511/`

**Linear (7 issues closed)**: AGE-5/6/8/15/12/9/16 → Done

**Sentry quota saved**: **>1000 events/week** stop firing (gemini_rerank -9/day + sonnet-4-5 -7/day + swap_critical -88% + benign markers).

**Architecture milestone — «Outcomes, not heartbeats» pattern**:
1. Wave 63-A: detect-and-recover split-brain (server pts vs local)
2. Wave 50-B: pre-empt-and-refresh OAuth (don't trust "already synced" flag)
3. Wave 65-D: pre-empt before failing call (know model unavailability upfront)
4. Wave 62-G: pre-empt codex weekly quota (read state file, skip subprocess)

Все используют один принцип: **check outcomes, not process aliveness**.

**Background agents used (11+ parallel)** — Sonnet only (Haiku context window не справился). Sentry triage / Linear / log scan / memory pressure / routines / AGE-15 research / AGE-8 fix / Wave 63-A / Wave 64 / cron jobs / Wave 65-C / Wave 65-F/G/H.

**Wave 66 series (2 commits + 1 .env edit)** — emergency billing leak fix:
* **66-A** (`1a1dc39`): `gemini_rerank_provider` Vertex mode preferred (was per-message paid AI Studio → €40/week). Fallback на AI Studio if Vertex unavailable.
* **66-B** (`8f73871`): `google_genai_direct` (Wave 18-B) Vertex mode preferred (Gemma excluded — Vertex doesn't have). Plus `.env`: `GEMINI_PAID_KEY_ENABLED=1 → 0` safety belt.
* Env: `KRAB_GEMINI_RERANK_VERTEX_ENABLED=1` (default ON), `KRAB_GOOGLE_DIRECT_VERTEX_PREFERRED=1` (default ON).
* Эффект: future Gemini traffic полностью через caramel-anvil-492816-t5 bonus credits (€848 до 2027-03), no paid AI Studio leaks.

### Session 43+44 highlights (2026-05-09 → 2026-05-10 — Waves 44-Z, 45-*, 46-*, 47, 48-*, 49-*)

- **Wave 44-Z merge** — Tor MCP server (был забыт в S42 close)
- **Wave 45 series**: 8 MCPs registered (4 → 10): context7, firecrawl, github, sentry, tor-full, osint-tools
- **Wave 45-G CI**: Python CI workflow (was red since 2026-04-07, now 🟢)
- **Wave 46 series**: catchup mechanism для lost messages, owner auth prompt, NLU tighten
- **Wave 47**: extended fallback chain + 📡 model footer per response
- **Wave 48 series**: multi-chat catchup, !routes detailed visibility
- **Wave 49 series**: ruff cleanup scripts/, HexStrike isolated venv (151 tools), !replay command, state snapshots scheduled (24/keep, 7d/age)
- **Wave 50 series**: post-sleep reinit fix, krab-tor deprecation (in progress)

**MCPs total**: 11 (context7/firecrawl/github/sentry/krab-hammerspoon/krab-telegram/krab-telegram-owner/krab-tor-deprecated/tor-full/osint-tools/hexstrike-ai-manual)
**Tests added**: ~377 across 18 commits
**CI**: 🟢 every push (~10s, ruff + pytest unit subset)

### Session 41 highlights (09.05.2026 — Waves 37 → 41-O, 6 commits, ~115 new tests)

Branch `main`. Все изменения deployed в production через Stop+Start Krab.command.

**Wave 37 (commit `ab4430f`)** — heartbeat + reply target + tech-metaphors:
- 37-A: `_telegram_heartbeat_loop` graceful `_try_reconnect_pyrofork` на 1-st
  fail (раньше threshold=3 / ~12 мин). Разделение `_last_telegram_event_ts`
  vs новый `_last_heartbeat_ok_ts` — фикс split-brain detection.
- 37-B: `_query_has_anaphora` (RU+EN) + `_resolve_reply_target` в
  `delivery_helpers.py`. Plus anaphora hint в `build_segmented_prompt`.
- 37-C: tech-metaphors restraint (SSH/OAuth/ports) в casual chats.

**Wave 38 (commit `b92ec45`)** — `_inject_user_mention_link` для users без
@username. Markdown `[name](tg://user?id=N)` rendered Pyrofork как clickable
mention.

**Wave 39 (commit `6bb5c41`)** — 4 sub-waves в parallel sub-agents:
- 39-X: `_resolve_reply_target_from_output` парсит начало LLM ответа,
  matches против referenced.from_user. Regression case 09.05 02:14 в YMB.
- 39-A: `src/core/repetition_guard.py` — token Jaccard (0.6, 600s window).
- 39-C: `_AUTO_SWEEP_KINDS` += `"owner_mention"`. **Verified live**:
  `swept=5` на restart.
- 39-D: `_last_seen_update_id` + `_probe_updates_flow_alive` — true
  split-brain detection.

**Wave 40 (commit `7dbfc4e`)**:
- 40-S: `User-Agent: krab-mcp/1.0` + retry в Sentry MCP. **Verified**:
  `krab_sentry_status` returns full data.
- 40-T: ironic + compound mention patterns в `trigger_detector.py`.

**Wave 41 (commit `bd78bcd`)** — LM Studio singleton httpx client lifecycle:
`is_lm_studio_available` проверяет `client.is_closed` → fallback на per-call.

**Wave 41-O (commit `5faad4d`)** — openclaw 500 → `logger.warning` (Sentry
hygiene, eliminate ~50 events/день spam). 4xx остаются errors.

**Параллелизация**: 6 sub-agents (sonnet) + 2 haiku (failed "prompt too
long"). Sonnet 6/6, haiku 0/2 — context window haiku недостаточен.
Empirical rule: sonnet — 200-300 word с TDD; haiku — < 100 word.

**Tests added: ~115**. **Project separation guide**:
`docs/PROJECT_SEPARATION_GUIDE.md` (commit `5805465`).

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
| **Session 40 (07.05)** | **+30 SkillCurator analyzer + e2e fixes** (см. ниже) |
| **Session 41 (09.05)** | **~12817 collected** (+115: Wave 37-41-O) |
| **Session 43+44 (10.05)** | **13795/13916 collected** (+~377: Waves 44-Z, 45-*, 46-*, 47, 48-*, 49-*) |
| **Session 45 (11→12.05)** | **~13920+ collected** (+~120 tests: Waves 62-66 + AGE-8/15/12/9/16 + 50-B). **25 commits** (incl. Wave 66 billing leak fix), **7 Linear issues closed**, **>1000 Sentry events/week** stop firing. 3 paradigm shifts: «outcomes-not-heartbeats» pattern (Wave 63-A/50-B/65-D/62-G). Wave 66: €40/week paid AI Studio leak → Vertex bonus credits. |
| **Session 47+48 (13.05)** | **15141/15264 collected** (+1346 from S44 baseline; admin pages add, autotables refresh — Wave 168) |

### Session 40 highlights (07.05.2026 — runtime e2e + KE deadlock fix + ecosystem health)

Branch `claude/naughty-ellis-f5a58e`, ~12 commits + Krab Ear repo (отдельный) `761bd5b`.

**Killer bug:** Krab Ear menu bar/hotkey/window broken → root cause `Process+Pipe+waitUntilExit`
deadlock in `SingleInstanceGuard.swift:defaultPsRunner`. `ps -axo pid,command` пишет 174KB
на dev-машине, pipe buffer ~16KB → ps blocks on write → `waitUntilExit` hangs forever →
`applicationDidFinishLaunching` blocked → нет `NSStatusBar.statusItem`. Fix: drain pipe
ПЕРЕД `waitUntilExit`. Same root cause затрагивал KRAB-EAR-AGENT-E NSAlert hang.

**Sentry housekeeping:**
- 5 issues resolved (PYTHON-FASTAPI-Z 387 events + 4 pytest-leak: 83/84/85/63)
- `_detect_git_release()` → `release: krab@<git-sha>` для auto-close on `Fixes:` PR
- `_is_pytest_event()` filter дропает testserver URL + pytest-of- paths из prod проекта

**Swarm group "🐝 Krab Swarm" production-ready:**
- `swarm_channels.json` затёрт тестами до placeholders → restored + conftest guard (4-я persistent state leak path после Session 39)
- 4 team accounts privacy `ChatInvite/Forwards` → `AllowAll` (главное для Coders где было `DisallowAll`)
- E2E подтверждён: все 4 команды отвечают в группе
- `docs/USER_GUIDE.md` (294 LOC) + pinned summary в группе

**Krab Ear ecosystem:**
- `start_krab.command`: `ensure_krab_ear_launchd_loaded()` — auto-bootstrap LaunchAgents после Stop (раньше каждый restart Krab ломал KE)
- LM Studio: `gemma-4-e4b-it-mlx` loaded → LLM postprocess работает (97→56 chars rewrites verified live)
- Backend → settings.json `llm_model = gemma-4-e4b-it-mlx`
- `KRAB_MCP_APPLE_WRITE_ENABLED=1` в .env → Reminders/Calendar/Notes write enabled
- KE auto_glossary.json: removed 21 corrupt hallucinated entries + added 30 domain terms (Krab/swarm/codex-cli/Whisper/MLX/Quironsalud/etc)

**SkillCurator Step 2 (Wave 14-I follow-up):** новый module `src/core/skill_curator_analyzer.py` —
LLM analyzer читает swarm_artifacts/ + текущий prompt → предлагает 3-5 улучшений per team
(clarity / structure / delegation / format). CLI `scripts/skill_curator_analyze.py`,
30 tests pass, markdown reports в `~/.openclaw/krab_runtime_state/skill_curator_reports/`.

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

**KraabUserbot MRO (23 mixins):** LLMTextProcessing → RuntimeStatus → VoiceProfile → AutoTranslate → AccessControl → LLMFlow → BackgroundTasks → Session → StartupState → CallbackHandler → NetworkWatchdog → TranslatorProfile → TelegramSendUtils → ReactionDispatch → CronTask → RelayInbox → SwarmTeamClients → MediaProcessors → BackgroundLoops → VoiceHandlers → ProactiveWatch → ServiceOrchestration → DeliveryHelpers.

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
