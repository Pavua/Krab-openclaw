# Стартовый промпт для Session 14

Скопируй это в новый чат Claude Code когда начнёшь Session 14:

---

Проект: **Krab** — персональный Telegram userbot на pyrofork + MTProto с OpenClaw Gateway, Dashboard V4 на :8080, Memory Layer (SQLite FTS5 + Model2Vec), мультиагентным swarm и набором MCP серверов.

Path: `/Users/pablito/Antigravity_AGENTS/Краб`

**ВАЖНО: начинай от main branch!**
Session 13 merged все worktrees в main. Если нужен новый worktree — создавай от свежего main:
```bash
cd /Users/pablito/Antigravity_AGENTS/Краб
git checkout main && git pull
git worktree add .claude/worktrees/session-14 -b claude/session-14
```

## Текущее состояние (Session 13 CLOSED 19.04.2026 22:30 UTC)

- **~46 коммитов Session 13 в main** — Wave 27-29 massive batch: bug fixes (_safe_reply, !bench, !archive, 39 missing commands), features (!health deep, !memory rebuild, /api/health/deep), perf (MMR 49× speedup), stability (OPENCLAW_HEALTH_WAIT_TIMEOUT_SEC=90), observability (14 Prometheus alerts)
- **HEAD:** `7361c2d` fix(tests): MESSAGE_CAP_PER_WINDOW default 20→50
- **Krab:** alive, OpenClaw: flaky (recovers), LM Studio: 401 (normal)
- **User ACL fixed** — OWNER_USER_IDS=312322764 в .env (swarm listeners unblocked)
- **Paywall bypass active** — 4 team accounts (@p0lrdp_AI, @p0lrdp_worldwide, @hard2boof, @opiodimeo) добавлены в p0lrd контакты
- **how2ai incident** — Telegram spam-ban для @yung_nagato, expires 04:11 UTC 20.04.2026 (NOT code bug)
- **Integration tests** — Chado 17/19 fixed, остались: classify_priority signature + CAPACITY import (quick fixes Session 14)

## Прочитай первым делом

1. `/Users/pablito/Antigravity_AGENTS/Краб/.remember/next_session.md` — подробный handoff Session 13 → 14
2. `IMPROVEMENTS.md` в корне — Wave 27-29 learnings
3. `CLAUDE.md` в корне — канонические конвенции проекта

## Session 14 приоритеты (по важности)

### 🔴 Критичные

1. **Memory bootstrap** — когда user Telegram export готов (~500k+ messages, aged account):
   ```bash
   venv/bin/python scripts/bootstrap_memory.py --export <path/to/result.json> --output <result.json>
   ```
   Incremental mode переедет через existing yung_nagato. Сверить counts до/после в archive.db.

2. **Verify how2ai recovered** после 04:11 UTC 20.04.2026 (spam-ban expires):
   - Manual cleanup chat_ban_cache или `!chatban unban -1001587432709`
   - Verify @yung_nagato может постить в how2ai дальше

3. **OpenClaw auto_restart_policy review** — Wave 29-X diagnosis found over-aggressive при CPU load >3× count. Рекомендации:
   - `/Users/pablito/Library/LaunchAgents/ai.krab.core.plist` — добавить `<key>ExitTimeout</key><integer>120</integer>`
   - `ai.openclaw.gateway.plist` — bump `ThrottleInterval` 1→5 (Wave 29-X assessment)

### 🟡 Важные

4. **Session 14 waves in progress** (проверить и завершить):
   - 29-LL: classify_priority test signature fix
   - 29-MM: `ruff stash pop` (190 test files F401 cleanup)
   - 29-NN: CAPACITY import rename в tests
   - 29-OO: DM reactions skip
   - 29-PP: FTS5/vec orphans в health deep

5. **LM Studio load avg optimization** — unload model когда не используется. Load avg хронически 73+, душит OpenClaw на слабеньких CPU-cycles. Рекомендация: `!model switch google/gemini-3-pro-preview` (cloud primary).

6. **Optional: LaunchAgent ExitTimeout** — добавить graceful shutdown timeout для кrab.core plist (Wave 29-X follow-up).

### 🟢 Low

7. **Live benchmark** 29-KK unified is_owner — after ACL file edits propagate to runtime (`5aafe67`)

8. **Dashboard V4 frontend** — delegate to Gemini 3.1 Pro (spec: `docs/DASHBOARD_V4_SESSION10_FRONTEND_SPEC.md`). JS/HTML rule: Gemini only.

9. **`!memory rebuild` end-to-end test** — requires brief Krab downtime для archive.db lock release.

## Known issues carry-forward

- Load avg 73+ chronic (LM Studio main culprit)
- archive.db FTS5 + vec_chunks orphans (repair script ready в `src/core/memory_repair.py`)
- Telegram paid messaging — user может toggle для @p0lrd contacts vs global (need handoff notes)
- Integration test flakes: Chado classify_priority signature mismatch (2 tests)

## Launch commands

```bash
cd /Users/pablito/Antigravity_AGENTS/Краб
/Users/pablito/Antigravity_AGENTS/new\ Stop\ Krab.command && sleep 4 && /Users/pablito/Antigravity_AGENTS/new\ start_krab.command
```

## Архитектура (ключевые модули)

- `src/userbot_bridge.py` — ядро (pyrofork, message processing)
- `src/openclaw_client.py` — OpenClaw API + tool execution
- `src/modules/web_app.py` — Dashboard :8080 (204+ endpoints)
- `src/core/memory_*` — Memory Layer Phase 1 (archive.db 42k+ msgs / 9k+ chunks)
- `src/core/health_deep_collector.py` — `/api/health/deep` diagnostics
- `src/handlers/command_handlers.py` — 154+ команд (added !health deep, !memory rebuild, !archive, etc.)

## Git workflow

- Main branch: `main`
- Worktrees: `.claude/worktrees/*`
- PR через `gh pr create --base main --head <branch>`
- Merge через `gh pr merge <num> --merge` (preserve history)

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

## Важные правила проекта

### Железные

1. **Frontend/CSS/HTML — ТОЛЬКО через Gemini 3.1 Pro API** (gemini-3.1-pro-preview)
2. **Не SIGHUP openclaw** — только `openclaw gateway start/stop`
3. **Krab restart** — только `/Users/pablito/Antigravity_AGENTS/new start_krab.command` + `/new Stop Krab.command`; wait 3-5s между stop и start
4. **Общение на русском**, code comments краткие русские
5. **LM Studio models** — test ONE AT A TIME (RAM overflow на 36GB M4 Max)

### Тестирование

```bash
pytest tests/ -q                             #全て
pytest tests/unit/test_openclaw_client.py -q # specific
ruff check src/ && ruff format src/          # linting
```

---

Добавь это в начало нового чата. После того как прочтёшь handoff и проверишь HEAD (`git log main --oneline -20`), начни с приоритета 🔴 #1 — Memory bootstrap, если user Telegram export готов.

🦀 Давай завершим Session 14!
