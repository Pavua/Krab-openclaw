# Стартовый промпт для Session 11

Скопируй это в новый чат Claude Code когда начнёшь Session 11:

---

Проект: **Krab** — персональный Telegram userbot на pyrofork + MTProto с OpenClaw Gateway, Dashboard V4 на :8080, Memory Layer (SQLite FTS5 + Model2Vec + real-time indexer), мультиагентным swarm и набором MCP серверов.

Path: `/Users/pablito/Antigravity_AGENTS/Краб`

**ВАЖНО: начинай от main branch!**
Session 10 merged все worktrees в main. Если нужен новый worktree — создавай от свежего main:
```bash
cd /Users/pablito/Antigravity_AGENTS/Краб
git checkout main && git pull
git worktree add .claude/worktrees/session-11 -b claude/session-11
```

## Текущее состояние (Session 10 closed)

- **~22 коммитов Session 10 в main** — Memory Injection Validator + `!confirm` + `!reset` + correlation ID + tool indicator + auto-restart + codex-cli stagnation cancel + dedicated Chrome
- **Memory Layer Phase 1 live**: archive.db — **42 753 messages / 9 099 chunks / 42 МБ** (yung_nagato bootstrap done, 92 PII redactions)
- **155+ новых тестов Session 10**, total ~7465+ passing
- **Krab live**: `codex-cli/gpt-5.4` primary + 3 Gemini fallbacks
- **Готовность проекта** ~99% (production-ready по большинству каналов)

## Прочитай первым делом

1. `/Users/pablito/Antigravity_AGENTS/Краб/.remember/next_session.md` — подробный handoff Session 10 → 11 (known issues, priorities, команды)
2. `IMPROVEMENTS.md` в корне — архитектурный бэклог с Session 10 rollup
3. `CLAUDE.md` в корне — канонические конвенции проекта

## Session 11 приоритеты (по важности)

### 🔴 Критичные

1. **p0lrd bootstrap** — когда Telegram Export p0lrd финализирован, запустить:
   ```bash
   python scripts/bootstrap_memory.py \
     --export "/Users/pablito/Downloads/Telegram Desktop/DataExport_2026-04-17 (1)/result.json" \
     --db ~/.openclaw/krab_memory/archive.db \
     --whitelist ~/.openclaw/krab_memory/whitelist.json \
     --incremental --verbose
   ```
   Incremental mode не перезапишет yung_nagato. Сверить counts до/после.

2. **Memory Layer Phase 2 — Model2Vec embeddings для retrieval** — сейчас archive.db только FTS5 (keyword). Нужно:
   - Добавить Model2Vec (≤50MB модель) embeddings в `chunks` table
   - HNSW index через `hnswlib` или `faiss`
   - Semantic search команда `!recall <query>` через vectorsearch + FTS5 rerank
   - Smoke-тест + integration тест

3. **Session 10 integration follow-up через Telegram MCP** — end-to-end прогон всех новых фич:
   - `!confirm <hash>` happy path + edge cases
   - `!reset --dry-run --all` превью + `!reset --layer=krab` execute
   - Tool indicator появляется в реальном codex-cli запросе
   - `request_id` видим в логах (`rg 'request_id' logs/`)
   - Dedicated Chrome auto-launch при первом CDP request
   - codex-cli stagnation cancel при искусственной симуляции

### 🟡 Важные

4. **Dashboard V4 — показать Session 10 features** — **ТОЛЬКО через Gemini 3.1 Pro API** (frontend rule). Карточки: Memory Validator queue depth, archive.db stats, auto-restart health, correlation ID в timeline, tool indicator history.

5. **PIIRedactor — дополнительные false positives** — passport numbers, JWT tokens, generic long-digit patterns. Тесты + fix.

6. **Memory Indexer Phase 2 — chunking strategy** — long-form messages (>2k chars) по semantic boundaries (paragraph splits), не fixed-length.

### 🟢 Nice-to-have

7. **Cron jobs для Session 10**: weekly archive audit, pending queue cleanup (validator), auto-restart stats rollup.

8. **HOW2AI live verification** — backticks fix после Session 10 restart.

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

5. **MEMORY.md / USER.md — SENSITIVE!** Записи теперь через `!confirm <hash>` gate (Session 10 Memory Injection Validator).

### Архитектурные

- `src/userbot_bridge.py` — ядро (pyrofork, message processing, aux_tasks)
- `src/openclaw_client.py` — OpenClaw API + tool loop + ops metrics
- `src/modules/web_app.py` — Dashboard :8080 (215+ endpoints)
- `src/web/v4/` — 10 HTML страниц Liquid Glass + provider cards
- `src/core/memory_*` — Memory Layer + indexer worker
- `src/core/memory_validator.py` — Memory Injection Validator (Session 10)
- `src/core/auto_restart_policy.py` — Auto-restart (Session 10)
- `src/integrations/dedicated_chrome.py` — Dedicated Chrome (Session 10)
- `src/core/openclaw_task_poller.py` — gateway watchdog + task state poller
- `src/core/swarm_*` — мультиагентный swarm
- `src/handlers/command_handlers.py` — 180+ команд + `!confirm`, `!reset`

### Паттерны

- Singleton pattern для memory_manager, model_manager, command_registry
- FastAPI маршруты `/v4/*` через FileResponse
- SSE через StreamingResponse (chat, swarm, inbox events)
- Safe DOM building в JS (createElement + textContent, не innerHTML)
- DM redirect для тех-сообщений: `chat_id < 0 → "me"`
- Correlation ID: `request_id` в structlog contextvars, auto-prop через `asyncio.create_task`

## Архитектура аккаунтов Telegram

- **p0lrd** = твой основной Telegram аккаунт (user_id 312322764, display "OG P Cod/id"), **Krab userbot работает на этой сессии**
- **Yung Nagato** (user_id 6435872621) = отдельный Krab-аккаунт для swarm features
- Ты пишешь Крабу: **p0lrd → Yung Nagato** (DM между двумя аккаунтами)
- При тестировании через MCP: `p0lrd → chat_id 6435872621` triggers Krab response

## Инфраструктура

| Service | Port | State |
|---------|------|-------|
| Krab core | 8080 | running, codex-cli/gpt-5.4 primary |
| OpenClaw gateway | 18789 | live (v2026.4.14+) |
| Memory Indexer | — | running, indexer_state populated |
| archive.db | — | 42 МБ / 42 753 msgs / 9 099 chunks |
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
- **Wave 4**: final merge + post-merge ruff/pytest

## Git workflow

- Main branch: `main`
- Worktrees: `.claude/worktrees/*`
- PR через `gh pr create --base main --head <branch>`
- Merge через `gh pr merge <num> --merge` (сохраняем историю коммитов)
- При конфликтах: `git rebase origin/main` + `git checkout --theirs/--ours <file>` если нужно

## Known gotchas Session 10

- **Chrome prompts "Allow remote debugging?"** — MCP отключены в `~/.claude.json`, prompts всё равно приходят. Источник возможно Chrome extension / Arc. Dedicated Chrome (Session 10) должен помочь.
- **Memory validator pending queue persist** — если владелец забыл `!confirm`, записи остаются на диске.
- **PIIRedactor false positives** — Twitter URL status IDs → CARD, ASCII art → PHONE. Частично пофикшено `bada9f4`.
- **archive.db growth** — на больших экспортах до 3 GB. Variant B (whitelist filter) рекомендуется.

---

Добавь это в начало нового чата. После того как прочтёшь handoff и текущие коммиты (`git log --oneline -20`), скажи мне какой приоритет начнёшь первым — и поехали.

🦀 Давай продолжим Krab!
