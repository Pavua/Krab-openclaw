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
  core/
    swarm.py            — AgentRoom: мультиагентные роли, delegation
    swarm_bus.py        — SwarmBus + TEAM_REGISTRY (traders/coders/analysts/creative)
    swarm_memory.py     — персистентная память свёрма (JSON, FIFO 50/team)
    swarm_scheduler.py  — рекуррентный планировщик (!swarm schedule)
    swarm_channels.py   — live broadcast в Telegram группы
    subprocess_env.py   — clean_subprocess_env() (MallocStackLogging cleanup)
    proactive_watch.py  — фоновый мониторинг runtime state (+ ErrorDigest + Telegram alerts)
    weekly_digest.py    — еженедельный дайджест активности (session 6)
  handlers/
    command_handlers.py — !swarm, !search, _AgentRoomRouterAdapter
  integrations/
    tor_bridge.py       — Tor SOCKS5 proxy (httpx + Playwright)
    browser_bridge.py   — CDP подключение к Chrome
    hammerspoon_bridge.py — HTTP bridge к Hammerspoon :10101
    macos_automation.py — AppleScript/osascript автоматизация
  skills/
    mercadona.py        — Playwright scraper со stealth
  modules/
    web_app.py          — Owner panel FastAPI (:8080)
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

Текущий routing (11.04.2026):
- Primary: `google/gemini-3-pro-preview`
- Translator: `google/gemini-3-flash-preview` (preferred_model для скорости)
- Fallbacks: `gemini-2.5-pro-preview`, `gemini-2.5-flash`, `gemini-3-flash-preview`
- `google-antigravity` — НЕ использовать (квота/бан)
- LM Studio local — автоматический fallback при cloud-failure

## Свёрм (Multi-Agent)

Команды в Telegram: `!swarm <team> <topic>`, `!swarm teams`, `!swarm schedule`, `!swarm memory`
Дополнительные команды (session 6): `!swarm research <topic>` — глубокий веб-ресёрч свёрмом; `!swarm summary` / `!swarm сводка` — сводка последних активностей
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

## Ссылки

- `IMPROVEMENTS.md` — архитектурный бэклог и глобальное видение
- `docs/MASTER_PLAN_VNEXT_RU.md` — мастер-план проекта
- Memory: `~/.claude/projects/-Users-pablito-Antigravity-AGENTS-----/memory/`

## Owner Panel API (актуально на 12.04.2026)

Новые endpoints (session 6):

| Endpoint | Метод | Описание |
|----------|-------|----------|
| `/api/costs/budget` | GET/POST | Просмотр и установка бюджета расходов |
| `/api/costs/history` | GET | История расходов по провайдерам |
| `/api/thinking/status` | GET | Статус режима thinking (extended reasoning) |
| `/api/thinking/set` | POST | Включить/выключить thinking |
| `/api/depth/status` | GET | Текущий уровень глубины reasoning |

## Статистика тестов

| Сессия | Тестов |
|--------|--------|
| Session 5 | 2071 |
| Session 6 | 3633 |
