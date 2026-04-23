# Session 19 — Final Handoff (2026-04-22)

## Quick status (as of end Session 19)
- **35+ commits shipped** in Session 19 (branch `fix/daily-review-20260421`)
- **26 commits** locally ahead of origin before final push
- Infrastructure: Krab live, archive.db ~506 MB, 752k+ msgs, realtime memory indexer active
- Dashboard V4: 7/7 pages complete, hourly sparkline costs, auto-refresh live
- Security: trusted_guests allowlist wired, reaction understanding live

## Live features shipped in Session 19
- **Proactivity levels** — unified level control: silent → reactive → proactive
- **Implicit triggers** — semantic trigger detection (implicit questions, Krab follow-ups, AI aliases)
- **Phantom action guard** — strict prompt + real forward tool + post-processor (no "передал владельцу" phantom)
- **Command blocklist** — per-chat command blocklist (`!cmdblock` / `!cmdunblock`)
- **Memory attribution** — `chat_id` / `timestamp` / `chat_title` fields in `_Adapted` model
- **reply_to passthrough** — parent message text included in LLM prompt (fixes "не вижу reply" bug)
- **Forward batch** — `_process_message_serialized` + `_forward_batch_prompt` wired
- **Image vision (models.json)** — `input[] = ['image']` restored; Gemini now receives photos
- **Reasoning autoscale** — per-request reasoning scaling in model routing
- **Per-account persona** — `[policy]` block injected per-request; neutral tone enforced
- **Swarm loop guard** — prevents runaway swarm iteration
- **Trusted guests allowlist** — `@dodik_ggt` bypass guest XOR, expandable via config
- **Incoming reactions** — who reacted what is understood and logged
- **!forget command** — clear session history on demand; auto-clear on memory queries
- **Daily log rotation** — gzip >50 MB, delete >30d, truncate live if >500 MB
- **Dashboard V4 costs** — hourly sparkline + day-compare + chat-breakdown + auto-refresh

## Known issues / in-flight as of session end
- **!status cmd blocklist regression (W26.1)** — command blocklist may affect !status; verify after push
- **Image DM regression (W26.2)** — photo in owner DM may regress after persona fix; e2e verify needed
- Unstaged changes in `IMPROVEMENTS.md` + 6 `/src/web/v4/*.html` files (not committed — dashboard tweaks)

## Next session priorities (Session 20)
1. **OpenClaw tools expansion** — extend tool manifest in `mcp_client.py` with missing tools
2. **Swarm team tool-per-team** — each swarm team gets a dedicated tool subset (traders: crypto, analysts: search+archive, coders: run+read, creative: img+tts)
3. **Routines profit audit** — benchmark cost vs value of 12 active cron routines; disable dead-weight
4. **Verify W26.1 / W26.2** — !status cmd blocklist + image DM regression e2e tests
5. **MMR diversity penalty** (P2 carry-over) — λ=0.7 relevance / 0.3 diversity in `src/memory_engine.py`
6. **Query expansion** (P2 carry-over) — for queries <3 words: 3 rephrase via Gemini flash, OR merge RRF

## First commands for Session 20
```bash
cat .remember/next_session.md        # you're reading it
git log --oneline -10                # recent commits
git status                           # check any unstaged changes
pytest tests/ -q --tb=no 2>&1 | tail -10  # test suite state
curl -s http://127.0.0.1:8080/api/ecosystem/health | python3 -m json.tool | head -30
```

## Restart notes
- Krab running via launchd, no SIGHUP to OpenClaw
- Restart: `/Users/pablito/Antigravity_AGENTS/new\ Stop\ Krab.command` → wait → `new\ start_krab.command`
- MCP servers: `./scripts/restart_mcp_servers.command` if transport closed
- OpenClaw gateway: `openclaw gateway` (NOT SIGHUP)
- Memory Doctor: `./scripts/memory_doctor.command --fix` if stale chunks

## Infrastructure state (end Session 19)
- archive.db: ~506 MB / 752k+ msgs / realtime indexer live
- MCP ports: 8011 (yung-nagato), 8012 (p0lrd) LISTEN; 8013 (hammerspoon) stdio
- Owner Panel :8080 UP (238+ endpoints)
- OpenClaw Gateway :18789 UP

## Context hints
- Use parallel sonnet agents for bounded tasks (10+ OK)
- Don't blindly fix tests — check WHY they fail first (root cause, not symptoms)
- Gemini: 3 Pro by default, NOT flash (translator exception)
- Model routing: `~/.openclaw/agents/main/agent/models.json`
- `google-antigravity` — НЕ использовать (квота/бан)
- Subprocess: всегда `env=clean_subprocess_env()`
- LM Studio: ONE AT A TIME (RAM overflow на 36GB M4 Max)

## Architecture artifacts (Session 17, still valid)
- Hero (Canva): https://www.canva.com/d/wDX_xg3mClWE0t7
- Engineering (Claude Design): https://claude.ai/design/p/f8108663-9376-444f-8c2c-1e93302a02d6
- Ops (Canva mirror): https://www.canva.com/d/3dkWS667S3h08UB

---

<!-- previous handoff (Session 13) preserved below for reference -->
# Session 13 Handoff — Krab Project

> Session 12 CLOSED: ~20 commits, Chado architecture fully wired, Memory Phase 2 end-to-end, 10+ новых команд, 12+ endpoints

---

## 🟢 REBOOT RESUME NOTE (18.04.2026 ~21:40)

### State at reboot
- **main branch HEAD:** `071e45d` merge Wave 24-28 batch
- **Pipeline active:** parallel-orchestration mode (Sonnet/Haiku, medium reasoning)
- **Krab:** restart initiated after Wave 27-A routing fix (handle_bench/react/uptime/archive wired to dispatcher)
- **Git stash:** `stash@{0}` "wip-wave23f-ruff-tests-cleanup" — Wave 23-F ruff autofix 190 test files, safe to `git stash pop` later
- **Worktree:** `/Users/pablito/Antigravity_AGENTS/Краб/.claude/worktrees/fervent-goldstine-2947a2` (session id `8c162de3-...`)

### Agents still in flight при reboot (могут быть killed)
- Wave 22-C Chado Q6 RAG interview (>30 мин, возможно зависло)
- Wave 28-B `/api/commands/usage/top` endpoint
- Wave 28-C async reminders/ACL/chat_ban_cache load

### Commands to resume
```bash
# Verify Krab alive
curl -s http://127.0.0.1:8080/api/uptime
# MCP dispatcher verify (send !uptime к Yung Nagato)

# Get current todo + continue
cd /Users/pablito/Antigravity_AGENTS/Краб
git log main --oneline -20
git stash list
```

### Resume prompt template (next session starter)
> "продолжаем Session 13 pipeline с точки reboot. Проверь main HEAD 071e45d, git stash pop wip-wave23f-ruff-tests-cleanup, verify Krab alive via MCP, запусти Wave 29. Напомни мне ключевые in-flight items из next_session.md REBOOT RESUME NOTE секции."

---

## Session 13 WAVE 18-20 progress (18.04.2026)

### Wave 18 (post-disk-cleanup recovery)
- **18-A:** session_12 empty bug → **FIXED** (chat_window_manager singleton missing from ecosystem_health reflection)
- **18-B:** Live MCP verify — 5/5 commands pass, 224 endpoints active, all handlers integrated
- **18-C:** Session 13 handoff prep + docs sync
- **18-D:** Disk audit — 16 GB free post-cleanup, npm cache +1.4 GB recovered
- **18-E:** Chado Q5 answered — `disabled: true` validated MVP approach ✓

### Wave 19 (Chado follow-ups & hot-reload)
- **19-A:** `chat_filters.json` hot-reload (mtime polling + `!listen reload` command)
- **19-B:** EXPERIMENTAL_SKILLS_WORKFLOW.md documentation layer
- **19-C:** SKILLS_INVENTORY.md (4 skills, 0 plugin.json, 7 MCP servers catalogued)
- **19-D:** `/api/ecosystem/health/debug` promoted to permanent diagnostic tool
- **19-E:** Hot-reload integration tests (14 pass + 1 skip) — chatfilter, batcher, mempool

### Wave 20 (current live)
- Prometheus metrics expanded (commands/filter/ChatWindow instrumentation)
- Performance benchmark suite (FTS/semantic/ChatWindow/PII latency profiles)
- HotReloadableConfig generic helper class
- `!memory clear --chat|--before` per-chat delete subcommands
- Auto-reactions (👍✅❌⚙️🧠) — context-aware
- Commands usage analytics + `/api/commands/usage` endpoint
- `!loglevel` runtime toggle (DEBUG/INFO/WARN)
- `sync_docs.py` composite regenerator (Prometheus + OpenAPI + command list)
- ChatWindow env config + `/api/chat_windows/evict_idle`
- Voice smoke test suite

### Wave 21 (recovery + wiring + monitoring)
- **21-A:** auto_reactions module recovered (lost commit) + !react toggle
- **21-B:** auto_reactions wired в llm_flow (5 hooks: start/memory/agent/complete/fail) + graceful fallback
- **21-C:** Weekly maintenance LaunchAgent plist + WEEKLY_MAINTENANCE.md docs
- **21-D:** archive growth monitor + /api/archive/growth + anomaly alert
- **21-E:** !uptime extended (Krab + Gateway + LM Studio + Archive + macOS)
- **21-F:** Live verify — все endpoints live, some stale from pre-restart cache

### Wave 22 (Phase 3 prep + Chado Q6)
- **22-A:** Archive growth daily snapshot в nightly summary (`cc3c01c`)
- **22-B:** !archive growth/stats Telegram command (`ad245a3`)
- **22-C:** Chado Q6 interview on RAG retrieval tuning (pending, возможно killed reboot)
- **22-D:** Memory query expansion (synonyms RU+EN, stem) (`8e3ab8e`)
- **22-E:** Reminders persistence + concurrency integration tests (`6ad64c4`)
- **22-F:** !bench Telegram command runner (`5914fcc`)
- **22-G:** Session 13 handoff update
- **22-H:** Auto-reactions live verify via MCP — FOUND DISPATCHER BUG (event loop stall `swarm_task_board_loaded` 200)

### Wave 23 (docs & dashboards)
- **23-A:** Prometheus alert rules YAML (`073f630`) — 8 alerts + README
- **23-B:** Chat filters user guide (`3175ae8`) — 148 lines
- **23-C:** Dashboard V4 spec append Wave 17-22 (`beb84d7`) — 6 new widgets
- **23-D:** `/api/memory/stats` endpoint (`e07dfb1`) — 9158 chunks / 51MB archive.db
- **23-E:** Dashboard summary (initial — killed, retry в 24-C)
- **23-F:** Ruff autofix tests/ — **stashed** `stash@{0}` (190 files F401 cleanup)

### Wave 24 (perf fixes + diagnostics)
- **24-A:** async task_board load — **root cause fix** (`424568f`) + hidden `cleanup_old()` AttributeError fix
- **24-B:** Session lock audit — LOW risk, clock drift main cause, stagger recommended
- **24-C:** `/api/dashboard/summary` single-call aggregator (`b257daa`) — 12 tests, DI pattern

### Wave 25 (follow-up fixes)
- **25-A:** Stagger swarm startup 1.5s + warmup gate (`de6c973`) — 3 tests
- **25-B:** `/api/system/clock_drift` diagnostic (`5d0fec6`) — real offset +0.139s = ok
- **25-C:** IMPROVEMENTS.md Wave 22-25 learnings (`0bac0c9`)

### Wave 26 (maintenance + e2e)
- **26-A:** MCP e2e live verify — **FOUND Wave 21-A/E handlers not routed** (LLM fallback)
- **26-B:** archive.db VACUUM + log rotation 100MB threshold (`433fc15`)
- **26-C:** Ruff per-dir config (`0e45cb2`) — tests 112→1, src unchanged

### Wave 27 (critical fix)
- **27-A:** Routing fix (`9c183f9`) — `USERBOT_KNOWN_COMMANDS` frozenset blocked bench/react/uptime/archive/unarchive (ACL rejected silently → LLM fallback)
- **27-B:** sync_docs.py composite regen (`198a018`) — 190 endpoints / 151 handlers / 8 alerts / 8 metrics autoextract

### Wave 28 (Phase 3 + backlog)
- **28-A:** swarm_research_pipeline profile + non-blocking persist (`5d2e911`) — 31 tests
- **28-B:** /api/commands/usage/top (in-flight reboot)
- **28-C:** async reminders/ACL/chat_ban_cache load (in-flight reboot)
- **28-D:** memory_adaptive_rerank stub (`f74418e`) — MMR + temporal decay + trust, 12 tests

### merge 24-28 batch (`071e45d`)
13 commits консолидированы в main через --no-ff merge с conflict resolution `--theirs`.

### Recovery lessons learned
- **Merge markers** can leak when `git checkout --theirs` in conflicts — ALWAYS grep `<<<<<<< | =======$ | >>>>>>>` after merge.
- **Python .pyc cache** — clear `find src -name __pycache__ -exec rm -rf {} +` when stale SyntaxError in log.
- **Branch confusion** — agents sometimes commit to current branch instead of main. Always `git checkout main` before merging agent worktrees.

### Critical notes saved
`.remember/session_13_critical_notes.md` contains:
- Merge conflict gotchas
- Python cache workarounds
- Live Krab verification workflow
- Session 12-13 achievements recap

---

## Session 12 achievements

### Chado-inspired architecture (How2AI interview learnings Q1-Q3)
- **Per-chat ChatWindow** + LRU cache (`chat_window_manager.py`) — isolates message context per чат
- **Priority Dispatcher** (P0/P1/P2, `message_priority_dispatcher.py`) — queues по приоритету
- **Per-chat Filter** (`chat_filter_config.py` + `!listen` / `!mode`) — мutes/unmutes по чату
- **Message Batcher** backpressure (`message_batcher.py`) — handles concurrent batching
- **Structured Reflector** (pydantic schema, `swarm_self_reflection.py`) — JSON reflection schema
- **Krab Identity** + **Group Identity** (`krab_identity.py`, `group_identity.py`) — 🦀 prefix в groups
- **Full integration в `_process_message`** (Wave 17-A + 17-B) — chatwindow → filter → priority → batcher → reflect

### Memory Layer Phase 2 (LIVE)
- **9131+ chunks encoded** (Model2Vec + sqlite-vec vectors)
- **Hybrid FTS+semantic RRF re-ranker** — full-text search + cosine distance combo
- **`/api/memory/search`** + **`!recall`** command + MCP tools for memory query
- **Live message indexing** as messages arrive in archive.db

### Proactivity (3 levels)
1. **Level 1:** `!cron quick "каждый день в 10:00" "prompt"` — рекуррентные задачи
2. **Level 2:** Reminders queue (time-based, event-based) — `!remind <time> <text>`
3. **Level 3:** Self-reflection → auto follow-ups — structured JSON schema in `swarm_self_reflection.py`

### UX & Resilience polish
- **parse_mode=markdown default** + fallback в `_safe_reply()`
- **Typing keepalive** с explicit CANCEL (context manager)
- **!help pagination fix** для MESSAGE_TOO_LONG в groups
- **!reset ACL fast-path** (direct handler registration, no LLM routing)
- **Auto-failover policy** (opt-in)
- **Auto-restart launchctl** (detects "not loaded")
- **Archive.db size alerts** (500MB/1GB warning)

### New Telegram commands (10+)
`!confirm`, `!reset`, `!recall`, `!remind`, `!cron quick`, `!model info`, `!memory stats`, `!stats ecosystem`, `!digest`, `!listen` (alias `!mode`)

### New API endpoints (12+)
- `/api/memory/search` — semantic + FTS search
- `/api/session10/summary` — session timeline
- `/api/chat_windows/stats` — per-chat window stats
- `/api/message_batcher/stats` — batcher backpressure status
- `/api/swarm/task-board/export?format=csv|json`
- `/api/krab_ear/status` — KrabEar health + active sessions
- `/metrics` — Prometheus scrape endpoint
- session_12 block в `/api/ecosystem/health`

### Tests & Coverage
- **Wave 12-17:** +700+ new tests
- **Memory Layer:** 94% coverage
- **Wave 17 modules:** 86-100% coverage
- **Total:** ~7465 tests (from session 10)

---

## Session 13 priorities

### 🔴 High (critical path)
1. **session_12 block empty fix** — Wave 18-A investigation: why ecosystem_health.session_12 returns empty dict?
2. **Chado Q4+Q5 interview** (postponed from Session 12) — plugin architecture, prod vs experimental skills
3. **p0lrd Telegram Export** (>48h ETA) — when ready, bootstrap ~500k more messages to Memory Layer Phase 2
4. **Dashboard V4 frontend** — delegate Gemini 3.1 Pro (spec ready from Session 11)

### 🟡 Medium (quality)
5. **Live verify Chado modules** after Wave 17 — do ChatWindow+Filter+Priority+Batcher work end-to-end?
6. **Ruff cleanup** src/ and tests/ (total ~580 errors in tests/scripts outside src/)
7. **Memory Phase 3 prep** — query expansion, adaptive re-ranking, chunk sampling strategies
8. **Disk hygiene** — archive.db compaction, log rotation automation

### 🟢 Low (enhancements)
9. Bookmark cheatsheet в Dashboard
10. Add ruff pre-commit hook
11. Context-aware `!listen suggest` — predict mode based on chat activity patterns

---

## Known issues carried forward
- 99% disk usage (user cleaning partition incrementally)
- **session_12 block empty bug** (appears in ecosystem_health — Wave 18-A investigation needed)
- **Chado Q4 timeout** — retry Session 13 with extended budget
- Some locked worktrees may still exist (cleanup in progress)

---

## Infrastructure snapshot (18.04.2026)

| Component | Status | Notes |
|-----------|--------|-------|
| Krab PID | 3515 | codex-cli/gpt-5.4 primary |
| archive.db | 43k+ msgs / 50+ MB | Live indexing active |
| Dedicated Chrome | :9222 | Isolated profile, running |
| MCP yung-nagato | 8011 | Bootstrap complete |
| MCP p0lrd | 8012 | Ready for Telegram export |
| MCP Hammerspoon | 8013 | Registered in Claude Desktop |
| OpenClaw Gateway | 18789 | Gateway mode active |

---

## Launch commands (Session 13)

```bash
# Canonical safe restart
/Users/pablito/Antigravity_AGENTS/new\ Stop\ Krab.command
sleep 4
/Users/pablito/Antigravity_AGENTS/new\ start_krab.command

# If stale (rare)
launchctl bootout gui/$(id -u)/ai.krab.core
sleep 3; pkill -9 -f src.main
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/ai.krab.core.plist

# Test suite
pytest tests/ -q
pytest tests/core/test_chado_integration.py -q  # Wave 17
```

---

## Carry-forward rules (Session 13)

- **Russian communication** always
- **Sonnet/Haiku default** (Opus only for architecture decisions)
- **parse_mode=markdown** in all replies
- **НЕ SIGHUP openclaw** — only `openclaw gateway`
- **Max parallel agents** for Wave 18+ tasks
- **NO destructive MCP commands** without `!confirm`
- **Archive.db alerts** at 500MB / 1GB
- **Wave 17 e2e tests** must pass before next wave

---

## Session 14 priorities

> Session 13 CLOSED 2026-04-19 22:30 UTC — Wave 27-29 massive (46 commits, !health deep, !memory rebuild, MMR 49× speedup, 14 Prometheus alerts, classifier fix pending)

### 🔴 High
1. **Memory bootstrap** — when user Telegram export ready (~500k+ messages, aged account): `venv/bin/python scripts/bootstrap_memory.py --export <path/to/result.json>`
2. **how2ai spam-ban recovery** — expires 04:11 UTC 20.04.2026. Manual cleanup: `!chatban unban -1001587432709`
3. **OpenClaw auto_restart_policy review** — Wave 29-X diagnosis: over-aggressive CPU load >3×. Fixes: ExitTimeout=120 in krab.core.plist, ThrottleInterval 1→5 in openclaw.plist

### 🟡 Medium
4. **Wave 29 in-progress cleanup:** LL (classify_priority sig), MM (ruff pop 190 files), NN (CAPACITY import), OO (DM reactions), PP (FTS5 orphans)
5. **LM Studio load avg** — 73+ chronic, unload when idle. Switch to cloud primary (google/gemini-3-pro)
6. **Integration test flakes** — Chado 17/19 fixed, remaining: classify_priority sig mismatch + CAPACITY import (2 tests)

### 🟢 Low
7. **Live benchmark 29-KK** — unified is_owner after ACL edits propagate
8. **Dashboard V4 frontend** — delegate Gemini 3.1 Pro (spec: docs/DASHBOARD_V4_SESSION10_FRONTEND_SPEC.md)
9. **`!memory rebuild` e2e test** — requires brief Krab downtime for archive.db lock
