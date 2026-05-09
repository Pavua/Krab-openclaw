# Стартовый промпт для Session 9

Скопируй это в новый чат Claude Code когда начнёшь Session 9:

---

Проект: **Krab** — персональный Telegram userbot на pyrofork + MTProto с OpenClaw Gateway, Dashboard V4 на :8080, Memory Layer (SQLite FTS5 + Model2Vec), мультиагентным swarm и набором MCP серверов.

Path: `/Users/pablito/Antigravity_AGENTS/Краб`

**ВАЖНО: начинай от main branch!** 
Session 8 worktrees (`.claude/worktrees/youthful-pascal`, `.claude/worktrees/memory-layer`) можно удалить — их ветки уже merged. Если нужен новый worktree — создавай от свежего main:
```bash
cd /Users/pablito/Antigravity_AGENTS/Краб
git checkout main && git pull
git worktree add .claude/worktrees/session-9 -b claude/session-9
```

## Текущее состояние (Session 8 closed, main at commit 481c287+)

- **149+ коммитов** Session 8 в main (3 PR merged: Track B #11, Track E #12, hotfix #13)
- **Dashboard V4** — 10 страниц готовы: Hub / Chat / Costs / Inbox / Swarm / Translator / Ops / Research / Settings / Commands
- **Memory Layer** operational — HybridRetriever active, `is_memory_layer_available() == True`, но archive.db ещё пустой (нет Telegram Export)
- **Phase 7** = 100% (test_log, !members, !cron closed)
- **Готовность проекта** ~99%

## Прочитай первым делом

1. `/Users/pablito/Antigravity_AGENTS/Краб/.claude/worktrees/youthful-pascal/.remember/next_session.md` — подробный handoff Session 8 → 9
2. `IMPROVEMENTS.md` в корне — архитектурный бэклог с Session 8 rollup
3. `CLAUDE.md` в корне — канонические конвенции проекта

## Session 9 приоритеты (по важности)

### 🔴 Критичные
1. **Telegram Export → real data ingestion в Memory Layer**
   - Пользователь обещал сделать Export в `~/Downloads/tg_export_for_krab/p0lrd_whitelist/`
   - Запустить bootstrap parser из `src/core/memory_archive.py` или `scripts/bootstrap_memory.py`
   - Verify `search_archive("test")` возвращает реальные results

2. **Phase 4 — Incremental indexer worker**
   - Добавить `src/core/memory_indexer_worker.py` (hook на каждое incoming msg)
   - Интеграция в aux_tasks `src/userbot_bridge.py` (параллельно с proactive_watch)
   - Real-time indexing новых Telegram messages

3. **Mobile PWA тестирование** на iPhone (10 pages на 375px viewport)

### 🟡 Важные
4. **Ops metrics real data collection** — сейчас `/api/ops/metrics` возвращает нули (new runtime after restart). Собирать latencies/errors из proactive_watch и persist.
5. **Integration tests** для memory_adapter — real flow: ingest → search → verify text_redacted → confirm decay
6. **2 flaky integration tests** — `test_cloud_failover_chain_smoke`, `test_full_message_flow` (Track B area, надо понять regression или environment)

### 🟢 Nice-to-have
7. **Remote access** (152.89.100.100 + Caddy SSL, или Cloudflare Tunnel, или Tailscale Funnel)
8. **Guest Mode** (OWNER / VIEWER / DEMO roles с redaction)
9. **Bell dropdown polish** — "Mark all as read" кнопка
10. **Inbox bulk actions**

## Важные правила проекта

### Железные
1. **Frontend/CSS/HTML — ТОЛЬКО через Gemini 3.1 Pro API**
   - Модель: `gemini-3.1-pro-preview`
   - API key: `AIzaSyA07LwNZgfBf3NhyBu_VuHdY5Tq_LhPUKY`
   - Endpoint: `https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-pro-preview:generateContent?key=AIzaSyA07LwNZgfBf3NhyBu_VuHdY5Tq_LhPUKY`
   - JS-логика, HTML structural edits (новые ссылки в nav), Python backend — можно сам
   - Записано в `~/.claude/projects/-Users-pablito-Antigravity-AGENTS-----/memory/feedback_frontend_gemini_only.md`

2. **Не SIGHUP openclaw** — только `openclaw gateway`

3. **Krab restart** — только через канонические скрипты:
   - `/Users/pablito/Antigravity_AGENTS/new start_krab.command`
   - `/Users/pablito/Antigravity_AGENTS/new Stop Krab.command`

4. **Общение на русском**, комментарии в коде краткие русские

5. **LM Studio** — тестировать модели по одной (RAM overflow на 36GB M4 Max)

### Архитектурные
- `src/userbot_bridge.py` — ядро (pyrofork, message processing, aux_tasks)
- `src/openclaw_client.py` — OpenClaw API + tool loop
- `src/modules/web_app.py` — Dashboard :8080 (210+ endpoints)
- `src/web/v4/` — 10 HTML страниц Liquid Glass
- `src/core/memory_*` — Memory Layer (Track E, merged в Session 8)
- `src/core/swarm_*` — мультиагентный swarm (4 команды)
- `src/handlers/command_handlers.py` — 180+ команд

### Паттерны
- Singleton pattern для memory_manager, model_manager
- Singleton + try/except ImportError для memory_adapter
- FastAPI маршруты `/v4/*` через FileResponse
- SSE через StreamingResponse (chat, swarm, inbox events)
- Safe DOM building в JS (createElement + textContent, не innerHTML)

## Инфраструктура

| Service | Port | State |
|---------|------|-------|
| Krab core | 8080 | running via launchd |
| OpenClaw gateway | 18789 | live |
| LM Studio | 1234 | loaded |
| MCP yung-nagato | 8011 | up |
| MCP p0lrd | 8012 | up |
| MCP Hammerspoon | 8013 | up |

## Gemini 3.1 Pro агенты

Используй для CSS/HTML/визуального дизайна. Можно запускать **до 10 параллельно** через Agent tool:
```
Agent(description="Gemini: task name", subagent_type="general-purpose", prompt="Используй ТОЛЬКО Gemini 3.1 Pro API ...", run_in_background=true)
```

## Параллельный чат

Session 8 параллельный чат Track E закрыт после MEMORY_LAYER_GUIDE.md merge.
Если нужна параллелизация в Session 9 — открывай новый чат с конкретным треком.

## Git workflow

- Main branch: `main`
- Worktrees: `.claude/worktrees/*`
- PR через `gh pr create --base main --head <branch>`
- Merge через `gh pr merge <num> --merge` (сохраняем историю коммитов)

---

Добавь это в начало нового чата. После того как прочтёшь handoff и текущие коммиты (`git log --oneline -10`), скажи мне какой приоритет начнёшь первым — и поехали.

🦀 Давай продолжим Krab!
