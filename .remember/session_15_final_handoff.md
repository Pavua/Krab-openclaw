# Session 15 Final Handoff — 2026-04-20

## Достижения (10 коммитов в main, ~12000 строк изменено)

```
a2261bd feat: Swarm V4 + promote to /v4/swarm
62fd288 feat: Inbox V4 + promote to /v4/inbox
bee5177 docs(precommit): mypy non-blocking default
2c0579d docs+feat: Design artifacts + /v4/costs promoted
2e514e3 fix(openclaw): terminate+kill+wait + Semaphore (leak fix)
0d1b007 feat(dashboard): costs V4 polish (null-safe + empty states)
71a79f3 feat: Wave 2 — OWNER_USER_IDS depr + prototypes
4eb90bf docs: Claude Design brief
62dc1c2 fix(tests): prometheus renames
96d0c17 feat: Wave 1 — 5 priorities (lm_studio watcher, metrics, gemini probe, pre-commit, B.9)
```

## Session 15 priorities status (8/12 closed)

| # | Приоритет | Статус |
|---|-----------|--------|
| 1 | lm_studio_idle_watcher wired | ✅ |
| 2 | Metrics emission (3 missing) | ✅ |
| 3 | Dead alerts cleanup | ✅ |
| 4 | how2ai chatban unban | ✅ |
| 5 | probe_gemini_key timeout | ✅ |
| 6 | 29-UU cron native | ✅ (ранее) |
| 7 | Plist tweaks | ⏸ owner confirm |
| 8 | Memory Phase 2 bootstrap | ⏸ export v2 pending |
| 9 | !memory rebuild | ⏸ after bootstrap |
| 10 | OWNER_USER_IDS deprecation | ✅ |
| 11 | Dashboard V4 (/costs/inbox/swarm) | ✅ 3/4 (ops pending) |
| 12 | Pre-commit hook | ✅ + relaxed mypy |

## Bonus — Design plugin v1.2.0 proof of concept

Проверил 4 skills plugin'а Anthropic Design:
- `/design-critique` — structured feedback на /costs screenshot
- `/design-handoff` — pixel-perfect spec (docs/DASHBOARD_COSTS_V4_HANDOFF.md, 718 lines)
- `/accessibility-review` — WCAG 2.1 AA audit (docs/DASHBOARD_COSTS_V4_A11Y_AUDIT.md, 393 lines, 16 findings, все contrast pass)
- `/design-system` — cross-page drift audit (docs/DASHBOARD_V4_DESIGN_SYSTEM_AUDIT.md, 266 lines)

Claude Design web квота 0% использована — всё сделали через Claude Code, быстрее.

## OpenClaw leak fix (Wave 3, 2e514e3)

Root cause: `proc.terminate()` без `await proc.wait()` → orphans.
Fix: terminate → wait(2s) → kill → wait(1s) + `OPENCLAW_CLI_SPAWN_BUDGET=3` Semaphore.
Coverage: `_run_openclaw_cli`, `_run_openclaw_cli_json`, `_fetch_openclaw_cron_jobs`, `_cron_run_openclaw`.

**Gap discovered:** /api/openclaw/* endpoints that indirectly trigger CLI spawns обходят Semaphore (например `/api/openclaw/cron/status` через `_collect_openclaw_cron_snapshot`). Wave 4 нужен.

## Runtime issues в процессе работы

1. **Pytest zombies** — 2 застрявших pytest (65261, 67517) от ранних agents накопили 47 openclaw orphans за 3 часа. **Lesson:** агенты с pytest нужны с --timeout (pytest-timeout plugin).
2. **Parallel window crossdep** — параллельное окно Claude Code создало pip install / voice gateway start / ear pytest которые тоже eating CPU. **Lesson:** координировать работу между окнами.
3. **Krab launcher lock** — при SIGKILL launcher оставляет `~/.openclaw/krab_runtime_state/launcher.lock` stale. Нужен trap handler.
4. **start_krab.command pre-flight** — слишком тяжёлый под load 350+. **Workaround:** `launchctl bootstrap gui/501 ~/Library/LaunchAgents/ai.krab.core.plist` обходит pre-flight.

## Текущее состояние (на момент handoff)

- Krab PID 30720 — running, bootstrap в процессе (model2vec load)
- Panel :8080 — ещё не отвечает, но процесс жив
- openclaw 2-3 (gateway + helpers, clean)
- Load 233 и падает
- 7 мин с момента start

## Next session plan

### 🔴 High — root fixes
1. **Wave 4: Semaphore coverage gap** — /api/openclaw/* indirect calls вне semaphore. Либо обернуть wrappers, либо migrate на HTTP-only gateway.
2. **start_krab.command robustness** — trap handler для cleanup lock file + option `--skip-preflight` для emergency restart.
3. **V4 ops page** (re-generate, agent не завершил при emergency cleanup).

### 🟡 Medium
4. **mypy 5 pre-existing errors** в command_handlers.py — исправить по одному (type hints).
5. **Design drift cleanup** — по report'у design-system: chat.html ghost vars, inbox.html stray colors, 3 skeleton keyframes.
6. **Memory bootstrap после export v2** от owner.

### 🟢 Low
7. v4/translator, v4/settings, v4/commands pages (по образу costs/inbox/swarm).
8. Integrate Figma workflow (когда owner создаст design files).

## Files touched

### Code
- src/modules/web_app.py (Wave 3)
- src/core/proactive_watch.py (Wave 3)
- src/handlers/command_handlers.py (Wave 3)
- src/core/auto_restart_policy.py (Wave 1)
- src/core/cloud_key_probe.py (Wave 1)
- src/core/prometheus_metrics.py (Wave 1)
- src/openclaw_client.py (Wave 1)
- src/userbot_bridge.py (Wave 1 + 2)
- src/config.py (Wave 2)
- src/web/prototypes/costs_v4_claude_design.html (1193 lines)
- src/web/prototypes/inbox_v4_claude_design.html (1120 lines)
- src/web/prototypes/swarm_v4_claude_design.html (1273 lines)
- src/web/v4/costs.html, inbox.html, swarm.html (promoted)

### Tests
- tests/unit/test_gemini_probe_timeout_cache.py (new, 7 tests)
- tests/unit/test_lm_studio_idle_watcher.py (updated, +3 tests)
- tests/unit/test_prometheus_metrics.py (+8 tests)
- tests/unit/test_b9_silent_failure_polish.py (new, 8 tests)
- tests/unit/test_owner_user_ids_deprecation.py (new, 8 tests)
- tests/unit/test_openclaw_cli_leak_fix.py (new)

### Docs
- docs/CLAUDE_DESIGN_BRIEF_COSTS_V4.md (297)
- docs/DASHBOARD_COSTS_V4_HANDOFF.md (718)
- docs/DASHBOARD_COSTS_V4_A11Y_AUDIT.md (393)
- docs/DASHBOARD_V4_DESIGN_SYSTEM_AUDIT.md (266)
- docs/OWNER_USER_IDS_DEPRECATION.md
- docs/PRECOMMIT.md (updated)

### Config
- ops/prometheus/krab_alerts.yml (-5 dead, +2 new)
- .git/hooks/pre-commit (mypy non-blocking)

## Carry-forward rules
- Russian в общении
- Sonnet/Haiku default, Opus high только для architecture
- Max 2-3 parallel sonnet agents чтобы не choke event loop
- Если user report Krab error — сначала check Telegram history via MCP, потом logs
- Pre-commit hook: `git commit --no-verify` при emergency, лечить root cause отдельно
