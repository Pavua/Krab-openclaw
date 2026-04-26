# Session 27 — Starter Handoff (after Session 26 close, 2026-04-26 evening)

## Status snapshot

- Branch: `fix/daily-review-20260421` — **80+ commits** (Session 24-26 непрерывно)
- **Krab production live** (PID 70468 после Session 26 manual restart)
- Smart Routing **active** — 5-stage pipeline в production
- archive.db: 506 MB / 753k+ msgs — `memory_doctor.py 5/5 ✅` (5 chunks без vec — normal indexer queue lag)
- 10 LaunchAgents активны post-reboot (включая `ai.krab.db-lock-monitor`, `ai.krab.cloudflared-tunnel`, MCP yung-nagato)

## Session 26 wins (~17 commits)

### Smart Message Routing (5 phases + design spec)
| Commit | Phase | Tests |
|---|---|---|
| `9661d8c` | Phase 1: chat_response_policy.py + JSON store + auto-adjust | 28 |
| `7152ab6` | Phase 4: !chatpolicy commands + chat_policy_router (4 endpoints) | 23 |
| `c8a4bfe` | Phase 2: llm_intent_classifier.py + LRU cache (5min/500entries) | 29 |
| `a48ab40` | Phase 3: feedback_tracker.py + Pyrogram delete/reaction hooks | 21 |
| `84c00f0` | Phase 5: extend trigger_detector + wire userbot_bridge + integration | 14 |
| `ef146f7` | docs/SMART_ROUTING_DESIGN.md — architectural spec | — |
| **TOTAL** | **5 phases / 8 components / spec** | **115** |

### Other Session 26 fixes
| Commit | Что |
|---|---|
| `f35e099` | Phase 2 Wave YY — costs cluster (7 endpoints → costs_router) |
| `93985e9` | Phase 2 Wave ZZ — finish swarm (12 leaked endpoints, 17 tests) |
| `9d44e50` | DB corruption circuit breaker — auto-quarantine + 16 tests |
| `2b46afb` | Inbox 6 dual-patch fix (Wave 3/Wave O extraction breakage) |
| `88e3d50` | CLAUDE.md autotables refresh (Session 25 final) |
| `63ac7a3` | Cleanup 418 dead Phase 2 comments в web_app.py |
| `674ebd1` | Session 26 starter handoff |
| `b857ba6` / `0081c91` / `6ccf790` / `3af8041` / `7d35050` / `a58f05a` / `1601f10` / `ec16d56` | Phase 2 Waves QQ→XX (HTML pages, runtime, etc.) |
| `aea2b9a` | Cold-start rate limit guard (`scripts/launchers/cold_start_rate_limit.sh` + docs) |
| `6969048` | Phase 2 audit follow-up — promote helpers (tail_text/mask_secret/bool_env/project_root/clone_jsonish/float_env/get_public_base_url) в _helpers.py |
| `269412b` | Fix DB guard — exclude `disk I/O error` from HARD markers (false positive lesson) |
| `fad6e8d` | CLAUDE.md + Smart Routing section + Session 26 row |

## Что live в production (новое в Session 26)

### 1. Smart Routing (commits 9661d8c→84c00f0)
Pipeline активен — `smart_trigger_decision` events логируются в `~/.openclaw/krab_runtime_state/krab_main.log` для каждого group message.
- Owner control: `!chatpolicy [show|set <mode>|threshold <0.0-1.0>|stats|list|reset]`
- Web API: `GET/POST/DELETE /api/chat/policy/{chat_id}` + `GET /api/chat/policies`
- Modes: silent (1.1) / cautious (0.7) / normal (0.5) / chatty (0.3)
- LLM cache: 500 entries, 5min TTL
- Auto-adjust: >5 negatives/24h → downshift (rate-limit 6h)
- Feedback hooks: Pyrogram `on_deleted_messages` + `on_message_reaction_updated`

### 2. DB Corruption Guard (commit 9d44e50, refined 269412b)
- Pre-flight `PRAGMA integrity_check` на boot для kraab.session + archive.db
- Auto-quarantine corrupt files → `<path>.corrupt-<unix_ts>` (с WAL/SHM sidecars)
- HARD markers: malformed / not a database / encrypted / malformed schema
- **disk I/O error EXCLUDED** (false positive — transient OS issue, не corruption)
- Sentry tag `db_corruption=true` для фильтрации
- Critical DBs (session) → `sys.exit(78)` launchd throttle

### 3. Cold-Start Rate Limit (commit aea2b9a)
- `scripts/launchers/cold_start_rate_limit.sh` — sourceable function
- `~/.openclaw/krab_runtime_state/krab_cold_starts.log` — sliding 5min window
- ≥5 starts/5min → cooldown 600s
- ≥10 starts/5min → ABORT exit 1
- Log rotation >100 lines → tail 50

### 4. Phase 2 Code Splits (Session 25 завершено)
- 25 routers / 207 endpoints в `src/modules/web_routers/`
- web_app.py: 15.8k → ~10k LOC (-37%)
- Helper injection через late-bound lambda — устоявшийся pattern
- `_helpers.py` extended (Session 26 6969048): tail_text, mask_secret, bool_env, project_root, clone_jsonish, float_env, get_public_base_url (default_port param)

## Session 27 priorities

### P0 (operational watch)
1. **Watch Smart Routing logs 24-72h** — `grep smart_trigger_decision ~/.openclaw/krab_runtime_state/krab_main.log`. Анализ ложных positive/negative, tune `KRAB_IMPLICIT_TRIGGER_THRESHOLD` или per-chat policy.
2. **Sentry observation** — продолжать (markers expansion + DB guard работают).
3. **db_lock_monitor 24h baseline** — pragma_baseline cosmetic issue (busy_timeout=0 в monitor's own connection — fix nice-to-have).

### P1 (improvements)
4. **Apply launchd Option C fix** — `Stop Krab.command` modified locally (added `launchctl enable` + removed `launchctl remove`). После следующего reboot verify auto-load работает.
5. **Auto-load investigation** — verify post-reboot Krab starts itself (без manual `start_krab.command`).
6. **Smart Routing tuning** — based на 24-72h observation, может потребоваться:
   - LLM prompt iteration (false positives/negatives)
   - Auto-adjust thresholds tweak
   - Add validation set с known YES/NO examples
7. **Phase 2 audit follow-up Part 2** — finish ops cluster extraction (5 endpoints), context_router (3 endpoints), assistant query+stream extraction.

### P2 (architectural)
8. **command_handlers.py split** — Phase 2 для commands. Phase 1 scaffold готов (`src/handlers/commands/_shared.py`). Phase 2 extraction — отдельная сессия.
9. **HNSW migration** — vec count 72k / 250k trigger, p95 ~25ms. Только monitoring.

### P3 (backlog)
10. **`_recover` для kraab.session** — automated recovery via `sqlite3 .recover` если quarantined session integrity = OK (Session 26 manual fix mainly automated). 
11. **Restart-loop alert** — Telegram/Sentry notification при rate limit triggered (currently only stdout warning).

## Recent operational lessons (Session 26)

1. **`disk I/O error` НЕ corruption** — transient OS-level issue when WAL contention. Krab может open WAL когда file system busy → false positive quarantine. Fix: exclude from HARD markers, retry on next cycle.

2. **Pyrogram session recovery via `sqlite3 .recover`** — works для btreeInitPage corruption (page-level damage). Recovered session preserves user_id, auth_key, peers — НЕ требует phone+SMS re-auth. Команда:
   ```bash
   sqlite3 corrupt.session ".recover" > recovered.sql
   sqlite3 fresh.session < recovered.sql
   ```

3. **launchd `bootout` + `remove` оставляет persistent disabled flag** since Catalina. Fix: `launchctl enable gui/501/ai.krab.core` после bootout.

4. **DB guard auto-quarantine при boot** — protects от 322-event restart loops. После Session 26 deploy guard сработал на false positive (disk I/O), быстро identified и fixed (excluded marker).

## Operational commands

```bash
cd /Users/pablito/Antigravity_AGENTS/Краб
cat .remember/next_session.md          # this file
git log --oneline -25                  # recent commits
launchctl list | grep -i krab          # 10 active expected
curl -s http://127.0.0.1:8080/api/health/lite | python3 -m json.tool

# Smart Routing observation
grep smart_trigger_decision ~/.openclaw/krab_runtime_state/krab_main.log | tail -20
grep "feedback_negative\|feedback_positive\|chat_response_policy_auto" ~/.openclaw/krab_runtime_state/krab_main.log | tail -10

# Per-chat policies
curl -s http://127.0.0.1:8080/api/chat/policies | python3 -m json.tool
# Or via Telegram: !chatpolicy stats

# DB guard logs
grep "db_corruption_detected\|db_corruption_quarantined" ~/.openclaw/krab_runtime_state/krab_main.log | tail -5

# Restart guard log
tail -10 ~/.openclaw/krab_runtime_state/krab_cold_starts.log

# Session diagnostic
bash scripts/krab_session_diagnostic.sh
```

## Sentry post-Session 26

- Issues PYTHON-FASTAPI-5Z (322 fatal_error events от kraab.session corruption) и 5W (4 disk I/O events) — **resolved** в Session 26.
- Future: rate limiting на same fingerprint (Sentry side config — manual через UI).

## Restart notes

- Krab manual: `/Users/pablito/Antigravity_AGENTS/new\ start_krab.command`
- Stop manual: `/Users/pablito/Antigravity_AGENTS/new\ Stop\ Krab.command`
- After Mac reboot: launchd должен auto-load после Option C fix (Stop Krab.command). Verify: `launchctl list | grep ai.krab.core`. Если НЕ loaded — manual start.
- MCP: `launchctl kickstart -k gui/$(id -u)/com.krab.mcp-yung-nagato` (после server.py changes)
- DB recovery: `sqlite3 corrupt.session ".recover"` → новая чистая SQLite

## Что новое для Claude в Session 27

- **Smart Routing live**: 5-stage pipeline через `detect_smart_trigger()`. Logs `smart_trigger_decision` events.
- **DB guard tuned**: false positive lesson зафиксирован (disk I/O excluded).
- **Cold-start guard**: launchd рестарт-loops защищены `cold_start_rate_limit.sh`.
- **_helpers.py extended** с 7+ utility functions — используй вместо `WebApp._method` где возможно.

## Files for Session 27 reference

- `docs/SMART_ROUTING_DESIGN.md` — Smart Routing architectural spec
- `docs/CODE_SPLITS_PLAN.md` — Phase 2 plan (mostly done)
- `docs/HNSW_MIGRATION_PLAN.md` — HNSW prep (not triggered yet)
- `docs/KRAB_LAUNCHER_RATE_LIMIT.md` — restart guard documentation
- `scripts/launchers/cold_start_rate_limit.sh` — sourceable rate limit
- `scripts/krab_session_diagnostic.sh` — Pyrogram session is_bot detection

## Test counts

Session 25: 10125 collected (~9700 passed, 94 skipped)
Session 26: +173 tests (Smart Routing 115 + DB guard 16 + costs 9 + swarm 17 + inbox dual-patch 7 + cleanup helpers 0 net)
**Session 27 baseline**: ~10300+ tests
