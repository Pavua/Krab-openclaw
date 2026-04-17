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
    reaction_engine.py  — движок реакций
    routing_errors.py   — ошибки routing
    runtime_policy.py   — политика runtime
    scheduler.py        — общий планировщик задач
    shared_repo_switchover.py — переключение shared-репозитория
    shared_worktree_permissions.py — права worktree
    telegram_rate_limiter.py — rate limiter для Telegram API
    auth_recovery_readiness.py — готовность к восстановлению авторизации
    exceptions.py       — кастомные исключения
  handlers/
    command_handlers.py — 175+ команд, _AgentRoomRouterAdapter
  integrations/
    tor_bridge.py       — Tor SOCKS5 proxy (httpx + Playwright)
    browser_bridge.py   — CDP подключение к Chrome
    browser_ai_provider.py — AI через браузер
    hammerspoon_bridge.py — HTTP bridge к Hammerspoon :10101
    macos_automation.py — AppleScript/osascript автоматизация
    krab_ear_client.py  — клиент KrabEar (STT диаризация)
    voice_gateway_client.py — клиент Voice Gateway
    voice_gateway_subscriber.py — подписчик Voice Gateway событий
    cli_runner.py       — запуск CLI инструментов (codex/gemini/claude)
  userbot/
    access_control.py   — ACL на уровне userbot
    auto_translate.py   — авто-перевод сообщений
    background_tasks.py — фоновые задачи userbot
    llm_flow.py         — основной LLM flow
    llm_retry.py        — retry логика LLM (session 7)
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
    web_app.py          — Owner panel FastAPI (:8080), 180+ endpoints
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
MCP Hammerspoon (8013) зарегистрирован в Claude Desktop (session 6).
Plists: `scripts/launchagents/`

## Модели и routing

Runtime truth: `~/.openclaw/agents/main/agent/models.json`

Текущий routing (12.04.2026):
- Primary: `google/gemini-3-pro-preview`
- Translator: `google/gemini-3-flash-preview` (preferred_model для скорости)
- Fallbacks: `gemini-2.5-pro-preview`, `gemini-2.5-flash`, `gemini-3-flash-preview`
- `google-antigravity` — НЕ использовать (квота/бан)
- LM Studio local — автоматический fallback при cloud-failure

## Свёрм (Multi-Agent)

Команды в Telegram: `!swarm <team> <topic>`, `!swarm teams`, `!swarm schedule`, `!swarm memory`
Session 6: `!swarm research <topic>` — глубокий веб-ресёрч; `!swarm summary` / `!swarm сводка` — сводка активностей
Session 7: `!swarm info <team>` — детали команды; `!swarm stats` — статистика; `!swarm report` — markdown отчёты

**Task Board (session 7):**
```
!swarm task board             — Kanban-доска задач
!swarm task list [team]       — список задач
!swarm task create <team> <title>
!swarm task done/fail <id>
!swarm task assign <id>
!swarm task status <id>       — детальный просмотр
!swarm task priority <id> <low|medium|high|critical>
!swarm task count             — быстрый счётчик
!swarm task clear             — cleanup done/failed
```

Teams: `traders`, `coders`, `analysts`, `creative`

Tool access: web_search, tor_fetch (если TOR_ENABLED), peekaboo, все MCP tools.
`SWARM_ROLE_MAX_OUTPUT_TOKENS` default 4096. Role context clip 3000 chars.

### Forum Topics (live broadcast)
Forum-группа: **🐝 Krab Swarm** (chat_id: `-1003703978531`)
Каждая команда пишет в свой топик. Конфиг: `~/.openclaw/krab_runtime_state/swarm_channels.json`
Setup: `!swarm setup` в группе с включёнными Topics.
Intervention: пиши в топик во время раунда — Краб подхватит как директиву.

## Виртуальное окружение

Единый venv для всего: runtime, MCP серверы, тесты.

| Путь | Python | Pyrogram | Назначение |
|------|--------|----------|-----------|
| `venv/` | 3.13 | pyrofork 2.3.69 | Runtime, MCP, тесты |

Pyrofork — форк Pyrogram с нативной поддержкой Forum Topics (`message_thread_id`),
`send_reaction()`, stories. Импорты: `from pyrogram import ...` (namespace совместим).

## Правила

- **Не дублируй нативный функционал OpenClaw** если он уже есть
- **Не SIGHUP openclaw** — только `openclaw gateway` для рестарта
- **LM Studio модели** — тестировать ONE AT A TIME (RAM overflow на 36GB M4 Max)
- **Subprocess** — всегда `env=clean_subprocess_env()` для subprocess'ов
- **Handoff** — после изменений обновляй memory и IMPROVEMENTS.md
- **Проверяй после правок**: `pytest tests/ -q`, `ruff check src/`

## Phase 7 статус (12.04.2026)

- **Phase 7: ~98%** (session 7 завершила 40%→88%, session 8 target: 100%)
- Готово: ErrorDigest, WeeklyDigest, Research Pipeline, AlertSystem, Cost Budget Alerts, TaskBoard, CommandRegistry, 175+ команд, 180+ API endpoints
- В работе (session 8): !members, !cron, !log финализация; Dashboard frontend (Gemini spec готов); Swarm listeners e2e; KrabEar диаризация

## Ссылки

- `IMPROVEMENTS.md` — архитектурный бэклог и глобальное видение
- `docs/MASTER_PLAN_VNEXT_RU.md` — мастер-план проекта
- `docs/DASHBOARD_REDESIGN_SPEC.md` — спецификация frontend-дашборда (сессия 7)
- `.remember/next_session.md` — handoff следующей сессии
- Memory: `~/.claude/projects/-Users-pablito-Antigravity-AGENTS-----/memory/`

## Накопленные команды (~175+)

```
# AI и контент
!ask <вопрос>                — AI ответ в текущем чате
!search <запрос>             — AI поиск + источники
!search --raw <запрос>       — сырые результаты Brave
!translate <текст>           — перевести текст
!summary [N]                 — суммарный recap N сообщений
!catchup                     — алиас !summary 100
!report [daily|weekly]       — AI отчёт по активности
!weather <город>             — прогноз погоды
!define <слово>              — определение слова
!urban <слово>               — Urban Dictionary
!img <prompt>                — генерация изображения
!ocr                         — распознать текст из изображения
!yt <url>                    — транскрипция YouTube
!news [тема]                 — актуальные новости
!rate <текст>                — оценить текст/идею

# Costs & FinOps
!costs                       — cost report прямо в Telegram
!budget [сумма]              — показать или установить бюджет
!digest                      — немедленный weekly digest

# Заметки и хранилище
!memo [текст]                — заметка в текущем чате
!memo list                   — список заметок
!memo del <n>                — удалить заметку
!note <текст>                — быстрая заметка
!bookmark / !bm [url]        — закладка (из reply или URL)
!bm list                     — список закладок
!bm del <n>                  — удалить закладку
!export [формат]             — экспорт заметок/закладок
!snippet [lang] <код>        — сохранить code snippet
!paste [текст]               — вставить clipboard/текст
!quote                       — цитата из reply
!template <name> [text]      — шаблон сообщения
!tag <name>                  — пометить сообщение тегом

# Анализ чата
!grep <паттерн>              — поиск по истории чата
!context [N]                 — контекст чата (N сообщений)
!monitor on/off              — мониторинг активности
!who [N]                     — топ активных участников
!fwd <chat_id>               — переслать сообщение
!collect [N]                 — собрать N последних
!top [N]                     — топ сообщений по реакциям
!history [N]                 — история чата
!chatinfo                    — информация о чате
!whois <user>                — информация о пользователе

# Сообщения и управление
!pin [тихо]                  — закрепить reply-сообщение
!unpin [all]                 — открепить сообщение
!del [N]                     — удалить N последних своих
!purge [N]                   — удалить N от любого (reply)
!autodel <sec>               — автоудаление через N сек
!schedule <time> <текст>     — отложить сообщение
!remind <time> <текст>       — напоминание
!remind list                 — список напоминаний
!remind cancel <n>           — отменить напоминание
!poll <вопрос> | <opt1> | …  — голосование
!quiz <вопрос> | <ответ>     — викторина
!dice [N]                    — бросить кубик
!typing [сек]                — эффект "печатает..."
!say <текст>                 — отправить от имени бота

# Текстовые утилиты
!calc <выражение>            — калькулятор
!b64 [enc|dec] <текст>       — Base64 кодирование
!hash [algo] <текст>         — хэш (md5/sha1/sha256)
!len / !count <текст>        — длина и количество слов
!json [pretty|compact]       — форматировать JSON
!sed s/from/to               — замена в тексте (reply)
!diff                        — diff двух текстов
!regex <паттерн> <текст>     — проверить regex
!rand [N] / !rand <a> <b>    — случайное число
!qr <текст>                  — QR-код
!convert <val> <from> <to>   — конвертация единиц
!color <hex|rgb|name>        — информация о цвете
!emoji <name|unicode>        — информация об эмодзи

# Время и сеть
!timer <время>               — таймер (1m30s, etc.)
!stopwatch start/stop/lap    — секундомер
!time [timezone]             — текущее время
!currency <сумма> <from> <to> — курс валют
!ip [адрес]                  — информация об IP
!dns <домен>                 — DNS lookup
!ping <хост>                 — ping хоста
!link <url>                  — short link / info
!uptime                      — аптайм Краба

# Социальное и модерация
!react <emoji>               — реакция на reply
!afk [причина]               — режим отсутствия
!afk off / !back             — вернуться
!afk status                  — статус AFK
!welcome on/off              — приветствие новых участников
!sticker                     — инфо о стикере
!alias <cmd> <команда>       — создать алиас команды
!alias list                  — список алиасов
!chatmute <user> [dur]       — заглушить пользователя
!slowmode [сек]              — слоумод в группе
!spam status/add/remove      — антиспам
!archive / !unarchive        — архивировать чат
!mark <read|unread>          — пометить прочитанным
!blocked                     — список заблокированных
!invite <user>               — пригласить в группу
!profile [bio|photo|name]    — управление профилем
!contacts [search]           — управление контактами
!members [search]            — участники группы
!log [N]                     — лог активности
!tts <текст>                 — text-to-speech

# Программирование и утилиты
!run <lang> <код>            — выполнить код
!eval <python>               — eval Python (owner-only)
!grep <паттерн>              — regex поиск
!encrypt / !decrypt <текст>  — шифрование текста
!report spam                 — пожаловаться на spam
!todo [add|done|list|del]    — персональный TODO
!qr <текст>                  — генерация QR-кода
!backup                      — резервное копирование данных
!hash [algo]                 — хэш-функция

# Системные (owner-only)
!health                      — расширенная диагностика
!stats                       — статистика (FinOps/Translator/Swarm)
!sysinfo                     — системная информация
!version                     — версия Краба
!model [list|switch|info]    — управление моделью
!model switch <model>        — сменить модель
!reasoning [low|medium|high] — уровень reasoning
!config [key] [value]        — просмотр/изменение конфигурации
!set <key> <value>           — быстрый set config
!scope [scope]               — управление scopes OpenClaw
!acl [allow|deny] <user>     — управление ACL
!notify [on|off|status]      — управление уведомлениями
!restart                     — перезапуск Краба
!debug [on|off|trace]        — режим отладки
!diagnose                    — диагностика всей экосистемы
!agent <prompt>              — прямой вызов AI агента
!context [clear|save]        — управление контекстом OpenClaw
!cronstatus                  — статус cron-задач
!cron list/add/remove/toggle — управление cron
!health                      — комплексный health-check
!panel                       — URL owner panel
!browser [status|tabs]       — состояние браузера
!macos <команда>             — macOS автоматизация
!hs <команда>                — Hammerspoon bridge
!codex / !gemini / !claude   — CLI AI инструменты
!inbox [list|update]         — управление inbox
!role <role>                 — сменить роль агента
!chatban [ban|unban|list]    — бан в чате
!silence [on|off|status]     — режим тишины
!costs                       — FinOps отчёт
!budget [сумма]              — бюджет
!digest                      — дайджест

# Translator (full suite)
!translator status            — статус переводчика
!translator on / off          — включить/выключить
!translator lang <es-ru|…>   — пара языков
!translator auto              — авто-определение языка
!translator mode <bilingual|auto_to_ru|auto_to_en>
!translator strategy <voice-first|subtitles-first>
!translator ordinary <on|off>
!translator internet <on|off>
!translator subtitles|timeline|summary|diagnostics <on|off>
!translator phrase add/remove — кастомные фразы
!translator reset             — сброс настроек
!translator test <текст>      — быстрый тест перевода
!translator history           — статистика переводов
!translator help              — список субкоманд
!translator session status/start/pause/resume/stop/mute/unmute/replay/clear

# Voice
!voice on|off|toggle         — голосовой режим
!voice speed <0.75..2.5>     — скорость речи
!voice voice <edge-tts-id>   — выбор голоса
!voice delivery <text+voice|voice-only>
!voice block <chat_id>       — заблокировать чат для голоса
!voice unblock <chat_id>     — разблокировать
!voice blocked               — список заблокированных
!voice reset                 — сброс голосовых настроек

# Swarm
!swarm <team> <задача>       — запустить агентную сессию
!swarm teams                 — список команд
!swarm research <topic>      — глубокий веб-ресёрч
!swarm summary / !swarm сводка — сводка активностей
!swarm info <team>           — детали команды
!swarm stats                 — статистика по всем командам
!swarm report                — просмотр markdown отчётов
!swarm setup                 — настройка Forum Topics
!swarm schedule [add|list]   — рекуррентный планировщик
!swarm memory [team]         — память свёрма
!swarm task board            — Kanban-доска
!swarm task list [team]      — список задач
!swarm task create <team> <title>
!swarm task done|fail <id>
!swarm task assign <id>
!swarm task status <id>
!swarm task priority <id> <level>
!swarm task count
!swarm task clear

# Search & Web
!search <запрос>             — AI-режим поиска
!search --raw <запрос>       — сырые результаты
!web login/screen/gpt        — браузерный контроль
!shop <запрос>               — поиск в Mercadona

# Files & Memory
!ls [path]                   — список файлов
!read <path>                 — прочитать файл
!write <path> <content>      — записать файл
!remember <key> <value>      — сохранить в память
!recall <key>                — прочитать из памяти
```

## Owner Panel API (актуально на 12.04.2026)

Endpoints session 6 (добавлены):

| Endpoint | Метод | Описание |
|----------|-------|----------|
| `/api/costs/budget` | GET/POST | Просмотр и установка бюджета расходов |
| `/api/costs/history` | GET | История расходов по провайдерам |
| `/api/thinking/status` | GET | Статус режима thinking (extended reasoning) |
| `/api/thinking/set` | POST | Включить/выключить thinking |
| `/api/depth/status` | GET | Текущий уровень глубины reasoning |

Endpoints session 7 (добавлены, ~180+ итого):

| Endpoint | Метод | Описание |
|----------|-------|----------|
| `/api/commands` | GET | Реестр команд с метаданными |
| `/api/commands/{name}` | GET | Детальная инфо о команде |
| `/api/version` | GET | Версия и данные сессии |
| `/api/uptime` | GET | Аптайм в секундах |
| `/api/system/info` | GET | Системная информация хоста |
| `/api/endpoints` | GET | Self-documenting список endpoints |
| `/api/v1/health` | GET | Версионированный health (внешние мониторы) |
| `/api/voice/toggle` | POST | Переключить голосовой режим |
| `/api/voice/profile` | GET | Голосовой профиль |
| `/api/voice/runtime` | GET/POST | Runtime голосовых настроек |
| `/api/translator/auto` | POST | Авто-определение языка |
| `/api/translator/lang` | POST | Смена пары языков |
| `/api/translator/test` | GET | Быстрый тест перевода |
| `/api/translator/languages` | GET | Поддерживаемые языки |
| `/api/translator/readiness` | GET | Готовность переводчика |
| `/api/translator/control-plane` | GET | Control plane |
| `/api/translator/session-inspector` | GET | Инспектор сессии |
| `/api/translator/mobile-readiness` | GET | Мобильная готовность |
| `/api/translator/delivery-matrix` | GET | Матрица доставки |
| `/api/translator/live-trial-preflight` | GET | Preflight live-trial |
| `/api/translator/mobile/onboarding` | GET | Онбординг мобильный |
| `/api/translator/bootstrap` | GET | Bootstrap данные |
| `/api/swarm/task-board` | GET | Kanban-доска задач |
| `/api/swarm/task-board/export?format=csv\|json` | GET | Export task board |
| `/api/swarm/tasks` | GET | Список задач свёрма |
| `/api/swarm/task/{id}` | GET | Детальная задача |
| `/api/swarm/tasks/create` | POST | Создать задачу |
| `/api/swarm/task/{id}/update` | POST | Обновить статус |
| `/api/swarm/task/{id}/priority` | POST | Сменить приоритет |
| `/api/swarm/task/{id}` | DELETE | Удалить задачу |
| `/api/swarm/team/{name}` | GET | Детальная инфо о команде |
| `/api/swarm/teams` | GET | Список команд |
| `/api/swarm/stats` | GET | Статистика board+artifacts+listeners |
| `/api/swarm/reports` | GET | Markdown-отчёты |
| `/api/swarm/artifacts` | GET | Артефакты свёрма |
| `/api/swarm/artifacts/cleanup` | POST | Очистка старых артефактов |
| `/api/swarm/listeners` | GET | Статус слушателей команд |
| `/api/swarm/listeners/toggle` | POST | Управление слушателями |
| `/api/model/switch` | POST | Сменить модель |
| `/api/model/status` | GET | Статус модели |
| `/api/model/recommend` | GET | Рекомендация модели |
| `/api/model/preflight` | POST | Preflight проверка модели |
| `/api/model/local/status` | GET | Статус LM Studio |
| `/api/model/local/load-default` | POST | Загрузить LM Studio модель |
| `/api/model/local/unload` | POST | Выгрузить LM Studio модель |
| `/api/model/explain` | GET | Объяснение выбора модели |
| `/api/model/catalog` | GET | Каталог моделей |
| `/api/model/apply` | POST | Применить конфигурацию модели |
| `/api/model/feedback` | GET/POST | Feedback по модели |
| `/api/model/provider-action` | POST | Действия с провайдером |
| `/api/silence/status` | GET | Статус тишины |
| `/api/silence/toggle` | POST | Переключить режим тишины |
| `/api/notify/status` | GET | Статус уведомлений |
| `/api/notify/toggle` | POST | Переключить уведомления |
| `/api/runtime/recover` | POST | Восстановить runtime |
| `/api/runtime/chat-session/clear` | POST | Очистить сессию чата |
| `/api/runtime/operator-profile` | GET | Профиль оператора |
| `/api/runtime/repair-active-shared-permissions` | POST | Починить permissions |
| `/api/context/checkpoint` | POST | Сохранить checkpoint контекста |
| `/api/context/transition-pack` | POST | Transition pack контекста |
| `/api/context/latest` | GET | Последний контекст |
| `/api/ecosystem/health` | GET | Здоровье экосистемы |
| `/api/ecosystem/health/export` | GET | Экспорт health |
| `/api/ecosystem/capabilities` | GET | Возможности экосистемы |
| `/api/system/diagnostics` | GET | Диагностика системы |
| `/api/ops/diagnostics` | GET | Ops диагностика |
| `/api/ops/metrics` | GET | Метрики |
| `/api/ops/timeline` | GET | Timeline событий |
| `/api/sla` | GET | SLA метрики |
| `/api/ops/runtime_snapshot` | GET | Runtime snapshot |
| `/api/ops/models` | POST | Управление моделями |
| `/api/ops/usage` | GET | Использование |
| `/api/ops/cost-report` | GET | Cost report |
| `/api/ops/runway` | GET | Runway бюджета |
| `/api/ops/executive-summary` | GET | Executive summary |
| `/api/ops/report` | GET | Ops отчёт |
| `/api/ops/report/export` | GET | Экспорт отчёта |
| `/api/ops/bundle` | GET | Bundle данных |
| `/api/ops/bundle/export` | GET | Экспорт bundle |
| `/api/ops/alerts` | GET | Активные алерты |
| `/api/ops/history` | GET | История ops |
| `/api/ops/maintenance/prune` | POST | Очистка данных |
| `/api/ops/ack/{code}` | POST/DELETE | Подтвердить/снять alert |
| `/api/openclaw/cron/status` | GET | Статус cron |
| `/api/openclaw/cron/jobs` | GET | Список cron jobs |
| `/api/openclaw/cron/jobs/create` | POST | Создать cron job |
| `/api/openclaw/cron/jobs/toggle` | POST | Вкл/выкл cron job |
| `/api/openclaw/cron/jobs/remove` | POST | Удалить cron job |
| `/api/openclaw/channels/status` | GET | Статус каналов |
| `/api/openclaw/channels/runtime-repair` | POST | Починить каналы |
| `/api/openclaw/channels/signal-guard-run` | POST | Запустить signal guard |
| `/api/openclaw/runtime-config` | GET | Runtime конфигурация |
| `/api/openclaw/report` | GET | Отчёт OpenClaw |
| `/api/openclaw/deep-check` | GET | Глубокая проверка |
| `/api/openclaw/remediation-plan` | GET | План исправлений |
| `/api/openclaw/browser-smoke` | GET | Smoke-тест браузера |
| `/api/openclaw/browser/start` | POST | Запустить браузер |
| `/api/openclaw/browser/open-owner-chrome` | POST | Открыть Owner Chrome |
| `/api/openclaw/browser-mcp-readiness` | GET | Browser MCP готовность |
| `/api/openclaw/photo-smoke` | GET | Smoke-тест фото |
| `/api/openclaw/cloud` | GET | Cloud статус |
| `/api/openclaw/cloud/diagnostics` | GET | Cloud диагностика |
| `/api/openclaw/cloud/runtime-check` | GET | Cloud runtime проверка |
| `/api/openclaw/cloud/switch-tier` | POST | Сменить cloud tier |
| `/api/openclaw/cloud/tier/state` | GET | Состояние cloud tier |
| `/api/openclaw/cloud/tier/reset` | POST | Сброс cloud tier |
| `/api/openclaw/model-routing/status` | GET | Статус routing |
| `/api/openclaw/model-autoswitch/status` | GET | Авто-переключение |
| `/api/openclaw/model-autoswitch/apply` | POST | Применить авто-переключение |
| `/api/openclaw/control-compat/status` | GET | Совместимость control |
| `/api/openclaw/routing/effective` | GET | Эффективный routing |
| `/api/openclaw/model-compat/probe` | GET | Probe совместимости модели |
| `/api/assistant/query` | POST | Запрос к AI ассистенту |
| `/api/assistant/attachment` | POST | Прикрепить файл к запросу |
| `/api/assistant/capabilities` | GET | Возможности ассистента |
| `/api/diagnostics/smoke` | POST | Smoke-тест диагностики |
| `/api/inbox/status` | GET | Статус inbox |
| `/api/inbox/items` | GET | Элементы inbox |
| `/api/inbox/update` | POST | Обновить элемент inbox |
| `/api/inbox/stale-processing` | GET | Зависшие в processing |
| `/api/inbox/stale-open` | GET | Зависшие open |
| `/api/inbox/stale-processing/remediate` | POST | Исправить processing |
| `/api/inbox/stale-open/remediate` | POST | Исправить open |
| `/api/inbox/create` | POST | Создать inbox item |
| `/api/provisioning/templates` | GET | Шаблоны provisioning |
| `/api/provisioning/drafts` | GET/POST | Черновики provisioning |
| `/api/provisioning/preview/{id}` | GET | Preview черновика |
| `/api/provisioning/apply/{id}` | POST | Применить черновик |
| `/api/capabilities/registry` | GET | Реестр возможностей |
| `/api/channels/capabilities` | GET | Возможности каналов |
| `/api/userbot/acl/status` | GET | Статус ACL |
| `/api/userbot/acl/update` | POST | Обновить ACL |
| `/api/policy` | GET | Политика |
| `/api/policy/matrix` | GET | Матрица политик |
| `/api/queue` | GET | Очередь задач |
| `/api/ctx` | GET | Контекст чата |
| `/api/reactions/stats` | GET | Статистика реакций |
| `/api/mood/{chat_id}` | GET | Настроение чата |
| `/api/links` | GET | Ссылки |

## Статистика тестов

| Сессия | Тестов |
|--------|--------|
| Session 5 | 2071 |
| Session 6 | 3633 |
| Session 7 | ~6826+ |
