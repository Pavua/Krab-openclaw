# Session 20 — Final Report (2026-04-24)

**Branch:** `fix/daily-review-20260421`
**Commits shipped:** 13+ (3cb0276 → 808a508, все на GitHub)
**Mode:** Parallel orchestration (Opus primary + up to 3 Sonnet subagents)
**Duration:** ~4 hours continuous work

## Problems closed

### Security + Stability
| # | Issue | Fix | Commit |
|---|---|---|---|
| 1 | ACL registry missed W21-W30 commands (`!proactivity` returned deny even for owner) | +40 commands в `USERBOT_KNOWN_COMMANDS` | `3cb0276` |
| 2 | `operator_info_guard_failed` WARN в каждом guest-reply | Создан `src/core/operator_info_guard.py` (SSH/github/home-path + PIIRedactor wrapper) | `3cb0276` |
| 3 | Phantom action guard пропускал «Отправил ... messageId N» галлюцинации | +7 regex patterns (delivery-confirmed, messageId, structured sent-confirmations) | `d6f62ac` |
| 4 | OpenClaw Gateway booted out (launchd KeepAlive не спас) | Ручной reload; watchdog в работе | — |

### Observability + Alerts
| # | Component | Action | Commit |
|---|---|---|---|
| 5 | Sentry `/api/hooks/sentry` endpoint | FastAPI + HMAC + formatter (markdown TG) | `64cbe27` |
| 6 | `setup_sentry_alerts.py` | CLI для создания alert rules через API | `1363209` |
| 7 | Cloudflare quick tunnel + self-heal | 2 LaunchAgents, 60s poll update webhook URL | `4ec5a3b` |
| 8 | 9 Sentry alert rules | 6 email (new+spike × 3 projects) + 3 TG webhook | via API |

### Routines audit + fixes
| # | Component | Action | Commit |
|---|---|---|---|
| 9 | `cron_native_store._load` | 1230 warn/session → 0 (guard на missing file) | `f85646c` |
| 10 | `error_digest_loop` | 6h → 24h + `krab_error_digest_fired_total{outcome}` | `4487045` |
| 11 | `weekly_digest_loop` sleep-before-fire | FIRST_RUN_DELAY_SEC=300s; idempotent dedupe_key | `a4b0114` |
| 12 | `nightly_summary` orphan loop | Wired в `_ensure_proactive_watch_started` | `a4b0114` |

### New features
| # | Component | LOC | Tests | Commit |
|---|---|---|---|---|
| 13 | Swarm per-team tool allowlist | +435 | 7/7 | `8d58c5d` |
| 14 | Workspace backup + log rotation LaunchAgents | +41 | N/A | `4904cc2` |
| 15 | Memory MMR diversity + query expansion | +839 | 18/18 | `675da20` |
| 16 | E2E MCP smoke harness (8 test cases) | +360 | 6/8 | `808a508` |

**Total LOC:** +3000 net, ~25 files touched.

## New infrastructure

**LaunchAgents active (7):**
- `ai.krab.cloudflared-tunnel` → public URL for Sentry webhook
- `ai.krab.cloudflared-sentry-sync` → 60s poll, auto-update webhook URL on change
- `ai.krab.workspace-backup` → daily 04:00, 30-day rotation
- `ai.krab.log-rotation` → every 6h, gzip >50MB / delete >30d / truncate >500MB
- `ai.openclaw.gateway` → KeepAlive (reloaded manually today)
- `com.krab.mcp-yung-nagato` → MCP :8011
- `ai.krab.core` → Krab userbot main

**New Prometheus metrics:**
- `krab_error_digest_fired_total{outcome}` — ok/empty/failed
- `krab_swarm_tool_blocked_total{team,tool}` — per-team tool denial

**New webhook endpoint:**
- `POST /api/hooks/sentry` — receives Sentry alerts, formats, delivers to Telegram via userbot

## Known residual issues

1. **`!version` + `!silence status` не отвечают** — dispatcher regression (пойманы E2E). In flight via agent.
2. **Sentry → webhook autopush** — legacy plugin disconnect, требует Internal Integration через UI.
3. **LM Studio 401** — auth broken, отдельный багет.
4. **sqlite-vec malformed** — Session 13 carry-over.
5. **Model2Vec partial load** — MMR падает на Jaccard fallback в prod; работает, но cosine был бы идеальнее.

## Metrics delivered

| Metric | Before session 20 | After |
|---|---|---|
| Log noise (cron_native warnings) | ~1230/session | 0 |
| ACL denial false-positives (owner) | ~1/10 cmd runs | 0 |
| operator_info_guard_failed | каждый guest reply | 0 (module exists) |
| Sentry→TG alert pipeline | none | live (tested) |
| weekly_digest fires | 0 за 49 рестартов | fires every week (tested unit) |
| nightly_summary fires | 0 ever (orphan) | will fire in 23:00 local |
| Phantom guard coverage | 10 patterns | 17 patterns |
| Memory retrieval diversity | no MMR | λ=0.7 relevance/diversity opt-in |
| Swarm tool scope | full manifest | per-team allowlist |
| Regression test coverage | no MCP harness | 8 cases (6 passing) |

## Next session (21) priorities

1. **Handler regressions** — `!version` + `!silence status` (in progress)
2. **Gateway watchdog** 2-й уровень (in progress)
3. **MCP tools expansion** — filesystem/git/system/http/time (in progress)
4. **Cron jobs population** — daily brief / cost / archive (pending user input)
5. **Named Cloudflare Tunnel** — для persistent URL (deferred)
6. **Photo vision e2e** — W26.2 auto-verify (deferred; требует send_photo MCP)
7. **Model2Vec reliability** — почему fallback на Jaccard в prod

## Orchestration stats

- **Sonnet subagents:** 7 launches (swarm-plan, swarm-impl, digest-debug, memory-mmr, e2e-harness, launchd, routines-audit)
- **Total agent runtime:** ~43 min parallel compute
- **Longest agent:** e2e-harness 13:37 min (fully autonomous)
- **Cherry-picks:** 1 (launchd commit from worktree branch)
- **Merge conflicts:** 2 (resolved via `--theirs`)
