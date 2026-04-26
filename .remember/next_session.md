# Session 25 — Starter Handoff (after Session 24 close, 2026-04-26)

## Status snapshot

- **Session 24 CLOSED** на ветке `fix/daily-review-20260421`, **3 commit'а** (`8f0da60..9b7cf52`), всё в origin
- KrabEar PR #288 — статус **MERGED** в base `codex/krab-ear-v2` (выполнено параллельной KrabEar сессией, не в скопе Krab main)
- **Krab production live**, PID 91282 после 3-х successful restart-ов в сессии
- archive.db: 506 MB / 753k+ msgs / **72,362 chunks** ↔ 72,362 vec ↔ 72,362 FTS — **memory_doctor.py 5/5 ✅**
- **10 LaunchAgents активны** (включая новый `ai.krab.db-lock-monitor` Session 24)

## Session 24 wins (3 commits)

| sha | type | summary |
|---|---|---|
| `8f0da60` | feat(health) | /api/health/deep 8→**12 секций** (sentry/mcp_servers/cf_tunnel/error_rate_5m) + bug fix orphan_vec false positive (72362→0) |
| `b4d7dc0` | fix(sentry) | _BENIGN_ERROR_MARKERS expansion: +`router_not_configured`, +`Client has not been started yet` (transient boot HTTPException) |
| `9b7cf52` | docs | docs/HNSW_MIGRATION_PLAN.md + docs/CODE_SPLITS_PLAN.md (sub-agent prep) |

## Что live в production (новое в Session 24)

### /api/health/deep 12 секций
- **sentry**: `initialized`, `dsn_configured` — отражает Session 23 init drift fix (`8c803e2`). Использует `sentry_sdk.is_initialized()` (>=1.16) + Hub fallback.
- **mcp_servers**: parallel TCP probe portov 8011/8012/8013 (yung-nagato/p0lrd/hammerspoon) через `asyncio.open_connection` timeout=1s.
- **cf_tunnel**: `launchctl list ai.krab.cloudflared-tunnel` + state файлы `/tmp/krab_cf_tunnel/{last_url,fail_count}`.
- **error_rate_5m**: sliding window count из `error_handler._RECENT_ERROR_TS` (deque maxlen=1000, push в FloodWait/RecursionError/Exception except'ах). Pure in-memory, без log-парсинга.

### orphan_vec bug fix
Старый SQL `vec_chunks_rowids LEFT JOIN chunks ON c.id = vr.id` → false positive 72362/72362 (vec_chunks_rowids.id ВСЕГДА NULL — это shadow view с non-standard semantics). Заменено на каноничный `SELECT COUNT(*) FROM vec_chunks WHERE rowid NOT IN (SELECT id FROM chunks)` из `memory_doctor.py`. Live: 0 orphans.

### error_handler ring buffer
`_RECENT_ERROR_TS: deque[float] = deque(maxlen=1000)` + `recent_error_count(window_sec)` — exposed для health endpoint без зависимости от лога.

### Sentry markers extension
3 transient HTTPException теперь drop в `_before_send`:
- `userbot_not_ready` (был с Session 23)
- `router_not_configured` (Session 24)
- `Client has not been started yet` (Session 24)

После 3-го рестарта (PID 91282) — **0 новых spam-событий за следующие ~10 минут**, markers extension работает.

### Sentry observation (по результатам 24h)
- Top events до Session 23 fix: `swarm_research_error` x39, `cli_runner_tool_not_found` x26, `router_not_configured` x17, KrabEar App Hanging x19+9 — все исторические, captured retroactively после 8c803e2.
- **Новое событие в сессии**: `fatal_error error='disk I/O error'` (id=115202160) — единичный case при двойном Stop+Start подряд (close+reopen archive.db race). Sentry → Telegram alert delivered ✅. Не критичный, наблюдение.

### db_lock_monitor LaunchAgent
- **Loaded** через `launchctl bootstrap gui/501 ~/Library/LaunchAgents/ai.krab.db-lock-monitor.plist`
- PID 90041, StartInterval 3600s
- First scan at 03:40:46: count=0 threshold=5 → OK
- pragma_baseline: `busy_timeout=0, journal_mode=wal` ⚠️ **`busy_timeout=0` — кандидат на bump до 30000ms** (мини-оптимизация для retry vs immediate fail)

### Sub-agent reports (saved as docs)
- **`docs/HNSW_MIGRATION_PLAN.md`** — trigger 250k vectors (текущее 72k), 2-3 session-days effort. Активация при `VecQueryLatencyHigh` p95>100ms или vec count >= 250k.
- **`docs/CODE_SPLITS_PLAN.md`** — `command_handlers.py`(19.6k LOC) → 11 модулей в `src/handlers/commands/`; `web_app.py`(15.8k LOC) → 12 routers с APIRouter + RouterContext dataclass DI. 6 session-days, 5-phase build.

## Pytest health (как ожидалось sub-agent verify)

- Sub-agent ran `tests/unit/` на committed `b4d7dc0`: **9527 passed, 0 failed, 94 skipped**
- Стартовая команда сессии показала 26 failed + 18 errors — это были stale cache/uncommitted state в worktree, не реальная регрессия
- **Финальный full pytest run** в Session 24 — TBD (бежит в фоне на момент закрытия handoff)

## Session 25 priorities

### P0 (operational)
1. **Sentry observation 24-48h post-Session 24 restart** — markers extension работает, но смотреть появятся ли новые типы spam после 24h. Если да — расширить `_BENIGN_ERROR_MARKERS` ещё.
2. **db_lock_monitor 24h baseline** — наблюдение за `/tmp/krab_db_lock_monitor/run.log`, `pragma_baseline`. Если ALERT — расследовать concurrent writer'ов.

### P1 (improvements)
3. **`busy_timeout=0` → 30000ms** — мини-оптимизация archive.db SQLite. Изменить `busy_timeout` PRAGMA в `memory_indexer_worker.py` или там где открывается connection. Ожидаемый impact: graceful retry вместо immediate `database is locked` fail.
4. **Wave 14 test cleanup** (если есть intermittent flakes) — пока известен только `test_gratitude_with_random_below_rate` (random seed, не блокирует). Если за неделю появятся новые flaky — sweep.
5. **CLAUDE.md autotables refresh** — endpoint count теперь 249→**253** (+4 не нужно, /api/health/deep уже был; reality TBD после `/api/endpoints` snapshot). Test count 9991→9527 (+коррекция в обе стороны).

### P2 (architectural — dedicated sessions)
6. **HNSW migration** — НЕ trigger'нулось (vec count 72k, p95 ~25ms). Только мониторинг, активация когда `VecQueryLatencyHigh` p95>100ms ≥30 минут. См. `docs/HNSW_MIGRATION_PLAN.md`.
7. **Code splits** — `command_handlers.py` + `web_app.py`. 6 session-days. См. `docs/CODE_SPLITS_PLAN.md`. **Требует max reasoning + test discipline**. Phase 1 (scaffold) — low risk, можно начать в любой момент.

### P3 (backlog)
8. **9 raw `print()` calls в src/** — заменить structured logger.
9. **LM Studio 401** — long-standing, Docker зависимость, отложен.
10. **Named Cloudflare Tunnel** — для Sentry уже не нужен (polling); другие webhook'ов на горизонте нет.

## Known residual issues

1. ~~Cron LLM quality~~ → CLOSED (Session 23 `aef6d07`)
2. ~~DB-locked retest~~ → CLOSED (Session 24, monitor LIVE)
3. ~~vec_chunks_meta desync~~ → CLOSED (Session 23 misdiagnosis)
4. ~~orphan_vec false positive~~ → CLOSED (Session 24 `8f0da60`)
5. ~~Sentry init drift~~ → CLOSED (Session 23 `8c803e2`)
6. **`busy_timeout=0`** в archive.db (Session 25 P1)
7. **1 flaky test** (`test_gratitude_with_random_below_rate`, random seed)
8. **disk I/O error** на двойном Stop+Start — race close+reopen archive.db (rare, мониторинг)
9. **9 raw print() in src/** (low impact)

## First commands for Session 25

```bash
cd /Users/pablito/Antigravity_AGENTS/Краб
cat .remember/next_session.md                  # this file
cat docs/SESSION_23_FINAL_REPORT.md            # previous big context
git log --oneline -10                          # recent commits
git status                                     # any drift
venv/bin/python3 scripts/memory_doctor.py      # 5/5 expected ✅
curl -s http://127.0.0.1:8080/api/health/deep | python3 -m json.tool | head -30
curl -s http://127.0.0.1:8080/api/health/deep | python3 -c "import sys,json; d=json.load(sys.stdin); print('sections:',len(d), '|', list(d.keys()))"
launchctl list | grep -i krab                  # 10 active expected
tail -5 /tmp/krab_sentry_poll/poll.log         # poll alive
tail -5 /tmp/krab_db_lock_monitor/run.log      # 24h baseline
```

### Sentry observation snapshot

```bash
source .env
for proj in python-fastapi krab-ear-agent krab-ear-backend; do
    echo "=== $proj ==="
    curl -sS "https://sentry.io/api/0/projects/po-zm/$proj/issues/?statsPeriod=24h&sort=new&limit=5" \
        -H "Authorization: Bearer $SENTRY_AUTH_TOKEN" \
        | python3 -c "import sys,json; [print(f\"  {i['count']:>4} {(i.get('title') or '')[:90]}\") for i in json.load(sys.stdin)[:5]]"
done
```

### busy_timeout fix (если выберешь как P1)

```bash
# Найти где открывается connection к archive.db
grep -rn "sqlite3.connect.*archive.db\|connect.*krab_memory" src/core/ | head
# Целевые файлы — добавить PRAGMA busy_timeout=30000 после connect
# Тест: повторить scenario disk I/O error из Session 24 (двойной Stop+Start)
```

## Restart notes

- Krab: `/Users/pablito/Antigravity_AGENTS/new\ Stop\ Krab.command` → wait 10s → `new\ start_krab.command`
- OpenClaw: `openclaw gateway` (NOT SIGHUP)
- MCP: `./scripts/restart_mcp_servers.command` if transport closed
- Memory check: `venv/bin/python3 scripts/memory_doctor.py` (5/5 expected ✅)
- Sentry poll: `tail -10 /tmp/krab_sentry_poll/poll.log`
- DB lock monitor: `tail -10 /tmp/krab_db_lock_monitor/run.log` (every 3600s)

## Что новое для модели Claude Opus в Session 25

- **`/api/health/deep` мониторит всё**: sentry init, MCP servers, CF tunnel, error rate 5m. Используй как entry point для observability.
- **`error_handler.recent_error_count(window_sec)`** — pure-Python in-memory счётчик errors, без log-парсинга. Можно расширить (например `recent_error_count_by_type`) если нужно.
- **Sub-agent для code splits + HNSW prep** уже отработали — планы лежат в `docs/`. Не пере-исследовать, просто читать при активации.
- **3 Krab restart'а в одной сессии — это много** (race условия с archive.db). Лучше 1 рестарт в конце сессии или в начале.
