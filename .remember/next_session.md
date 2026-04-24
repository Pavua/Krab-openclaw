# Session 22 — Starter Handoff (after Session 21 close, 2026-04-24)

## Status snapshot

- **Sessions 20 + 21 closed** on branch `fix/daily-review-20260421`
- **47 commits shipped** (range `3cb0276..03b9e0d`), pushed to origin
- **+13,458 / −166 LOC**, 90 files touched
- **~120 new tests**, 7/8 E2E cases passing
- Krab running (`ai.krab.core` launchd), OpenClaw Gateway live, archive.db ~506 MB / 752k+ msgs
- Dashboard V4 prod, MCP :8011 LISTEN, Prometheus `/metrics` exposed

## Live in production (enabled by default)

- **Sentry pipeline** — SDK + Performance Monitoring (10% sampling in prod) + `/api/hooks/sentry` webhook → Telegram
- **Cloudflared self-heal** — 2 launchagents keep public tunnel URL in sync with Sentry rules
- **MCP tool expansion** — filesystem, git, system, http (SSRF-guarded), time, db_query, Apple Notes, iMessage, Reminders, Fantastical, dev-loop pack
- **Grafana dashboard** JSON + 4 new Prometheus metrics (`krab_error_digest_fired_total`, `krab_swarm_tool_blocked_total`, `krab_memory_retrieval_mode_total`, `krab_memory_retrieval_duration_seconds`)
- **Routines hardening** — cron warn-spam 1230 → 0, digest/summary orphan fix, `error_digest_loop` 6h → 24h
- **Swarm per-team tool allowlist** (traders/analysts/coders/creative)
- **Memory MMR diversity + query expansion** (opt-in: `KRAB_RAG_MMR_ENABLED=1`)
- **`!diag` command** — one-shot owner diagnostic summary
- **Git post-commit hook** — auto-push + Sentry release resolve

## Phase 2 Memory — READY TO ENABLE

All 8 commits (C1–C8) merged. Flag-gated, default off.

- **Activate:** `docs/PHASE2_MIGRATION_GUIDE.md` — shadow day → flip flag → verify
- **Env-vars:**
  - `KRAB_RAG_PHASE2_SHADOW=1` — shadow reads, zero user impact
  - `KRAB_RAG_PHASE2_ENABLED=1` — live hybrid retrieval
  - `KRAB_RAG_RRF_VEC_WEIGHT=1.0` — RRF vector weight tuning
- **Expected:** Recall@5 +30%, MMR latency 10× faster (50–100ms → 5–10ms)
- **Prerequisites verified:** 72k vectors already in `vec_chunks`, Model2Vec pre-warmed on bootstrap (`cc9829b`)

## Known residual issues (carry-over to Session 22)

1. **FTS5 corruption watcher** — no auto-detect/rebuild (manual `memory_doctor.command --fix`)
2. **LM Studio 401** — local fallback broken, affects `!uptime` and cloud-failure tier
3. **Model2Vec partial load in prod** — fallback to Jaccard sometimes triggers (C4 vec-cache mitigates)
4. **sqlite-vec `vec_chunks_meta` desync** — Session 13 carry-over
5. **`phantom_action_guard` E2E timeout** (flaky, LLM variance — needs retry harness)
6. **OpenClaw Gateway KeepAlive** — occasional boot-out despite watchdog
7. **Named Cloudflare Tunnel** — still using quick-tunnel (ephemeral URL)

## Session 22 priorities (top 5)

1. **Enable Phase 2 Memory in shadow mode** → 24h telemetry → flip to live (migration guide ready)
2. **Model2Vec cold-start root-cause** — instrument `MemoryEmbedder.__init__`, resolve Jaccard fallback
3. **FTS5 watcher + auto-rebuild** — cron `PRAGMA integrity_check` + auto-rebuild from `messages` source
4. **LM Studio 401 resolution** — auth handshake inspection, restore local fallback tier
5. **E2E retry harness** — exponential retry for flaky LLM cases to separate true regressions from variance

## First commands for Session 22

```bash
cd /Users/pablito/Antigravity_AGENTS/Краб
cat .remember/next_session.md              # you're reading it
cat docs/SESSION_21_FINAL_REPORT.md        # full context
cat docs/PHASE2_MIGRATION_GUIDE.md         # activation procedure
git log --oneline -10                      # recent commits
git status                                 # any drift
pytest tests/ -q --tb=no 2>&1 | tail -10   # suite health
curl -s http://127.0.0.1:8080/api/ecosystem/health | python3 -m json.tool | head -30
venv/bin/python scripts/phase2_smoke.py    # confirm vec_chunks integrity
```

## Restart notes

- Krab: `/Users/pablito/Antigravity_AGENTS/new\ Stop\ Krab.command` → wait → `new\ start_krab.command`
- OpenClaw: `openclaw gateway` (NOT SIGHUP)
- MCP: `./scripts/restart_mcp_servers.command` if transport closed
- Memory doctor: `./scripts/memory_doctor.command --fix` if stale chunks
- Single-service kick: `launchctl kickstart -k gui/$(id -u)/ai.krab.core`

## Infrastructure state (end Session 21)

- archive.db: ~506 MB / 752k+ msgs / 72k+ vec_chunks / realtime indexer live
- MCP ports: 8011 (yung-nagato), 8012 (p0lrd) LISTEN; 8013 (hammerspoon) stdio
- Owner Panel :8080 UP (240+ endpoints)
- LaunchAgents active: `ai.krab.core`, `ai.openclaw.gateway`, `com.krab.mcp-yung-nagato`, `com.krab.mcp-p0lrd`, `com.krab.mcp-hammerspoon`, `ai.krab.cloudflared-tunnel`, `ai.krab.cloudflared-sentry-sync`, `ai.krab.workspace-backup`, `ai.krab.log-rotation`, `ai.krab.inbox-watcher`
- Sentry public URL: updated every 60s via `cloudflared-sentry-sync`
- Prometheus scrape target: `http://127.0.0.1:8080/metrics`

## Key files to read for context

- `docs/SESSION_21_FINAL_REPORT.md` — full report, 47 commits broken down
- `docs/PHASE2_MIGRATION_GUIDE.md` — Phase 2 activation step-by-step + troubleshooting
- `docs/MEMORY_PHASE2_IMPLEMENTATION_PLAN.md` — 8-commit architectural plan
- `docs/SENTRY_PERFORMANCE.md` — tracing taxonomy + env-vars
- `docs/KRAB_EAR_TRACE_INTEGRATION.md` — STT → Sentry breadcrumbs
- `docs/ROUTINES_PROFIT_AUDIT.md` — cron job value analysis
- `docs/SWARM_TOOL_PER_TEAM_PLAN.md` — per-team tool allowlist design
- `docs/CRON_JOBS.md` — active cron schedule
- `docs/E2E_RESULTS_LATEST.md` — most recent smoke run (7/8 pass)

## Orchestration reminders

- Parallel orchestration pattern held cleanly across both sessions — reuse
- Feature-flag discipline: every risky change behind `KRAB_*` env-var, default off
- Commit cadence: small, atomic, prefixed `fix|feat|perf|test|docs(scope):`
- NEVER SIGHUP OpenClaw
- Test LM Studio models one at a time (RAM overflow on 36GB M4 Max)
