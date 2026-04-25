# Session 23 Final Report (2026-04-25 / 2026-04-26)

Branch: `fix/daily-review-20260421`
Commits: 15 in main Krab repo + 1 in Krab Ear (PR #288)
Range: `c35ae0c..0804be4` (Krab) / `3183538` (Krab Ear)

## TL;DR

Session 23 — operational hardening + critical Sentry init drift fix.
- **P0 found and fixed:** Sentry was silently dropping ~150 logger.error/week
  because `LoggingIntegration(level=None, event_level=None)` disabled
  auto-capture. Production sentry_init.py replaced with full integrations
  (LoggingIntegration ERROR + FastApi + Asyncio + Httpx).
- **Cron LLM quality:** all 4 cron jobs now produce real briefs with
  consistent format (few-shot anchored prompts).
- **Sentry webhook → polling:** trycloudflare URL blocked by Sentry
  (silent regression discovered); replaced with 5min polling LaunchAgent
  (zero infrastructure dependencies).
- **reserve_bot FloodWait:** root-cause fixed (97% of all FloodWait events
  came from this single retry-loop). Persistent cooldown survives Krab
  restarts.
- **Test suite:** 324 → **1 failure** (intermittent only, -99.7%) across
  Waves 11-13.
- **Memory layer health:** confirmed 100% via new `memory_doctor.py`
  (chunks ↔ vec_chunks ↔ FTS5 perfectly aligned, 0 orphans).
- **CLAUDE.md autotables:** refreshed with honest counts (249 endpoints,
  105 handlers, 21 metrics, 9991 tests — vs claimed 257/151+/8/8/6826+).
- **KrabEar Sentry hangs:** Swift fixes on isolated branch (PR #288) —
  IPC socket timeout 5s + Auto Layout constraint deactivation.

## Commits

| sha | type | summary | impact |
|-----|------|---------|--------|
| `079395c` | fix(sentry) | resolve script silent-success bug | webhook now correct |
| `e717c51` | test(cleanup) | Wave 12 pollution fixtures | 17→7 fails |
| `05f9244` | feat(sentry) | polling alerts replace broken webhook | 0 infra deps |
| `aef6d07` | fix(cron) | few-shot format anchors + reply_preview | 4/4 jobs real output |
| `eb5668e` | feat(ops) | db_lock_monitor 60min sliding window | baseline 0/24h |
| `438fdc4` | feat(ops) | memory_doctor.py + misdiagnosis closed | 5/5 health checks |
| `e19f5a7` | docs(claude-md) | autotables refresh | honest counts |
| `1b12c46` | test(cleanup) | Wave 13 config singleton stale-ref | 7→1 |
| `be7d01a` | feat(memory) | vec_query duration histogram + alert | HNSW migration trigger |
| `2f1bb8f` | fix(ops) | telegram_flood_wait counter wiring | counter alive |
| `e7829d6` | fix(reserve_bot) | respect FloodWait wait_seconds | -97% FloodWait |
| `7944ddd` | chore(cleanup) | -7 .py.bak files | -1.1 MB working tree |
| `8c803e2` | **fix(sentry) P0** | LoggingIntegration + FastApi/Asyncio + 10 silent excepts | +151 logger.error/wk → Sentry |
| `0804be4` | test(coverage) | 4 high-priority tests | 65→85% on new modules |
| Krab Ear `3183538` | fix(swift) | IPC timeout + constraint deactivation | KRAB-EAR-AGENT-2/3/8 |

## Critical findings

### 1. Sentry was silently dropping logger.error events (P0)

**Symptom:** No alerts for FastAPI 500s, asyncio task crashes, ~150
logger.error events/week.

**Root cause:** `src/bootstrap/sentry_init.py` had
`LoggingIntegration(level=None, event_level=None)` which is *defaults*
that DISABLE auto-capture. Comment said "управляем вручную" but only 2
manual `capture_exception` calls existed in entire codebase.

**Fix:** `8c803e2` — proper integrations + filter expansion guidance.

**Action required after Krab restart:**
- Watch Sentry top issues 24-48h
- Expected initial spam-spike (events that were silent now flow)
- Add expected ones to `_BENIGN_ERROR_MARKERS` in `sentry_init.py:before_send`

### 2. Sentry blocks *.trycloudflare.com URLs in webhook PUT

**Symptom:** `cf_tunnel_sync.sh` returns 200 but webhook URL never updates.

**Root cause:** Sentry validates webhook URLs and rejects trycloudflare
domains (anti-phishing). Existing values held but PUT returns 400 "Not a
valid URL".

**Fix:** `05f9244` — replaced webhook approach entirely with polling
LaunchAgent (`ai.krab.sentry-poll`, 300s interval, dedup via state file).

### 3. reserve_bot retry-loop = 97% of all FloodWait events

**Symptom:** 134/138 FloodWait events per month came from
`auth.ImportBotAuthorization`, with cascade 431s → 393s → ... → 3175s.

**Root cause:** `reserve_bot.start()` had catch-all `except Exception`
that swallowed FloodWait → returned False → Krab restart loop called
again every ~35s → server kept extending wait time.

**Fix:** `e7829d6` — persistent cooldown gate (survives Krab restarts) +
proper FloodWait handling that respects `e.value`.

### 4. vec_chunks_meta "desync" was a misdiagnosis

**Symptom:** Session 13 backlog item flagged this as ongoing concern.

**Root cause:** `vec_chunks_meta` is vec0 extension's INTERNAL config
table (key, value with 3 rows: indexed_at, model_dim, model_name) — NOT
chunk metadata. Real desync check is `chunks.id ↔ vec_chunks.rowid`,
which passes 1:1 (72358 ↔ 72358).

**Fix:** `438fdc4` — `memory_doctor.py` + closed backlog item.

## Operational state

### LaunchAgents (10 active)

| label | interval | status |
|---|---|---|
| `ai.krab.core` | persistent | live |
| `ai.openclaw.gateway` | persistent | live |
| `com.krab.mcp-yung-nagato` (8011) | persistent | live |
| `com.krab.mcp-p0lrd` (8012) | persistent | live |
| `com.krab.mcp-hammerspoon` (8013) | persistent | live |
| `ai.krab.cloudflared-tunnel` | persistent | live (quick tunnel, ephemeral URL) |
| `ai.krab.cloudflared-sentry-sync` | — | **DISABLED** (Sentry blocks trycloudflare) |
| `ai.krab.sentry-poll` | 300s | **NEW** session 23, live |
| `ai.krab.db-lock-monitor` | 3600s | created but NOT loaded (waiting decision) |
| `ai.krab.workspace-backup` | daily | live |

### Memory layer health (verified via memory_doctor.py)

- archive.db: 506.2 MB
- chunks: 72,358 (perfectly aligned with vec_chunks)
- vec_chunks: 72,358 (vec0 model: M2V_multilingual_output, dim=256)
- chunk_messages: 712,404 (0 orphans)
- messages_fts: 72,358 (1:1 with chunks via content='chunks')
- indexer_state: 8 active chats
- ALL 5 health checks ✅

### Test suite

- Total: ~9,491 collected (was 9,489 in Wave 13, +2 from coverage)
- Passed: 9,490
- Failed: 1 intermittent (`test_contextual_reactions::test_gratitude_with_random_below_rate`)
- Skipped: ~94 (10 wave12 + others)
- Runtime: ~520s full suite

### Prometheus

- 11 alerts (added VecQueryLatencyHigh)
- 21 metrics total (added krab_telegram_flood_wait_total + krab_vec_query_duration_seconds)
- Endpoint `/metrics` exports merged manual + prometheus_client REGISTRY

## Tails for Session 24

### High priority
1. **Restart Krab** to apply Sentry init changes from `8c803e2`. After
   restart, watch Sentry for 24-48h spam-spike and extend
   `_BENIGN_ERROR_MARKERS` in `src/bootstrap/sentry_init.py:before_send`.
2. **KrabEar PR #288 verification + merge** — when parallel session's
   merge-train completes, verify PR base `codex/krab-ear-v2` has no
   conflict, run `xcodebuild test`, smoke 5min, merge.

### Medium priority
3. **Health endpoint extension** (`/api/health/deep`): add
   `sentry.initialized`, `mcp_servers.{8011,8012,8013}`, `cf_tunnel`,
   `error_rate_5m`. ~2-3h.
4. **db_lock_monitor LaunchAgent loading decision** — currently zero
   events 24h, but loading provides ongoing safety net.
5. **HNSW migration trigger** — alert ready (`VecQueryLatencyHigh` p95
   >100ms). When fires (likely at 250k+ vectors), 1-day migration to
   hnswlib + binary-serialized index.

### Low priority / architectural
6. **Code splits** — `command_handlers.py` 19,637 LOC (175+ commands),
   `web_app.py` 15,822 LOC (249 endpoints). Both highly painful but
   require dedicated architectural session with test discipline.
7. **1 flaky test** — `test_gratitude_with_random_below_rate` (random
   seed). Not blocking.
8. **9 raw `print()` calls in src/** — replace with structured logger
   (tracked but low impact).

### Operational watch
- Sentry events post-restart (need expansion of BENIGN markers)
- vec query latency creep (metric live)
- DB lock recurrence (monitor exists, not loaded)
- `ai.krab.sentry-poll` reliability (working since session 23 launch)
