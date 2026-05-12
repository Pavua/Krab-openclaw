# Session N+2 — Starter Handoff (Session 46 close, 2026-05-12 ~05:00)

> **Project**: Krab (Telegram userbot). Этот handoff — ТОЛЬКО про Krab.
> Krab Ear имеет свой handoff в `/Users/pablito/Antigravity_AGENTS/Krab Ear/.remember/next_session.md`.
> См. [docs/PROJECT_SEPARATION_GUIDE.md](../docs/PROJECT_SEPARATION_GUIDE.md).

## TL;DR — Session 46 closed 2026-05-12 (post unscheduled reboot), **5 commits**: Wave 63-B/63-C/63-D + Wave 65-K + AGE-13 closed, **all 4 ecosystem components теперь под launchd** (reboot resilience)

**FINAL STATE**:
- **main HEAD**: `b0f20b1` (AGE-13 test coverage gaps closed)
- **Recent commits**:
  - `b0f20b1` AGE-13: krab_daily_maintenance test coverage (16 tests)
  - `c3d883d` Wave 65-K: /api/network/probes endpoint + runtime_summary sub-section
  - `2bbeb2d` Wave 63-D: surgical recovery (gate=OFF, observability-only mode)
  - `9a95258` Wave 63-C: dispatcher_tick hook (outcomes-not-heartbeats для message dispatch)
  - `7390be6` Wave 63-B: per-client GetState probe для swarm Telegram-сессий
- **Krab core**: alive под `ai.krab.core` LaunchAgent (PID auto-restart on crash)
- **Все 4 компонента под launchd**: `ai.krab.core`, `ai.krab.ear.backend`, `ai.krab.ear.rest`, `ai.krab.voice-gateway` (Wave 68) — auto-load on reboot ✅
- **Tests**: +57 across 5 waves (8 dispatcher_recovery + 8 network_probes_api + 10 swarm_split_brain + 8 dispatcher_tick + 16 daily_maintenance + 7 retroactive)
- **Linear**: AGE-13 closed
- **Live verified**: `/api/network/probes` returns expected fields, paid_gemini_guard.mode=block активен после рестарта

## Session 45 retrospect — billing leak + 3 paradigm shifts

- **main HEAD pre-46**: `21c2cbc` Wave 67 (hard paid Gemini guard)
- **Sentry quota saved**: **>1000 events/week** stop firing
- **Linear**: 7 issues closed (AGE-5/6/8/9/12/15/16). Krab backlog: AGE-13 closed in S46. Krab Ear AGE-14/10 — отдельный repo.
- **Billing**: paid AI Studio leak stopped via Wave 66-A/B + Wave 67 runtime guard. Future Gemini traffic → caramel-anvil-492816-t5 bonus credits (€848 до 2027-03).

## 🎯 Heroic fixes (3 paradigm shifts) — «outcomes-not-heartbeats» pattern

| Wave | Commit | Эффект |
|---|---|---|
| **64** | `4f279cc` | SQLite corruption fix: `journal_mode=WAL→DELETE` + `fullfsync=1`. Migration автоматическая. 22 теста. |
| **63-A** | `145d6a9` | Split-brain detection 93min → **4min** via `updates.GetState` pts probe. 21 тест. |
| **50-B** | `cba58cf` | OAuth force-refresh при expiry<-60min. Verified -1492→60 min fresh. |

## Wave 62 series — Sentry hygiene (7 commits)

* `a62e311` 62-C: is_owner_dm via ACL (Wave 60-A wiring)
* `739b8f2` 62-D: cloud routing bypass local-first
* `83d0544` 62-E: gemini_rerank → 2.5-pro (**-9 Sentry/day**)
* `7546573` 62-F: benign markers
* `2c7dc4d` 62-G: codex weekly quota preempt (save 2-3s/request)
* `d9ba689` AGE-8: memory_doctor regression test
* `a140ee6` 62-H: footer cosmetic «codex weekly quota»

## Wave 65 series — operational + UX (8 commits)

* `9cbb61d` 65-A/B: leak_monitor Chrome filter + nightly-audit RunAtLoad
* `49e6afc` 65-C: swarm DM sender identity (AGE-16, Coders → «Создатель»)
* `148bef9` 65-D: anthropic sonnet-4-5 preempt (**-7 Sentry/day**)
* `870d36e` 65-E: two-tier swap thresholds (**-88% Telegram noise**)
* `4a954a9` 65-G: LM Studio idle unload alias + 10 tests (Wave 29-RR shipped earlier)
* `866359e` docs: CLAUDE.md Session 45 update
* `6ba12e1` 65-H: Sentry-poll direct API (replace bash curl 15s timeout с httpx 30s + retries)
* `fab8319` 65-F: conftest guard для test artifact leak (Session 39/40/44+ recurring pattern закрыт)

## 🚨 Wave 66 — Billing leak fix (post Session 45)

User notified that paid AI Studio API key tucked €40 за неделю. Two leak paths found:
* `gemini_rerank_provider.py` (Memory Phase 2 hybrid retrieval per-message) — primary leak
* `google_genai_direct.py` (Wave 18-B for google/*) — secondary leak

Both fixed: preferred Vertex mode (vertexai=True + project=caramel-anvil-492816-t5).
Safety belt: `GEMINI_PAID_KEY_ENABLED=0` в .env. Wave 67 in progress (hard runtime guard).

Anthropic Vertex quota — pending Google Sales POC (Cases #70886393 + #70886496 still open).

Commits:
* `1a1dc39` Wave 66-A: gemini_rerank Vertex mode
* `8f73871` Wave 66-B: google_genai_direct Vertex mode preferred

## Operational state

* Codex weekly quota disabled in `codex_quota_state.json` (7d cooldown from 2026-05-11 22:50, auto-recover ~2026-05-18)
* `~/.codex/config.toml`: MCP context7 `type="streamable_http"` fixed (5 OpenClaw cron jobs работают после 8 days down)
* kraab.session journal_mode=delete, fullfsync=1, 500 peers preserved
* All 5 Pyrogram sessions (kraab + 4 swarm) journal_mode=delete
* Inbox 40 stale acked, 23 corrupt session backups archived в `/tmp/krab_session_corrupt_archive_20260511/`
* leak_monitor count: 20 false-positives → 1 real (Chrome browser-bridge excluded)
* sentry-poll: bash → Python (httpx + retries, persistent cursor)

## Architecture patterns shipped

**«Outcomes-not-heartbeats» principle** — check actual outcomes, not process aliveness:

1. **Wave 63-A**: detect split-brain через server pts vs local seen_id
2. **Wave 50-B**: pre-empt OAuth refresh если expiry past (don't trust "already synced" flag)
3. **Wave 65-D**: pre-empt model call если no quota (env-driven blacklist)
4. **Wave 62-G**: pre-empt codex weekly quota (read state file, skip subprocess)
5. **Wave 65-H**: replace 15s curl timeout с httpx + retries (handle transient timeouts)

## Background agents (12+ Sonnet successful, 1 Haiku failed)

* Sentry/Linear/log scan/memory pressure/routines triage (5 agents, 30-50 min each)
* AGE-15 SQLite corruption research (5 min)
* AGE-8 fix (5 min)
* Wave 63-A/64/65-C/65-F/65-G/65-H implementations (5-32 min each)
* Cron jobs investigation (18 min)

Lesson: **Sonnet for codebase context (200KB CLAUDE.md), Haiku only for one-shot lookups without project context.**

## Pending для Session 46

### Krab-side
* ✅ **Wave 67** shipped (commit `21c2cbc`) — paid_gemini_guard registered live `mode=block`.
* ✅ **Wave 63-B/C/D** shipped (S46).
* ✅ **AGE-13** closed (S46, 16 tests).
* ✅ **Voice Gateway launchd plist** shipped (Wave 68, ai.krab.voice-gateway).
* **Anthropic Vertex quota approval** — pending Google Sales POC contact (Cases #70886393 + #70886496). Contact Sales form submitted 12 May. Meanwhile Wave 65-D preempts claude-sonnet-4-5.
* **Verify Wave 66 billing fix** — после 24h observe paid_gemini_guard logs (zero PaidGeminiGuardError raises = success).
* **Wave 63-D enable** (после 1-2 недели данных): `KRAB_DISPATCHER_RECOVERY_ENABLED=1` в .env когда наберём false-positive baseline на dispatcher_starved_detected.
* AGE-11 — Low priority daily review test gaps (если останутся после AGE-13)
* Verify Wave 64 reduces corruption recurrence (1-week observation window — продолжается)
* Memory pressure optimization (routines findings): `casual_chat_low_priority` → cloud Gemini 3 Flash при peak hours? OrbStack stop on idle?
* Prometheus alert на `main_dispatcher_tick_ago_sec > 600` через новый `/api/network/probes` endpoint (Wave 65-K).

### Krab Ear-side (отдельный repo)
* AGE-14: KRAB-EAR-AGENT-G AppHang ≥2000ms
* AGE-10: KRAB-EAR-AGENT-8 AppHang regression

### Routine maintenance
* Через 1 неделю проверить Sentry quota usage drop (Wave 65-E + Wave 62-F + Wave 65-D)
* Codex weekly quota recover ~2026-05-18 → удалить flag из codex_quota_state.json или auto-recovery
* Inbox cleanup cron `ai.krab.inbox-cleanup` — почему накопились stale 40? Cron работает но не bulk-acks?

## Quick commands

```bash
# Krab restart (НЕ SIGHUP openclaw!)
/Users/pablito/Antigravity_AGENTS/new\ Stop\ Krab.command
/Users/pablito/Antigravity_AGENTS/new\ start_krab.command

# Verify Wave 64 sticks
sqlite3 /Users/pablito/Antigravity_AGENTS/Краб/data/sessions/kraab.session "PRAGMA journal_mode;"
# expect: delete

# Verify Wave 63-A active
grep "get_state_probe_enabled" logs/krab_launchd.out.log | tail -1
# expect: =True

# Live status
curl -sS http://127.0.0.1:8080/api/model/status | python3 -m json.tool

# Inbox stale cleanup (manual)
curl -sS -X POST http://127.0.0.1:8080/api/inbox/bulk-ack-stale | python3 -m json.tool

# Tests
venv/bin/python -m pytest tests/unit/test_pyrogram_patch_wave64.py tests/unit/test_network_watchdog_wave63a.py tests/unit/test_sentry_poll_wave65h.py tests/unit/test_lm_idle_unload_wave65g.py -q
```

## Memory anchors

* Tour produced **20+ commits** в single session — highest commit density
* «Outcomes-not-heartbeats» — emerging Krab architecture principle
* Sonnet quota at 27% post-tour, Opus quota 82% (user работает на Opus, тур использовал ~11 Sonnet agents)
