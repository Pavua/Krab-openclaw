# Session 26 — Starter Handoff (after Session 25 close, 2026-04-26)

## Status snapshot

- Branch: `fix/daily-review-20260421` (≥30 commits в Session 24+25)
- **Krab production live**, MCP yung-nagato restarted (PID 39304)
- archive.db: 506 MB / 753k+ msgs / **72,362 chunks** ↔ 72,362 vec ↔ 72,362 FTS — `memory_doctor.py 5/5 ✅`
- **10 LaunchAgents активны** (`ai.krab.db-lock-monitor` loaded в Session 24)
- **Phase 2 Code Splits ACTIVE** — Wave A→H в Session 25, **36 endpoints / 10 routers extracted**

## Session 25 wins (~17 commits)

### Phase 2 Code Splits
| Wave | Commit | Router | Endpoints |
|---|---|---|---|
| PoC | `db6d9fd` | version_router | 1 |
| - | `d936969` | meta_router | 2 |
| - | `e5cfa44` | inbox_router | 5 |
| Test fix | `c4aab65` | (dual-patch) | - |
| A | `facde49` | commands_router | 4 |
| B | `e95e91f` | memory_router | 2 |
| C | `7d0f2f8` | swarm_router | 8 |
| D | `e04d802` | runtime_status_router | 4 |
| E | `18407e0` | monitoring_router | 6 |
| Foundation | `9563720` | (RouterContext + _helpers infra) | - |
| F | `ff2521f` | extras_router (1st RouterContext-based) | 2 |
| G | `59f2283` | runtime_inspect_router | 2 |
| **Total** | **10 routers** | | **36 endpoints** |

### Userbot capabilities P0 fixes
- `5c66495` — `telegram_send_message` API extension: `reply_to_message_id`, `quote_text`, `parse_mode`, `disable_web_page_preview` + `telegram_session_info` diagnostic tool. Persona `yung_nagato.md` upgraded (50+ строк userbot guidance).
- `c82bd0f` — `scripts/krab_session_diagnostic.sh` для read-only `is_bot` detection через SQLite RO. **Подтвердило: все 5 active sessions = USERBOT** (Yung Nagato галлюцинировал про "bot mode").
- `3d5b60f` — `peer_id_invalid` graceful handling: auto-retry через `client.get_chat()` (populate cache), structured error response с hint вместо raise. Persona explicit: НЕ говорить "я бот" при peer_id_invalid.
- `b6cafbe` — Persona preventive guidance: 4-step protocol перед отправкой user'у (username → get_dialogs → forward → structured complaint).

### Что live в production
- /api/health/deep 12 секций (Session 24)
- 10 routers с 36 endpoints (Session 25 Phase 2)
- MCP yung-nagato extended API + diagnostic tool
- Snapshot baselines (`tests/fixtures/api_endpoints_baseline.json` 253 / `commands_baseline.json` 151)
- RouterContext + _helpers infrastructure для дальнейших extractions

## Session 26 priorities

### P0 (operational watch)
1. **Sentry observation 24-72h** — markers extension работает. Watch на новые типы spam.
2. **db_lock_monitor 24h baseline** — first scan показал count=0. Watch run.log следующих суток.
3. **Verify Yung Nagato peer handling** — попроси его написать user'у (с username и без). Должен использовать get_dialogs / forward request, НЕ "я бот".

### P1 (continued extraction)
4. **Phase 2 Wave H+I** — promote `_policy_matrix_snapshot`, `_capability_registry_snapshot`, `_channel_capabilities_snapshot` в `_helpers.py` (Wave H), затем extract `/api/capabilities/registry`, `/api/channels/capabilities`, `/api/policy/matrix` (Wave I). 3 более endpoints.
5. **Phase 2 Wave J** — POST endpoints через `ctx.assert_write_access()`. Кандидаты: `/api/inbox/update`, `/api/notify/toggle`, `/api/silence/toggle`, `/api/openclaw/cron/jobs/{create,toggle,remove}`. Может ~10 endpoints.
6. **Phase 2 Wave K-L** — translator endpoints (~25), voice endpoints (~5), openclaw endpoints (~30) — самые большие domains.

Target Phase 2 finish: ~150-200 endpoints в routers (60-80% web_app.py extraction).

### P2 (architectural)
7. **command_handlers.py split** — параллельный track. 175+ commands в commands/{ai,memory,swarm,...}_commands.py. См. `docs/CODE_SPLITS_PLAN.md`.
8. **HNSW migration** — НЕ trigger'нулось (vec count 72k, p95 ~25ms). Только monitoring.

### P3 (backlog)
9. **busy_timeout=0 в db_lock_monitor.sh probe** — мини-косметика (Session 24 finding).
10. **9 raw print() в src/** → CLOSED (Session 24 sub-agent verify).

## Phase 2 Lessons learned (важно для Wave H+)

1. **Factory pattern** `build_X_router(ctx: RouterContext) -> APIRouter` — clean DI, легко тестируется.
2. **Test pattern**: `_build_ctx()` helper в test, mocked deps, без полного WebApp.
3. **Dual-patch** для existing tests которые mock'ают `src.modules.web_app.X` — добавлять path `src.modules.web_routers.X_router.X` параллельно. См. `c4aab65` lesson.
4. **RouterContext incremental extension**: добавлять field только когда нужно. `boot_ts_holder: list[float]` (Wave F) — shared mutable holder через list-ref, простой workaround для cross-cutting state.
5. **Helper promotion** перед extraction: если endpoint зависит от `self._helper()` который в свою очередь от `self.deps` — сначала promote helper в `_helpers.py` как module-level function (signature принимает deps + args), затем extract endpoint.
6. **Snapshot --diff = 0 после каждого commit** — гарантия что extraction не added/удалил endpoints. Если diff != 0 — НЕ commit, расследовать.

## Operational tools

- `scripts/krab_session_diagnostic.sh` — Telegram MCP session is_bot detection (read-only)
- `scripts/snapshot_endpoints_commands.py --diff` — regression detection для extractions
- `scripts/db_lock_monitor.sh` — DB lock event scanner (60min window, threshold 5)
- `tests/fixtures/api_endpoints_baseline.json` + `commands_baseline.json` — golden fixtures Phase 2

## Restart notes

- Krab: `/Users/pablito/Antigravity_AGENTS/new\ Stop\ Krab.command` → wait 10s → `new\ start_krab.command`
- OpenClaw: `openclaw gateway` (NOT SIGHUP)
- MCP: `launchctl kickstart -k gui/$(id -u)/com.krab.mcp-yung-nagato` (after server.py / telegram_bridge.py changes)
- Memory check: `venv/bin/python3 scripts/memory_doctor.py` (5/5 expected ✅)
- Sentry poll: `tail -10 /tmp/krab_sentry_poll/poll.log`
- DB lock monitor: `tail -10 /tmp/krab_db_lock_monitor/run.log`

## First commands for Session 26

```bash
cd /Users/pablito/Antigravity_AGENTS/Краб
cat .remember/next_session.md          # this file
git log --oneline -20                  # recent commits Session 25
git status                             # any drift
venv/bin/python3 scripts/memory_doctor.py
venv/bin/python3 scripts/snapshot_endpoints_commands.py --diff
launchctl list | grep -i krab          # 10 active expected
tail -5 /tmp/krab_sentry_poll/poll.log
tail -5 /tmp/krab_db_lock_monitor/run.log
ls src/modules/web_routers/*.py | wc -l  # 12 expected (10 routers + __init__ + _context + _helpers)
```

## Что новое в Phase 2 для Claude в Session 26

- **RouterContext** в `src/modules/web_routers/_context.py` — DI контейнер. Methods: `get_dep(name)`, `assert_write_access(header, token)`, `public_base_url()`, `get_boot_ts()`. Extension: добавляй field когда нужно.
- **_helpers.py** в `src/modules/web_routers/_helpers.py` — promoted module-level functions без self deps. `get_web_api_key()`, `get_public_base_url()`, `assert_write_access()`. Wave H+ добавит больше.
- **WebApp._make_router_context()** — factory создающий fresh RouterContext per router. Каждый extracted router получает свой ctx.
- **Pattern для extracted endpoints**: `def build_X_router(ctx: RouterContext) -> APIRouter` returns router. WebApp вызывает `self.app.include_router(build_X_router(self._make_router_context()))`.
