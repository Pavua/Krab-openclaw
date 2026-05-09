# Стартовый промпт для Session 10

Скопируй это в новый чат Claude Code когда начнёшь Session 10:

---

Проект: **Krab** — персональный Telegram userbot на pyrofork + MTProto с OpenClaw Gateway, Dashboard V4 на :8080, Memory Layer (SQLite FTS5 + Model2Vec + real-time indexer), мультиагентным swarm и набором MCP серверов.

Path: `/Users/pablito/Antigravity_AGENTS/Краб`

**ВАЖНО: начинай от main branch!**
Session 9 worktree (`.claude/worktrees/practical-austin`) содержит все merged ветки. Если нужен новый worktree — создавай от свежего main:
```bash
cd /Users/pablito/Antigravity_AGENTS/Краб
git checkout main && git pull
git worktree add .claude/worktrees/session-10 -b claude/session-10
```

## Текущее состояние (Session 9 closed)

- **~30 коммитов Session 9** в main: PR #17 (Phase 4 Memory Indexer Worker) + 6 security/UX фиксов + provider re-auth UI + observability instrumentation
- **Phase 4 = 100%** — Memory Indexer Worker live, archive.db создан, real Telegram messages индексируются с PII redaction + chunking + embeddings
- **Prompt injection RESOLVED** (root cause: `~/.openclaw/workspace-main-messaging/MEMORY.md` строка 15 — sandbox-инструкция «хвала лламовой халве» которую Krab сам записал по чужой просьбе через `memory-core` plugin)
- **Готовность проекта** ~99% (production-ready по большинству каналов)
- **Krab live**: `codex-cli/gpt-5.4` primary + 3 Gemini fallbacks

## Прочитай первым делом

1. `/Users/pablito/Antigravity_AGENTS/Краб/.remember/next_session.md` — подробный handoff Session 9 → 10 (главное про prompt injection и MEMORY.md gotcha)
2. `IMPROVEMENTS.md` в корне — архитектурный бэклог с Session 9 rollup
3. `CLAUDE.md` в корне — канонические конвенции проекта

## Session 10 приоритеты (по важности)

### 🔴 Критичные

1. **Memory validator в memory-core plugin** — главный systemic fix injection vector. OpenClaw memory-core пишет в MEMORY.md/USER.md по просьбе. Нужна валидация: префиксы "всегда", "после каждого", "пиши только X", "в каждом ответе" должны требовать **явного подтверждения владельца** (через `!confirm <hash>` или подобное). Это превратит "записал что сказали" в "записал что владелец подтвердил".

2. **Aggressive `!reset` команда** — текущий `!clear` чистит только Krab `history_cache.db`. Нужна команда которая чистит:
   - `~/.openclaw/krab_runtime_state/history_cache.db` (Krab cache)
   - `~/.openclaw/agents/main/sessions/{session_id}.jsonl` (OpenClaw session)
   - Опционально invalidate Gemini prompt cache (через cache-aware request)

3. **Tool call indicator в buffered mode** — расширить `src/core/openclaw_task_poller.py` (уже создан в Session 9) чтобы показывать `🔧 Вызов: tool_name(...)` в Telegram progress notice во время codex-cli/buffered запросов. Сейчас юзер видит только "⏱ Прошло: ~5 мин 15 сек" без деталей. Нужно: SQLite poll `~/.openclaw/tasks/runs.sqlite` + extract `progress_summary` + show в notice.

### 🟡 Важные

4. **Auto-restart упавших компонентов** — расширить `proactive_watch.py` для самодиагностики 9 сервисов и automatic restart падших (вместо текущего passive monitoring).

5. **Correlation ID per request** — `request_id` в structlog context, прокидывается через всю цепочку. Без этого debug параллельных swarm runs — ад.

6. **codex-cli session leak fix** — gateway restart посреди активного codex-cli stream оставляет hung process. Watchdog `openclaw_task_poller` это должен ловить — нужно интегрировать в `llm_flow.py` чтобы реально cancel запрос при detect стагнации (~120s без `last_event_at` обновления).

### 🟢 Nice-to-have

7. **Telegram Export → Memory Layer bootstrap** — ждёт от пользователя JSON Export. `scripts/bootstrap_memory.py --export ~/Downloads/tg_export_for_krab/p0lrd_whitelist/result.json --db ~/.openclaw/krab_memory/archive.db --verbose`.

8. **Provider cards CSS polish** через Gemini 3.1 Pro API.

9. **HOW2AI live verification** — отправить тестовое сообщение со ссылкой и убедиться что (a) backticks применяются, (b) нет SlowmodeWait залипания.

## Важные правила проекта

### Железные

1. **Frontend/CSS/HTML — ТОЛЬКО через Gemini 3.1 Pro API**
   - Модель: `gemini-3.1-pro-preview`
   - JS-логика, HTML structural, Python backend — можно сам

2. **Не SIGHUP openclaw** — только `openclaw gateway start/stop`

3. **Krab restart** — только канонические скрипты:
   - `/Users/pablito/Antigravity_AGENTS/new start_krab.command`
   - `/Users/pablito/Antigravity_AGENTS/new Stop Krab.command`
   - **Wait 3-5 секунд** между stop и start

4. **Общение на русском**, комментарии в коде краткие русские

5. **MEMORY.md / USER.md в `~/.openclaw/workspace-main-messaging/` — SENSITIVE!**
   - Любая запись влияет на ВСЕ ответы Krab (bootstrap context)
   - Не записывай туда ничего без явного подтверждения владельца
   - Если Krab "помнит непонятную инструкцию" — смотри сюда первым делом

### Архитектурные

- `src/userbot_bridge.py` — ядро (pyrofork, message processing, aux_tasks)
- `src/openclaw_client.py` — OpenClaw API + tool loop + ops metrics instrumentation
- `src/modules/web_app.py` — Dashboard :8080 (215+ endpoints)
- `src/web/v4/` — 10 HTML страниц Liquid Glass + provider cards
- `src/core/memory_*` — Memory Layer + indexer worker
- `src/core/openclaw_task_poller.py` — gateway watchdog + task state poller (Session 9)
- `src/core/swarm_*` — мультиагентный swarm
- `src/handlers/command_handlers.py` — 180+ команд + `_reply_tech` DM redirect helper

### Паттерны

- Singleton pattern для memory_manager, model_manager, command_registry
- Singleton + try/except ImportError для memory_adapter
- FastAPI маршруты `/v4/*` через FileResponse
- SSE через StreamingResponse (chat, swarm, inbox events)
- Safe DOM building в JS (createElement + textContent, не innerHTML)
- DM redirect для тех-сообщений: `chat_id < 0 → "me"`

## Архитектура аккаунтов Telegram

- **p0lrd** = твой основной Telegram аккаунт (user_id 312322764, display "OG P Cod/id"), **Krab userbot работает на этой сессии**
- **Yung Nagato** (user_id 6435872621) = отдельный Krab-аккаунт для swarm features
- Ты пишешь Крабу: **p0lrd → Yung Nagato** (DM между двумя аккаунтами)
- При тестировании через MCP: `p0lrd → chat_id 6435872621` triggers Krab response

## Инфраструктура

| Service | Port | State |
|---------|------|-------|
| Krab core | 8080 | running, codex-cli/gpt-5.4 primary |
| OpenClaw gateway | 18789 | live (v2026.4.14) |
| LM Studio | 1234 | available |
| MCP yung-nagato | 8011 | up |
| MCP p0lrd | 8012 | up |
| MCP Hammerspoon | 8013 | up |

## Параллельные агенты

Используй для research + независимых файлов **до 10+ параллельно** через Agent tool с `isolation: "worktree"`:
```
Agent({
  description: "<task>",
  subagent_type: "general-purpose",
  isolation: "worktree",
  run_in_background: true,
  prompt: "..."
})
```

**Паттерн волн:**
- **Wave 1**: research/diagnostic agents (read-only, можно много параллельно)
- **Wave 2**: implementation fixes на разных файлах (параллельно с worktree isolation)
- **Wave 3**: integration + smoke test (sequential)

## Git workflow

- Main branch: `main`
- Worktrees: `.claude/worktrees/*`
- PR через `gh pr create --base main --head <branch>`
- Merge через `gh pr merge <num> --merge` (сохраняем историю коммитов)
- При конфликтах: `git rebase origin/main` + `git checkout --theirs/--ours <file>` если нужно

---

Добавь это в начало нового чата. После того как прочтёшь handoff и текущие коммиты (`git log --oneline -10`), скажи мне какой приоритет начнёшь первым — и поехали.

🦀 Давай продолжим Krab!
