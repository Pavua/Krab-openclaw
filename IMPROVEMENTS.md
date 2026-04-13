# Краб — Архитектурный бэклог и задачи

> Составлен: 2026-03-23 | Обновлён: 2026-04-13 (session 7)
> Статус: Активная разработка
> Владелец: По

---

## 📋 Session 7 (2026-04-12–13)

> **91 коммит** | ~2508 новых тестов (3633→~6141) | **~130 фич** | ~50 параллельных агентов

### Статистика

| Метрика | Session 6 | Session 7 |
|---------|-----------|-----------|
| Коммиты | 9 | 91 |
| Новых тестов | +1562 | +2508 |
| Всего тестов | 3633 | ~6141 |
| Новых фич | ~20 | ~130 |
| Phase 7 готовность | 40% | **88%** |

---

### Bugfixes (session 7)
- **`_current_runtime_primary_model` AttributeError** (`userbot_bridge.py`, `runtime_status.py`) — re-export из `llm_flow` после mixin decomposition; voice messages крашились
- **`/api/inbox/items?status=all`** (`inbox_service.py`) — `status="all"` ошибочно фильтровал всё вместо отключения фильтра
- **`!translator on/off/status`** (`command_handlers.py`) — добавлены shorthand алиасы (on→session start, off→session stop, status→session status)
- **Voice AttributeError ×2** — mixin decomposition, re-export после рефакторинга userbot_bridge
- **SQLite stale locks** для swarm listeners — race condition при параллельных запросах
- **iMessage спам** — OpenClaw background job отключён (спамил папе)
- **Nightly Self-Diagnostics channel** — fix канала для ночных диагностик
- **f-string syntax errors** — исправлены для совместимости Python 3.12+
- **Missing handler exports** в `__init__.py` — добавлены недостающие re-export'ы

---

### Phase 7 — Backend Service Workflows

- **FinOps поля в `/api/costs/report`** (`web_app.py`) — добавлены `total_tool_calls`, `total_fallbacks`, `total_context_tokens`, `avg_context_tokens`, `by_channel` из `cost_analytics`
- **WeeklyDigest Telegram delivery** (`weekly_digest.py`, `userbot_bridge.py`) — callback pattern через `_send_proactive_watch_alert`, auto-запуск loop в `_ensure_proactive_watch_started`
- **Cost budget alert** (`proactive_watch.py`) — >80% warning, >100% error; dedupe по месяцу; интегрирован в `run_alert_checks()` (каждые 30 мин)
- **Translator latency opt** (`translator_engine.py`) — `max_output_tokens` 2048→512, pre-clear session для снижения overhead
- **Streaming UI config** (`config.py`, `userbot_bridge.py`) — explicit `TELEGRAM_STREAM_UPDATE_INTERVAL_SEC` (2.0s), `OPENCLAW_TOOL_PROGRESS_POLL_SEC` (3.0s), model hint в initial ack
- **Research Pipeline модуль** (`src/core/swarm_research_pipeline.py`) — отдельный модуль для deep-research через analysts-команду
- **Auto-Dispatch workflow_type** — `standard` / `research` / `report` в swarm scheduler
- **LLM error auto-retry** — автоматический retry при LLM-ошибках в основном потоке
- **Autonomous swarm DM handlers** — команды свёрма в DM от team-аккаунтов

---

### CommandRegistry — Центральный реестр команд

- **`src/core/command_registry.py`** (новый модуль) — единый реестр ~55 команд с полями `name`, `category`, `description`, `owner_only`, `aliases`, `usage`
- **`GET /api/commands`** — полный реестр с `total` / `categories` (обновлён `web_app.py`)
- **`GET /api/commands/{name}`** — детальная информация, поиск по алиасу, 404 если нет
- **`handle_help`** генерируется из registry (не hardcoded), пагинация сохранена
- Тесты: `test_command_registry.py` (48), `test_web_api_commands_registry.py` (37), `test_help_command.py` (251 lines)

---

### !timer и !stopwatch — новые команды времени

- **`!timer <время> [метка]`** — таймер через `asyncio.create_task` + `asyncio.sleep`; `_parse_duration()` принимает `5m`, `1h30m`, `90s`, `3600`; субкоманды: `list`, `cancel [id]`
- **`!stopwatch`** — секундомер через `time.monotonic()`; субкоманды: `start`, `stop`, `lap`, `status`
- 36 тестов в `test_timer_stopwatch_commands.py`

---

### !remind — Natural Language парсинг

- **`split_reminder_input`** — форматы: `me in Nm текст`, `in Nm текст`, `in N minutes/hours/days/seconds текст`, `at HH:MM текст`, `tomorrow/завтра HH:MM текст`, `N минут текст`
- **`parse_due_time`** — реализованы паттерны: `tomorrow/завтра`, `in Nm`, `in N minutes/hours/days/seconds`, `N минут` (рус. короткая форма без «через»)
- **Субкоманды `!remind list`** и **`!remind cancel <id>`** — управление активными напоминаниями
- Справка с примерами всех форматов; hint «Отменить: !remind cancel <id>»
- +39 тест-кейсов (итого 61 тест в `test_scheduler.py`)

---

### Новые REST endpoints (Owner Panel)

| Endpoint | Метод | Описание |
|----------|-------|---------|
| `/api/commands` | GET | Полный реестр команд из CommandRegistry |
| `/api/commands/{name}` | GET | Детальная информация о команде, поиск по алиасу |
| `/api/uptime` | GET | Аптайм Krab в секундах |
| `/api/version` | GET | Версия Krab и информация о сессии |
| `/api/system/info` | GET | Информация о хост-системе (CPU, RAM, disk) |
| `/api/endpoints` | GET | Self-documenting список всех API endpoint'ов |

---

### Новые Telegram команды (~100 новых)

#### AI & LLM
`!ask`, `!translate`, `!summary`, `!catchup`, `!report`, `!weather`, `!define`, `!img`, `!ocr`, `!urban`, `!yt`, `!search` / `!web`

#### Costs & Reports
`!costs`, `!budget`, `!digest`, `!report daily/weekly`

#### Notes & Storage
`!memo`, `!note`, `!bookmark` / `!bm`, `!export`, `!snippet`, `!paste`, `!quote`, `!template`, `!tag`

#### Chat Analysis (userbot-only)
`!grep`, `!context`, `!monitor`, `!who`, `!fwd`, `!collect`, `!top`, `!history`, `!chatinfo`

#### Messaging & Actions
`!pin`, `!unpin`, `!del`, `!purge`, `!autodel`, `!schedule`, `!poll`, `!quiz`, `!dice`, `!typing`

#### Text Utilities
`!calc`, `!b64`, `!hash`, `!len` / `!count`, `!json`, `!sed`, `!diff`, `!regex`, `!rand`, `!qr`

#### Time & Utility
`!timer`, `!stopwatch`, `!remind` (NL), `!time`, `!currency`, `!ip`, `!dns`, `!ping`, `!link`, `!uptime`, `!sysinfo`

#### Social & Moderation
`!react`, `!afk` / `!back`, `!welcome`, `!sticker`, `!alias`, `!tts`, `!chatmute`, `!slowmode`, `!spam`, `!archive` / `!unarchive`, `!mark`, `!blocked`, `!invite`, `!profile`, `!contacts`

#### Translator (обновлено)
`!translator on` / `off` / `status` / `history` / `lang` / `test` / `auto` / `help`

#### Swarm (обновлено)
`!swarm <team> <задача>`, `!swarm research`, `!swarm summary` / `!swarm сводка`, `!swarm teams`, `!swarm schedule`, `!swarm memory`
`!swarm task board` / `list` / `create` / `done` / `fail` / `assign` / `priority` / `count`

#### System
`!health`, `!stats` (обогащён FinOps/Translator/Swarm секциями), `!uptime`, `!sysinfo`, `!help` (из registry), `!run`, `!set`, `!todo`, `!restart`, `!remind`

---

### Новые модули (src/core/)

| Модуль | Назначение |
|--------|-----------|
| `silence_schedule.py` | Расписание тишины (time-based auto-silence) |
| `memo_service.py` | Временные заметки (in-memory) |
| `bookmark_service.py` | Персистентные закладки на сообщения |
| `chat_monitor.py` | Мониторинг ключевых слов по чатам |
| `command_aliases.py` | Пользовательские алиасы команд (персистентные) |
| `command_registry.py` | Центральный реестр всех команд (~55) |
| `message_scheduler.py` | Планировщик отложенных сообщений |
| `telegram_buttons.py` | Inline keyboard builder (callback_data) |
| `reaction_engine.py` | Управление реакциями на сообщения |
| `personal_todo.py` | Личный TODO-список через !todo |
| `spam_guard.py` | Расширенная защита от спама |
| `swarm_research_pipeline.py` | Research pipeline для !swarm research |

---

### Tests (+2508 новых)

Ключевые новые тест-файлы session 7:

| Файл | Тестов | Описание |
|------|--------|---------|
| `test_command_registry.py` | 48 | CommandRegistry: lookup, aliases, categories |
| `test_web_api_commands_registry.py` | 37 | /api/commands, /api/commands/{name} |
| `test_help_command.py` | (251 lines) | handle_help из registry, пагинация |
| `test_timer_stopwatch_commands.py` | 36 | !timer, !stopwatch: parse_duration, create_task |
| `test_scheduler.py` | итого 61 (+39) | NL remind парсинг: tomorrow, in Nm, at HH:MM |
| `test_cost_budget_alert.py` | 4 | budget thresholds, >80%/>100% severity |
| `test_weekly_digest.py` | 4 | callback, error resilience |
| `test_translator_engine_optimized.py` | 5 | max_output_tokens 512, pre-clear |
| `test_inbox_status_filter.py` | 7 | status=all/acked/open/empty |
| `test_web_app_costs_finops.py` | 7 | FinOps response fields |

---

### Phase 7 статус (после session 7)

- **Готовность: ~88%** (было ~40% после session 6)

| Компонент | Статус |
|-----------|--------|
| WeeklyDigest Telegram delivery | ✅ |
| Cost budget alert (>80% warn, >100% error) | ✅ |
| Dashboard API gaps закрыты (6/6 verified) | ✅ |
| Translator latency оптимизирована (512 tok) | ✅ |
| Streaming UI конфигурация | ✅ |
| Research Pipeline модуль | ✅ |
| Auto-Dispatch workflow_type | ✅ |
| CommandRegistry (55+ команд) | ✅ |
| LLM error auto-retry | ✅ |
| Autonomous swarm DM handlers | ✅ |
| ~100 новых Telegram команд | ✅ |
| 12 новых src/core/ модулей | ✅ |
| Dashboard frontend spec (docs/DASHBOARD_REDESIGN_SPEC.md) | ✅ |
| Dashboard frontend реализация | ❌ (session 8) |
| Swarm listeners e2e | ❌ (session 8) |
| KrabEar диаризация | ❌ (session 8) |

---

## 📋 Session 6 (2026-04-12)

> 93 файла изменено | 5988 вставок / 2523 удалений | 1 новый doc | 1 удалённый файл

### Bugfixes
- **`kraab` property** (`web_app.py`) — теперь доступ к userbot через `deps["kraab_userbot"]` вместо прямого присвоения `app.kraab`; тесты исправлены соответственно
- **Model/catalog crash** (`provider_manager.py`) — расширены dict-записи для всех Gemini/OpenAI/LM Studio моделей, убраны однострочные записи провоцировавшие KeyError
- **Proxy timeouts** (`test_web_api_endpoints.py`) — добавлены timeout-тесты для 6 OpenClaw proxy endpoints; выявлены и покрыты зависающие сценарии
- **Translator openclaw** (`openclaw_client.py`) — импорт `reload_openclaw_secrets`, улучшена обрезка истории чата, форматирование error tuples
- **`_ub` refs** (`userbot_bridge.py`) — удалены неиспользуемые импорты (`shutil`, `sqlite3`, `textwrap`, `traceback`, `types`), убраны оборванные ссылки
- **Obsidian Librarian** — форматирование + мелкие fix в `openclaw_runtime_signal_truth.py`

### Features
- **ErrorDigest** (`proactive_watch.py`) — `run_error_digest()` + `start_error_digest_loop()`: агрегирует inbox items по severity, swarm job failures, уведомляет owner-а периодически; интеграция с `inbox_service`
- **WeeklyDigest** — фундамент через `inbox_service` + `scheduler.py` (sync fired-reminder'ов с InboxService); полная реализация запланирована Phase 7 P0
- **`!swarm research`** — research pipeline в `command_handlers.py`; analysts-команда с обязательным web_search, структурированный отчёт (Summary / Key Findings / Sources / Next Steps)
- **`!swarm summary`** — compact сводка сессии: задачи created/completed/failed, артефакты, cost_analytics токены/USD
- **`save_report` all teams** (`swarm.py`) — markdown-отчёты теперь генерируются для всех 4 команд (analysts/traders/coders/creative), ранее только analysts+traders
- **Alert workflows** (`proactive_watch.py`) — детектирование `cost_budget_exceeded`, `swarm_job_stalled`, `inbox_critical_open`; расширены `open_trace_reasons` / `close_trace_reasons`
- **Owner Panel новые endpoints** (`web_app.py`) — `swarm_artifacts_list`, `swarm_task_detail`, `ops_timeline`, `model_recommend`, `ops_cost_report`, `ops_executive_summary`, `_build_runtime_cloud_presets`, browser diagnostics helpers

### Infra
- **Hammerspoon MCP зарегистрирован** — `hammerspoon_bridge.py` подключён к MCP manifest, инструменты доступны из swarm tools
- **10/10 restart cycles** — проверено через `new start_krab.command` / `new Stop Krab.command`; стабильность подтверждена
- **Translator voice verified** — E2E roundtrip с voice gateway подтверждён; timeout handling улучшен в `voice_gateway_client.py`
- **KraabUserbot class** введён в `userbot_bridge.py` — инкапсуляция userbot instance, `silence_manager` + `_is_bulk_sender_ext` + `handle_cap` как зависимости

### Tests (+97 новых)
- `test_proactive_watch.py` (+116 lines) — ErrorDigest: empty inbox, различные severity сценарии
- `test_web_api_endpoints.py` (+121 lines) — timeout-тесты для 6 proxy endpoints, fix инициализации `kraab_userbot`
- Translator, handlers, digest, research, browser, macos — покрытие в рамках существующих test-файлов

### Quality
- **268 ruff fixes** — массовое форматирование через `ruff check --fix src/`
- **78 файлов отформатированы** — `ruff format src/` (выравнивание импортов, trailing commas, длинные строки)
- **API audit (189 endpoints)** — полный аудит Owner Panel `/api/*`; выявлены зависающие proxy endpoints
- Удалены неиспользуемые импорты из ~20 файлов

### Docs
- `docs/DASHBOARD_SPEC.md` — новый файл: полная спецификация Owner Panel dashboard
- `.remember/phase7_analysis.md` — анализ Phase 7 Service Workflows: gaps, приоритеты P0/P1/P2, оценки усилий
- `.remember/session6_changes.md` — детальный лог изменений сессии с разбивкой на логические коммиты

### Phase 7 статус (после session 6)
- **Готовность: ~40%** (было ~25%)
- ✅ ErrorDigest loop реализован (`proactive_watch.py`)
- ✅ `save_report` для всех 4 команд
- ✅ Alert workflows (cost/stalled/critical) — фундамент
- ✅ `!swarm summary` + `!swarm research` команды
- ❌ WeeklyDigest (`swarm_weekly_digest.py`) — следующий приоритет P0
- ❌ Scheduled Auto-Dispatch с workflow_type + cron_expr
- ❌ Research Pipeline как отдельный модуль (`swarm_research_pipeline.py`)

---

---

## 🚀 Глобальное видение (Ultimate Goals)

### 1. Рой автономных агентов (Multi-Agent Swarm)
**Цель:** Создание независимых виртуальных команд (трейдеры, кодеры, аналитики), которые могут общаться между собой. Например, команда трейдеров анализирует рынок и ставит задачу команде кодеров на написание/корректировку крипто-бота. Главный фокус — окупаемость и автономный заработок.

**Статус:** 🚧 В РАЗРАБОТКЕ (R18→R20, 2026-04-06) — инфраструктура + память + расписание + инструменты:
- `src/core/swarm_bus.py`: TeamRegistry (4 команды: traders/coders/analysts/creative) + SwarmBus (межкомандное делегирование через `[DELEGATE: team]`, max_depth=1)
- `src/core/swarm.py`: AgentRoom R18 — детектирует директивы делегирования, инжектирует результат в контекст
- `src/core/swarm_memory.py`: ✅ (2026-04-05) Персистентная память между сессиями — JSON в `~/.openclaw/krab_runtime_state/swarm_memory.json`, FIFO 50 записей/команда, auto-inject в system_hint ролей
- `src/core/swarm_scheduler.py`: ✅ (2026-04-05) Рекуррентный планировщик — `!swarm schedule traders 4h BTC`, `!swarm jobs`, `!swarm unschedule <id>`, гейт `SWARM_AUTONOMOUS_ENABLED`
- ✅ (2026-04-06) **Tool access**: web_search, tor_fetch (TOR_ENABLED), peekaboo, все MCP tools. Tool awareness hint инжектируется в промпт каждой роли. `SWARM_ROLE_MAX_OUTPUT_TOKENS`=4096, `role_context_clip`=3000.
- ✅ (2026-04-06) **Forum Topics**: Одна supergroup "🐝 Krab Swarm" (chat_id `-1003703978531`) с 5 топиками (traders/coders/analysts/creative/crossteam). Live broadcast каждой роли в соответствующий топик. Делегирование → crossteam topic. `!swarm setup` для автоматического создания.
- ✅ (2026-04-06) **Broadcast for delegated rounds**: Убран фильтр `_depth==0` — delegated rounds (coders при делегировании от traders) теперь тоже транслируются в свой топик. `_MAX_DEPTH` снижен 2→1 для предотвращения каскадных делегаций.
- ✅ (2026-04-06) **Swarm channels tests**: 31 тест (broadcast routing, delegation, resolve_destination, is_forum_mode, resolve_team_from_topic). Мокают `_send_message` для изоляции от Pyrogram transport.
- Команды в Telegram: `!swarm traders <тема>`, `!swarm teams`, `!swarm memory [команда]`, `!swarm schedule/jobs/unschedule`

**Следующий шаг:** Миграция на pyrofork (объединение dual venv), затем отдельные TG аккаунты для команд свёрма.

### 2. Максимальный доступ к macOS (Permission Audit)
**Цель:** Дать Крабу возможность полностью управлять файловой системой, окнами и процессами без ручного вмешательства.
**Статус:** ✅ ВЫПОЛНЕНО (2026-04-05) — полный аудит `artifacts/ops/macos_permission_audit_pablito_latest.json`, `overall_ready=true`. Full Disk Access, Accessibility, Screen Recording — всё выдано.

### 3. Интеграция с умным домом (HomePod mini)
**Цель:** В будущем подключить управление HomePod mini и другими устройствами Apple прямо из контекста диалога с Крабом. (Приоритет: низкий, ждет стабилизации ядра).

---

## 🔴 Критично (Стабильность системы)

### 4. Устранение OOM-крашей в Krab Ear (Транскрибация)
**Симптом:** Параллельная обработка аудио запускает несколько инстансов Whisper, уводя систему в swap.
**Решение:** Внедрить очередь `queue.Queue()` или `.lock` файл в `krab_ear_watchdog.py` для строго последовательной обработки.

**Статус:** ✅ ИСПРАВЛЕНО (2026-03-23) — добавлен `asyncio.Lock()` в Krab Voice Gateway (`app/stt_engines.py`)
для последовательной обработки Whisper. Только один инстанс за раз — OOM устранён.

### 5. Авто-восстановление шлюза (Self-healing)
**Симптом:** При падениях шлюз OpenClaw зависает (not loaded). Вотчдог пытался перезапускать, но из-за трёх багов не мог: (a) `time.sleep(2)` слишком мало для старта, (b) нет cooldown — pkill убивал ещё стартующий шлюз, (c) дублирование логов мешало анализу.
**Статус:** ✅ ПОЛНОСТЬЮ ИСПРАВЛЕНО (2026-03-23) — трёхуровневый self-healing:
1. **macOS LaunchAgent** (`ai.openclaw.gateway.plist`, KeepAlive=true) — launchd-уровень, самовосстановление ~5с, выживает перезагрузку. Установлен через `openclaw gateway install`.
2. **`telegram_session_watchdog.py`** — retry loop (8с), cooldown 180с, "уже жив"-проверка перед pkill, фикс дублирования логов.
3. **`new start_krab.command`** — обновлён: теперь знает о LaunchAgent (не запускает nohup-конкурент), добавлен `openclaw doctor --fix` перед стартом.
4. **`new Stop Krab.command`** — обновлён: не трогает gateway при LaunchAgent (gateway — инфраструктура, живёт независимо от бота).

### 6. Таймауты Telegram API при долгих задачах
**Симптом:** При вызове множества инструментов вебхук Telegram отваливается.
**Решение:** Перевести долгие задачи на асинхронную очередь (`sendMessage`) и увеличить таймаут в конфигурации OpenClaw.
**Статус:** ✅ ПОЛНОСТЬЮ ИСПРАВЛЕНО (2026-04-04) — два уровня:
1. (2026-03-23) Увеличены таймауты в `~/.openclaw/openclaw.json`: `channels.telegram.timeoutSeconds: 180`, retry-политика `{attempts: 5, minDelayMs: 500, maxDelayMs: 60000, jitter: 0.2}`.
2. (2026-04-04) Добавлена `_TelegramSendQueue` — per-chat async queue с exponential backoff (0.5→1→2с, до 3 попыток) для всех исходящих Telegram API вызовов (`_safe_edit`, `_safe_reply_or_send_new`, voice/document send). Ленивые воркеры, автостоп через 30с простоя. Cleanup при shutdown.

---

## 🟠 Важно (Расширение функционала и UX)

### 7. Прозрачность долгих запросов (Как в нативном дашборде)
**Проблема:** В Telegram не видно, что Краб работает над задачей, кажется, что он завис.
**Решение:** - Добавить промежуточные статусы в Telegram-транспорт ("Вызываю инструмент...", "Читаю скриншот...").
- Использовать `sendChatAction` (`typing`), чтобы индикатор набора текста висел всё время, пока ИИ думает.
**Статус:** ✅ ПОЛНОСТЬЮ ИСПРАВЛЕНО (2026-04-04) — три уровня:
1. (2026-03-28) Базовый UX-контур: `typing` во время reasoning/tool-flow, delivery-actions перед отправкой вложений.
2. (2026-04-04) Granular tool-stage narration: `_TOOL_NARRATIONS` dict (25 инструментов) в `openclaw_client.py` + `_narrate_tool()` с fallback по подстроке. Вместо "🔧 Выполняется: browser" теперь "🌐 Открываю браузер...", "📸 Делаю скриншот..." и т.д. Polling каждые 4 сек, автоматическое обновление temp_msg.

### 8. Telegram-транспорт: live-smoke голосовых и hygiene ответов
**Актуализация 2026-03-27:** owner private text+voice roundtrip, mention-gated/group flow и graceful-content после raw fallback уже подтверждены живым E2E через второй Telegram MCP аккаунт `p0lrd`; детали и артефакты перенесены в `RESOLVED.md`.

### 9. Обновление Vision API и чтение скриншотов
**Симптом:** `vision_read.py` стучился в устаревшую модель `gemini-1.5-pro-latest` (ошибка 404).
**Статус:** ✅ ПОЛНОСТЬЮ ИСПРАВЛЕНО (2026-03-23) — нативный путь через OpenClaw images уже работает, `vision_read.py` не используется в основном потоке обработки фото.

Нативная интеграция подтверждена в `userbot_bridge.py` (строки 3300–3420):
- При `message.photo` фото скачивается через `client.download_media()` → конвертируется в base64 → передаётся в `send_message_stream(..., images=[b64_img])` напрямую в OpenClaw.
- `vision_read.py` как subprocess нигде не вызывается (файл отсутствует в `src/`).
- Для фото-маршрута автоматически применяются увеличенные таймауты (`_resolve_openclaw_stream_timeouts(has_photo=True)`) и принудительный cloud-роутинг (`_should_force_cloud_for_photo_route`).

### 18. ACL — Silence mode, Guest tools, Spam filter
**Статус:** ✅ РЕАЛИЗОВАНО (2026-04-06, batch 6)

**SilenceManager** (`src/core/silence_mode.py`):
- Per-chat и глобальный mute, in-memory, monotonic expiry
- `!тишина [N]` — mute чата на N минут (default 30), `!тишина стоп`, `!тишина глобально [N]`, `!тишина статус`
- Auto-silence: если owner пишет в чат сам → Краб молчит 5 мин (OWNER_AUTO_SILENCE_MINUTES)
- Silence check в pipeline — после trigger conditions, перед AI запросом. Команды (!/.) проходят всегда.
- Доступна FULL access (не только OWNER — т.к. OWNER=yung_nagato, оператор p0lrd имеет FULL)

**Guest mode** (`src/openclaw_client.py`):
- AccessLevel.GUEST → `disable_tools=True` в send_message_stream → tools=[] в payload
- NON_OWNER_SAFE_PROMPT обновлён: "живой помощник", не представляется ботом
- Контролируется `GUEST_TOOLS_DISABLED=1` (env)

**Расширенный спам-фильтр** (`src/core/spam_filter.py`):
- `is_notification_sender()` — shortcodes ≤5 цифр (iMessage)
- `is_bulk_sender()` — scam/fake флаги, verified+no-username (банки/сервисы), OTP-паттерны в first_name
- `should_skip_auto_reply()` — combined check, вызывается из userbot_bridge

**Тесты:** 36 новых (test_silence_mode.py × 22, test_spam_filter.py × 14). E2E подтверждено через MCP.

### 10. Парсинг Mercadona (Anti-bot)
**Решение:** Добавить `puppeteer-extra-plugin-stealth` и перехватывать XHR/Fetch запросы API через `page.on('response')` вместо нестабильного парсинга DOM-элементов.

### 14. Обновление OpenClaw v2026.3.13 → v2026.3.23-beta.1
**Статус:** ✅ ОБНОВЛЕНО (2026-03-23)
- v2026.3.22 имел баг паковки: `dist/control-ui/` отсутствовал в npm-пакете → дашборд не работал.
- v2026.3.22 ужесточил валидацию конфига: пришлось удалить `whatsapp`, `google-gemini-cli-auth` из plugins и поправить `browser.profiles.subscription-portal.driver: "extension"` → `"existing-session"`.
- Установлена бета v2026.3.23-beta.1 — UI включён, всё работает.
- Мониторить стабильность бета-версии.

### 15. Burst coalescing уже работает
**Статус:** ✅ ПОДТВЕРЖДЕНО — в логах видно `private_text_burst_coalesced absorbed_message_ids=['11127', '11128'] messages_count=3`. Склейка пересланных подряд сообщений работает.

### 17. Owner Panel: детерминированная initial hydration после рестарта
**Статус:** ✅ ПОЛНОСТЬЮ ИСПРАВЛЕНО (2026-03-28) — initial hydration теперь четырёхслойная:
1. `refreshAll()` уже не последовательный.
2. Translator first-paint идёт через единый `/api/translator/bootstrap`.
3. Owner panel поднимает last-good runtime sections из `localStorage` (`krab:owner-panel-bootstrap:v1`) до live refresh, поэтому cold reload больше не возвращает ключевые блоки в пустые `—`.
4. Верхний dashboard snapshot и high-value error-path теперь тоже cache-aware, поэтому transient fetch-failure не стирает уже поднятый first-paint обратно в `ERR/FAIL`.
5. `Core Liveness (Lite)` и `Ecosystem Deep Health` теперь при transient fetch-сбое сначала поднимают last-good bootstrap, а не прыгают сразу в `Offline/Error`.

**Оставшееся наблюдение:** `Browser / MCP Readiness` намеренно остаётся в `LOADING`, а не в cached-ready, потому что это volatile probe. Единичные `browser_action_probe_raw_failed` при зелёном acceptance пока считаем шумом health-probe, а не runtime-регрессией.

---

## 🔵 Глубокая интеграция в macOS

### 11. Локальная папка-шлюз "Inbox"
**Статус:** ✅ РЕАЛИЗОВАНО (2026-04-06) — LaunchAgent `ai.krab.inbox-watcher` мониторит `~/Krab_Inbox` через watchdog (FSEvents). Файлы пересылаются Крабу через `/api/notify`. Plist: `scripts/launchagents/ai.krab.inbox-watcher.plist`.

### 12. Глобальный macOS Hotkey
**Статус:** ✅ РЕАЛИЗОВАНО — Hammerspoon ⌘⇧K → текстовый ввод → Krab `/api/notify`. Apple Shortcut тоже настроен.

### 13. Управление окнами через Hammerspoon
**Статус:** ✅ РЕАЛИЗОВАНО — HTTP bridge на `localhost:10101`, Python bridge `src/integrations/hammerspoon_bridge.py`. POST `/window` с командами `left|right|maximize|...`.

---

## 🟢 Закрыто в Session 6 (2026-04-12)

### 19. Phase 7 — Error Digest (GAP 3)
**Статус:** ✅ РЕАЛИЗОВАНО (2026-04-12, session 6)
- `run_error_digest()` + `start_error_digest_loop()` в `src/core/proactive_watch.py`
- Агрегирует inbox items по severity за 24ч, swarm job failures, route_model_changed события
- Периодический trigger через krab_scheduler, уведомление owner-у если ошибок > threshold
- Тесты: `tests/unit/test_proactive_watch.py` (+116 lines)

### 20. Phase 7 — save_report для всех 4 команд (GAP 6)
**Статус:** ✅ РЕАЛИЗОВАНО (2026-04-12, session 6)
- `src/core/swarm.py:337` — расширен список команд: `{"analysts", "traders", "coders", "creative"}`
- Все команды теперь генерируют markdown-отчёты в `reports/`, не только analysts+traders

### 21. KraabUserbot class — рефакторинг userbot_bridge
**Статус:** ✅ РЕАЛИЗОВАНО (2026-04-12, session 6)
- `src/userbot_bridge.py` — введён класс `KraabUserbot` для инкапсуляции userbot instance
- `silence_manager`, `_is_bulk_sender_ext`, `handle_cap` переданы как зависимости
- Удалены неиспользуемые импорты (`shutil`, `sqlite3`, `textwrap`, `traceback`, `types`)

### 22. Alert Workflows — фундамент (GAP 7)
**Статус:** ✅ РЕАЛИЗОВАНО (2026-04-12, session 6) — базовый уровень
- `src/core/proactive_watch.py` — детектирование: `cost_budget_exceeded`, `swarm_job_stalled`, `inbox_critical_open`
- Расширены `open_trace_reasons` / `close_trace_reasons` для автоматического inbox lifecycle
- Полный threshold-based alerting — следующий шаг (Phase 7 P2)

### 23. Owner Panel — расширенные endpoints
**Статус:** ✅ РЕАЛИЗОВАНО (2026-04-12, session 6)
- `src/modules/web_app.py`: `swarm_artifacts_list`, `swarm_task_detail`, `ops_timeline`, `model_recommend`, `ops_cost_report`, `ops_executive_summary`
- Browser diagnostics: `_probe_owner_chrome_devtools`, `_build_browser_access_paths`, `_collect_openclaw_browser_smoke_report`
- `kraab` property через `deps["kraab_userbot"]` (ломающий API исправлен)
