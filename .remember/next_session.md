# Session 34 — Starter Handoff (after Session 33 close, 2026-05-02 23:20)

## TL;DR

- **main HEAD**: `6722d5b` — Sessions 31-33 + Wave 15 fully merged
- **Session 33 delivered**: ~36 commits в один день (Wave 5-15 batch)
- **Total commits сегодня**: 71+ merged via `ffc1324` (no-ff strategy A)
- **Krab live**: state=running, session=ready, integrity=ok
- **Production stability**: corruption events stuck at 225 (no new since 22:42 restart, 1+ hour stable)
- **All tests gated**: 11672 collected, 3 hanging files skipped (Wave 16 backlog)

## Session 33 — что сделано (2026-05-02)

### Defense in depth (Wave 5-6 + 14-J)
- **6-pragma stack**: WAL + busy_timeout=5000 + synchronous=FULL + wal_autocheckpoint=1000 + temp_store=MEMORY + cache_size=64MB
- **4 Pyrogram method wraps**: update_usernames + update_peers + update_state + remove_state (generic factory `_make_safe_method`)
- **Integrity-gate** при startup + **auto-recover** через `sqlite3 .recover` (Wave 5-D)
- **Hypothesis fixed**: `synchronous=FULL` НЕ покрывает temp_store → torn writes на /tmp при disk pressure → "disk image malformed". `temp_store=MEMORY` устраняет этот класс corruption полностью

### UX layers (Wave 7+9+14)
- **Bug 14 cap subtask-aware** (skip fallback message если tool succeeded)
- **Hallucination guard** (10 patterns + verified entries trust)
- **Bot/userbot routing instruction** (Дашуля incident — Wave 7-A)
- **Codex-cli hang detection 45s** + **silent fallback** к openai/gpt-5.5 (Wave 14-D + 14-K)
- **`LLMRetryableError` wired** к auto-retry helper (Wave 14-K)
- User видит **"⏱️ Переключаюсь на резервную модель..."** вместо generic "Ошибка"

### Concurrency (Wave 14-A+B)
- **forward_batch coalescing**: 3 messages → 1 в одном AI call (photos as `[фото] caption` markers)
- **OpenClaw semaphore**: 3 concurrent (env `KRAB_OPENCLAW_MAX_CONCURRENT`, range 1-10)

### Observability (Wave 14-F)
- **Sentry per-session dedupe** (226× → 1× per process), env `KRAB_SENTRY_DEDUPE_MODE`
- **misc_router.notify**: 500 → 503 wrap для DBError
- **CancelledError frame-aware filter** (только lifespan/uvicorn shutdowns suppressed, real timeouts visible)

### Architecture (Wave 11-13)
- **CLI tool_calls_executed contract** design + Krab-side parser (Wave 11-C + 12-C)
- **Hermes Agent** evaluation: 30% coverage parity → НЕ мигрируем, OpenClaw остаётся
- **3 design docs**: `HERMES_EVAL_REPORT.md`, `HERMES_PHASE1_DRY_RUN.md`, `HERMES_ACP_BRIDGE_DESIGN.md`
- **Wave 15-D**: Hermes Phase 2 Phase A live — `~/.hermes/` configured, launch script ready, `hermes acp` smoke verified (stdio JSON-RPC, не HTTP)

### CLI Telegram MCP isolation (Wave 9-B + 10-A)
- **codex-cli** + **claude-cli**: telegram MCP physically disabled (avoids hallucinated tool reports)
- gemini-cli + opencode: never had exposure (verified)

### Inbox & cron (Wave 8-A + 9-C + 14-G)
- **Janitor**: `proactive_action` + `owner_request` acked→done auto-transition
- **Cron look-ahead** semantics formalized (5-day silence regression Session 31 закрыт)

### SkillCurator (Wave 14-I + 15-C)
- **Step 1/4**: `!curator dry-run [team]` read-only analyzer (success rate, top failure/success patterns)
- **Step 2/4**: `!curator propose <team>` LLM analyzer через gemini-3-flash (~$0.0005 per proposal, $0.10/year)
- **Commands**: `!curator dry-run | propose | proposals | show | help`

## Wave 15+ backlog (для Session 34+)

### High priority
1. **Hermes Phase B** — ACP bridge для одной swarm room (analysts):
   - Pin `agent-client-protocol==0.9.0` в Krab venv
   - Implement `src/integrations/hermes_bridge.py` (Popen wrapper, ACP session lifecycle)
   - Feature flag `KRAB_HERMES_BRIDGE_ENABLED` (default OFF)
   - Route только analysts initially
   - Prometheus metrics `krab_hermes_bridge_*`
   - Unit + integration tests
2. **SkillCurator Step 3** — `apply_with_approval(proposal_id)`:
   - Snapshot current prompt → `prompts_archive/{team}/v{N}_{ts}.md`
   - Write new prompt → `swarm_team_prompts.py` или runtime overlay
   - Mutex per-team + idle check
   - Manual `!curator apply <id>` command
3. **24h Sentry observation** post Wave 14:
   - Verify corruption events drop к ~0 over 24h
   - Track `pyrogram_sqlite_malformed_swallowed` count
   - Health probe success rate trend

### Medium priority
4. **SkillCurator Step 4** — A/B framework (round-robin 10 rounds, control vs candidate)
5. **Hermes Phase C** — gradual swarm rollout если metrics ok
6. **Wave 16: Pre-existing test hangs fix** (currently skipped, 3 files):
   - `test_web_acl_api.py`, `test_photo_dm_owner.py`, `test_reply_media_extraction.py`
   - Pattern: starlette TestClient hangs without proper AI runtime mock
7. **KrabEar AppHang** investigation (98 events all-time, defer to Krab Ear repo)
8. **Paperclip server start** (user action: `npx paperclipai run` + onboarding) → Krab integration

### Low priority
9. Hermes selective cherry-picks (agentskills.io, Honcho memory plugin)
10. OpenClaw → Hermes migration tool inside Krab
11. IDE integration via ACP (VS Code/Zed/JetBrains use Krab as agent)

## Operational quick reference

```bash
cd /Users/pablito/Antigravity_AGENTS/Краб

# Krab control
"/Users/pablito/Antigravity_AGENTS/new Stop Krab.command"
"/Users/pablito/Antigravity_AGENTS/new start_krab.command"

# Health
curl -sS http://127.0.0.1:8080/api/health/lite | python3 -m json.tool

# Pragmas verify
sqlite3 data/sessions/kraab.session 'PRAGMA journal_mode;'  # → wal (persistent)

# Curator (Wave 14-I + 15-C):
# !curator dry-run [team]   — read-only analyzer
# !curator propose <team>   — LLM proposes prompt diff (gemini-3-flash, ~$0.0005)
# !curator proposals        — list pending
# !curator show <id>        — display diff

# Hermes Phase A (Wave 15-D, не в production):
bash scripts/start_hermes_standalone.command  # stdio JSON-RPC, не HTTP

# Memory recluster (Phase 2 ready):
venv/bin/python scripts/memory_recluster.py --num-clusters 50
```

## Operational notes (важно)

- **Pre-commit hook** иногда auto-stage'ит файлы соседних агентов в commit — verify после dispatch
- **Memory pressure**: при rapid Stop+Start ловим `disk I/O error`. Mitigation: wait 30s между cycles
- **launchctl kickstart -k**: НИКОГДА не использовать (causes session corruption per CLAUDE.md feedback)
- **Multi-agent dispatch**: Sonnet работает плотно (5-7 agents OK)
- **Reasoning depth**: medium fine для оркестрации, high когда архитектурные решения

## Final state snapshot (2026-05-02 23:20)

```
Branch: main (HEAD = 6722d5b)
Commits today on main: 76 (since 665c3f3 Session 31 close)
Krab process: running
Session integrity: ok
Pragmas live: synchronous=FULL, temp_store=MEMORY, 64MB cache, WAL+autocheckpoint
Health probes: silent (vs every-13s pre-Wave-14)
Corruption events: 225 (frozen since 22:42 restart, 1+ hour stable)
Test base: 11672 collected, 30+ skipped (3 hang files + Wave 14-K stale)
Memory Phase 2: 72k chunks → 50 clusters indexed
~/.hermes/: configured (Phase A, dormant standalone)
~/paperclip/: bootstrapped (skill memory only, server dormant)
```

## Session 34 P0 priorities

```
P0:
1. Verify 24h post-Wave-14 stability (corruption count, probe success rate)
2. Hermes Phase B: hermes_bridge.py + agent-client-protocol pin + feature flag
3. SkillCurator Step 3: apply_with_approval + mutex + archive

P1:
4. SkillCurator Step 4: A/B framework
5. Wave 16: starlette TestClient hangs fix
6. Krab restart to pick up Wave 15-B curator metadata (curator visible in /api/commands)
```
