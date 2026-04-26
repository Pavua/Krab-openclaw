# Session 26 — Starter Handoff (after Session 25 close, 2026-04-26)

## Status snapshot

- Branch: `fix/daily-review-20260421` (~70+ commits в Session 24+25)
- **Krab production**: после reboot launchd НЕ auto-loaded ai.krab.core; **manual restart нужен** через `new start_krab.command`. (Это известный паттерн — см. Session 3 "launchd KeepAlive только когда bootstrap'нут").
- archive.db: 506 MB / 753k+ msgs / **72,362 chunks** ↔ vec ↔ FTS — `memory_doctor.py 5/5 ✅`
- **10 LaunchAgents** (mcp/ear/cloudflared/etc) auto-loaded post-reboot; ai.krab.core — НЕ
- **Phase 2 Code Splits — DONE** (Waves A-XX, ~50 коммитов). 25 routers / 207 endpoints / **web_app.py: 15,822 → ~10k LOC (-37%)**

## Phase 2 финал

| Router | Endpoints | LOC ~ | Pattern |
|---|---|---|---|
| openclaw_router | 31 | ~1100 | factory + 30+ helpers via late-bound λ |
| translator_router | 28 | ~900 | factory + helper chain |
| pages_router (Wave XX) | 25 | ~235 | factory + webapp ref для template paths |
| model_router | 17 | ~700 | factory + thinking/depth/local |
| monitoring_router | 17 | ~500 | factory + ops/sla/timeline |
| system_router | 13 | ~700 | factory + runtime/handoff/session10 |
| misc_router | 11 | ~400 | factory + chat_windows/diag/notify |
| inbox_router | 10 | ~360 | factory + POST CRUD |
| browser_router | 9 | ~175 | factory |
| admin_router | 8 | ~220 | factory + idempotency cache |
| health_router | 8 | ~245 | factory + ecosystem |
| swarm_router | 8 | ~185 | factory |
| memory_router | 6 | ~250 | factory + search/heatmap |
| voice_router | 6 | ~180 | factory + voice_runtime |
| capabilities_router | 4 | ~140 | factory + helper injection |
| commands_router | 4 | ~75 | direct |
| runtime_status_router | 4 | ~130 | factory |
| assistant_router | 3 | ~130 | factory |
| extras_router | 3 | ~110 | factory + boot_ts_holder |
| runtime_inspect_router | 3 | ~95 | factory |
| write_router | 3 | ~110 | factory + assert_write_access |
| meta_router | 2 | ~65 | direct |
| policy_router | 2 | ~85 | factory |
| version_router | 1 | ~40 | direct |
| **TOTAL** | **207** | **~6800** | |

## Session 25 commits (~50 waves)

Полная цепочка: `db6d9fd → ec16d56` через **Wave PoC + A → XX**.

Ключевые коммиты:
- **Foundation** (Phase 1 + RouterContext infra): db6d9fd, d2ec04b, 9563720, fc120d7
- **Helper promotion**: f2a82ad (policy_matrix), Wave H' (later inline)
- **First wave**: db6d9fd (version_router PoC, direct pattern)
- **Factory pattern start**: ff2521f (Wave F extras_router — first build_X_router(ctx))
- **Last wave**: ec16d56 (Wave XX pages_router — HTML pages)

### Userbot capabilities (Session 25 P0 issue)

- `5c66495` — telegram_send_message extended (reply_to/quote/parse_mode/disable_preview) + telegram_session_info diagnostic
- `c82bd0f` — krab_session_diagnostic.sh (read-only is_bot detection)
- `3d5b60f` — peer_id_invalid graceful handling (auto-retry get_chat + structured error hint)
- `b6cafbe` — persona preventive guidance for peer_id_invalid

**Verified**: все 5 active sessions = USERBOT (`is_bot=0`). Yung Nagato галлюцинация про "bot mode" исправлена через persona update.

### Architectural patterns established

1. **Factory pattern** `build_X_router(ctx: RouterContext) -> APIRouter` — clean DI
2. **Helper injection через late-bound lambda** в `_make_router_context`:
   ```python
   "x_helper": lambda *a, **kw: self._method(*a, **kw)
   ```
   Это сохраняет совместимость с `monkeypatch.setattr(WebApp, "_method", ...)` в существующих тестах.
3. **Async/sync resolution** через `inspect.isawaitable(result)` (НЕ iscoroutinefunction — оно False для lambda).
4. **Dual-patch** для existing tests которые patch `src.modules.web_app.X` — добавлять path `src.modules.web_routers.X_router.X` (lesson c4aab65).
5. **WebApp ref through deps** для tests которые patch instance attrs (Wave XX webapp pattern).
6. **Snapshot --diff = 0** после каждого commit — гарантия zero-regression.

## Что осталось в web_app.py (~10k LOC)

**Stable infrastructure** (по design в WebApp):
- `WebApp.__init__` + lifespan + middleware
- `_make_router_context()` — factory создающий ctx per request
- `_setup_routes()` — orchestrator (теперь в основном `include_router(...)` calls)
- `_collect_runtime_lite_snapshot` (~600 LOC, deeply coupled, не extracted by design)
- Helper functions which router'ы вызывают через ctx
- Auth middleware, rate limiting, idempotency cache
- WebSocket handlers (если есть)

## Session 26 priorities

### P0 (operational)
1. **Krab post-reboot restart** — `new start_krab.command` (вручную) после Mac reboot. Если launchd не запустил — это manual ручной шаг для now.
2. **Sentry observation 24-72h** — markers extension работает. Watch для новых типов spam.
3. **db_lock_monitor 24h baseline** — watch run.log.

### P1 (improvements)
4. **Auto-load ai.krab.core post-reboot** — investigate почему launchd не auto-loaded после reboot. Возможно `RunAtLoad=false` в plist или другая causa.
5. **CLAUDE.md autotables refresh** — текущие цифры (Session 24 row было 9527/253 endpoints; теперь нужно Session 25 row с финальными цифрами).
6. **Test pollution sweep** — есть 6 inbox-related pre-existing failures в `test_web_app_runtime_endpoints.py` которые failure'ят на main (verified через git stash). Investigate root cause.

### P2 (что осталось — Phase 2 cleanup)
7. **Phase 2 cleanup pass** — удалить unused imports, мёртвый legacy comments в web_app.py, dead helper methods если есть.
8. **command_handlers.py split** — параллельный track. Phase 1 scaffold уже готов (`src/handlers/commands/_shared.py`). Phase 2 extraction для commands — отдельная сессия (175+ commands).

### P3 (architectural)
9. **HNSW migration** — vec count 72k / 250k trigger, p95 ~25ms. Только мониторинг.

## Phase 2 Lessons learned (for future architectural splits)

1. **Start with PoC** — extract один stateless endpoint первым (version_router) для validate pattern.
2. **Direct pattern сначала, factory(ctx) потом** — когда нужна DI.
3. **Helper injection через deps dict** для endpoints с complex self.method dependencies.
4. **Late-bound lambda** для backwards-compat с monkeypatching tests.
5. **Snapshot --diff** + dedicated baselines = zero-regression guarantee.
6. **Parallel sub-agents** работают если каждый touches **different files** (no race на web_app.py).
7. **ВАЖНО**: sub-agents должны явно делать `cd /Users/pablito/Antigravity_AGENTS/Краб` чтобы избежать wrong-worktree issues (Wave UU lesson).

## Operational commands

```bash
cd /Users/pablito/Antigravity_AGENTS/Краб
cat .remember/next_session.md          # this file
git log --oneline -20                  # recent commits
git status                             # any drift
launchctl list | grep -i krab          # 10 active expected
bash "/Users/pablito/Antigravity_AGENTS/new start_krab.command"  # manual start
curl -s http://127.0.0.1:8080/api/health/lite | python3 -m json.tool
venv/bin/python3 scripts/memory_doctor.py
venv/bin/python3 scripts/snapshot_endpoints_commands.py --diff   # 253 / 151 stable
bash scripts/krab_session_diagnostic.sh  # MCP session is_bot check
ls src/modules/web_routers/*.py | wc -l  # 25 expected (24 routers + __init__ + _context + _helpers)
```

## Ключевые файлы для Session 26

### Phase 2 infrastructure
- `src/modules/web_routers/_context.py` — RouterContext dataclass (с methods: get_dep, assert_write_access, public_base_url, policy_matrix_snapshot, get_boot_ts, collect_runtime_lite)
- `src/modules/web_routers/_helpers.py` — module-level functions (get_web_api_key, get_public_base_url, assert_write_access, collect_runtime_lite_via_provider, collect_policy_matrix_snapshot)
- `src/modules/web_app.py:_make_router_context()` — factory создающий ctx per WebApp instance, инжектирует ~30+ helpers через late-bound lambdas

### Tests
- `tests/unit/test_phase1_scaffold.py` — 33+ tests для RouterContext + helpers
- `tests/unit/test_*_router.py` — 24 файла, по 5-15 tests на router
- `tests/unit/test_snapshot_baselines.py` — regression detection
- `tests/fixtures/api_endpoints_baseline.json` (253) + `commands_baseline.json` (151)

### Userbot fixes
- `mcp-servers/telegram/server.py` + `telegram_bridge.py` — extended API
- `~/.openclaw/workspace-main-messaging/persona/yung_nagato.md` — upgraded persona
- `scripts/krab_session_diagnostic.sh` — diagnostic tool
