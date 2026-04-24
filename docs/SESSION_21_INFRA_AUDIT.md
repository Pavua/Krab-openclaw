# Session 21 — Infrastructure Audit Snapshot

**Date:** 2026-04-24
**Branch:** `fix/daily-review-20260421`
**Scope:** post-session 20 infrastructure audit; no remediation performed.

## Component status

| # | Component | Status | Evidence |
|---|-----------|--------|----------|
| 1.1 | `ai.krab.core` | OK | pid 85626, exit 0 |
| 1.2 | `ai.openclaw.gateway` | OK | pid 4141, exit 0 |
| 1.3 | `ai.krab.cloudflared-tunnel` | OK | pid 16829, exit 0 |
| 1.4 | `ai.krab.cloudflared-sentry-sync` | OK | waiting, exit 0 (StartInterval) |
| 1.5 | `ai.krab.workspace-backup` | OK | waiting, exit 0 |
| 1.6 | `ai.krab.log-rotation` | OK | waiting, exit 0 |
| 1.7 | `ai.krab.gateway-watchdog` | OK | waiting, exit 0 |
| 1.8 | `com.krab.mcp-yung-nagato` | WARN | pid 82805, last exit **-15** (SIGTERM) — respawned but prior crash |
| 1.9 | `com.krab.mcp-p0lrd` | WARN | pid 82809, last exit **-15** |
| 1.10 | `com.krab.mcp-hammerspoon` | OK | waiting, exit 0 |
| 1.11 | `ai.krab.oauth_refresh` | BROKEN | exit **127** — command-not-found class error |
| 1.12 | `ai.krab.signal-ops-guard` | WARN | exit 2 |
| 1.13 | `ai.krab.leak-monitor` | WARN | exit 1 |
| 1.14 | `ai.openclaw.signal-cli` | WARN | exit 1 |
| 2 | Cloudflare tunnel URL | OK | `https://mixer-ignored-object-harder.trycloudflare.com` (04:02Z); `/api/health/lite` reachable internally |
| 3.1 | cron `daily-morning-brief` | OK | last_run 08:00, runs=1 |
| 3.2 | cron `archive-growth-alert-6h` | OK | last_run 18:00, runs=3 |
| 3.3 | cron `cost-budget-midday-check` | OK | last_run 13:00, runs=1 |
| 3.4 | cron `daily-evening-recap` | PENDING | last_run=None, runs=0 (scheduled for >18:00; acceptable if not yet due, otherwise flag) |
| 4.1 | `/metrics` exports `krab_*` | OK | validator, archive, LLM route, reminders, command invocations present |
| 4.2 | New metrics `memory_retrieval\|error_digest\|swarm_tool_blocked` | **BROKEN** | grep count = **0** — not exported after restart |
| 5 | Sentry webhook signature gate | OK | POST without sig → **401 signature_missing** (expected) |
| 6 | Git `post-commit` hook | OK | installed, executable (3097 bytes, `KRAB_AUTOPUSH` + Sentry auto-resolve) |
| 7.1 | MCP SSE :8011 (yung-nagato) | OK | `event: endpoint` returned |
| 7.2 | MCP SSE :8012 (p0lrd) | OK | `event: endpoint` returned |
| 7.3 | MCP SSE :8013 (hammerspoon) | BROKEN | empty response on GET `/sse` |

## Cloudflare tunnel live URL

`https://mixer-ignored-object-harder.trycloudflare.com` (started 2026-04-24T04:02:37Z).

## Action items (priority-ordered)

1. **[HIGH] New metrics not exported** — `memory_retrieval`, `error_digest`, `swarm_tool_blocked` missing from `/metrics` even after Krab restart. Verify Prometheus collector registration (likely in `src/core/prometheus_metrics.py` or module that owns the new counters) is imported on bootstrap; probable root cause is a dormant import in `src/bootstrap/runtime.py` or the new module never being instantiated.
2. **[HIGH] `ai.krab.oauth_refresh` exit 127** — "command not found". Check plist `Program`/`ProgramArguments` path (likely stale venv or script moved); `launchctl print` для плагина.
3. **[MED] MCP Hammerspoon SSE :8013 empty** — other MCPs return `event: endpoint`, 8013 returns 0 bytes. Plist says `waiting` (no active process). Launch on-demand may be broken; test with `curl -N` after warm request or check `com.krab.mcp-hammerspoon` stderr log.
4. **[MED] MCP SIGTERM history** — yung-nagato and p0lrd both show exit `-15`. Respawned cleanly but check logs for OOM / watchdog kill pattern, especially under Session 20 load.
5. **[LOW] `daily-evening-recap` cron** — re-verify after 20:00 local; if still `last_run=None` past scheduled slot, inspect scheduler filter.
6. **[LOW] Ancillary exit≠0** — `signal-ops-guard` (2), `leak-monitor` (1), `openclaw.signal-cli` (1) — документируют периодические failures; not blocking but should have stderr triage.

## Green areas

Core runtime, gateway, Cloudflare tunnel, cron native scheduler (3/4 jobs firing), Sentry webhook hardening (C2 fix holding — 401 as specified), git hooks pipeline, and 2/3 MCP SSE endpoints — all healthy.
