# Session 28 — Starter Handoff (after Session 27 close, 2026-04-27)

## Status snapshot

- Branch: `fix/daily-review-20260421` — **98+ commits** (Sessions 24-27 непрерывно)
- **Krab production live** — восстановлен через 5-layer recovery 27.04.2026
- Smart Routing **active** — 5-stage pipeline, observation period НАЧАЛСЯ (первый раз без gateway 500)
- Phase 2 command_handlers split **COMPLETE** — 11041 LOC (было 19637, −43.8%), 10 модулей extracted
- archive.db: ~506 MB / 753k+ msgs — memory_doctor.py 5/5 OK
- 10 LaunchAgents активны

## Session 27 wins (~18 commits)

### Phase 2 command_handlers split (Waves 1-10)
| Commit | Wave | Модуль |
|---|---|---|
| `9d006be` | Wave 1 | text_utils (calc/b64/hash/json/sed/diff/regex/len/rand) |
| `5fef756` | Wave 2 | chat_commands (grep/history/whois/monitor/chatinfo/...) |
| `9d822ed` | Wave 3 | scheduler_commands (timer/stopwatch/remind/schedule/todo/cron) |
| `41ca90a` | Wave 4 | voice_commands (voice/tts/audio_message) |
| `f53f134` | Wave 5 | memory_commands (memo/bookmark/remember/recall/note/...) |
| `7968b8e` | Wave 6 | social_commands (pin/del/afk/poll/welcome/...) |
| `fcbda3b` | Wave 7 | ai_commands (ask/search/agent/rate/explain/fix/rewrite/summary) |
| `436b640` | Wave 8 | swarm_commands (handle_swarm + _AgentRoomRouterAdapter) |
| `326d0ac` | Wave 9 | translator_commands (handle_translator/translate/translate_auto) |
| `8945a5f` | Wave 10 | system_commands (health/diagnose/restart/panel/version/uptime/sysinfo) |

### Phase 2 web_app final waves (carried from Session 26 scope)
| Commit | Что |
|---|---|
| `ac833bc` | context_router (3 endpoints /api/context/*) |
| `10c3d67` | monitoring_router (/api/ops/{diagnostics,runtime_snapshot,bundle,...}) |
| `61178de` | assistant_router /api/assistant/stream |
| `4f237c6` | assistant_router /api/assistant/query (last HARD endpoint) |

### Repairs and ops
| Commit | Что |
|---|---|
| `fbf3262` | fix: dual-namespace lookup — repair Phase 2 split test regressions |
| `68111fc` | fix: purge stale model defaults (nvidia/nemotron-3-nano + nvidia/llama-3.1-nemotron-nano) |
| `0c7f89d` | feat: analyze_smart_routing.py log analyzer (Session 27 sub-agent) |

### 5-layer LLM recovery (27.04 operational, no separate commit)
1. `RESTORE_PREFERRED_ON_IDLE_UNLOAD=0` + `LOCAL_AUTOLOAD_FALLBACK_LIMIT=0` в `.env`
2. `codex login` (interactive — OAuth refresh token race)
3. `openclaw.json` + `agent.json` — harness renamed `codex-cli` → `codex`
4. `npm i -g @openai/codex@latest` (0.115 → 0.125)
5. `68111fc` — code-level stale defaults purge

## Что live в production (новое в Session 27)

### 1. Phase 2 command_handlers split (Waves 1-10)
11 модулей в `src/handlers/commands/` (включая pre-existing `policy_commands` + `_shared`):
`text_utils`, `chat_commands`, `scheduler_commands`, `voice_commands`, `memory_commands`,
`social_commands`, `ai_commands`, `swarm_commands`, `translator_commands`, `system_commands`.

Pattern в command_handlers.py: `from .commands.X import handler  # Phase 2 Wave N` re-exports.
Dual-namespace lookup (`_ch.symbol` + `_X_BASELINE` + `_resolve`) — необходим для test monkeypatch compatibility.

**Метрика:** 19637 → 11041 LOC (−43.8%). dispatcher + register/router logic остаётся в command_handlers.py.

### 2. Smart Routing observation period (активен 27.04)
Предыдущая попытка observation в Session 26-27 была blocked gateway 500.
После 5-layer recovery логи `smart_trigger_decision` реально пишутся.
Инструмент анализа: `python scripts/analyze_smart_routing.py --hours 24`.

### 3. Stale defaults purged (`68111fc`)
Удалены hard-coded defaults: `nvidia/nemotron-3-nano` (3 файла) — больше не trigger'ят LM Studio auto-load.

### 4. Phase 2 web_app роутеры (финальные 4 волны)
`src/modules/web_routers/` теперь содержит 26 роутеров — все endpoints extracted.

## Что новое для Claude в Session 28

- **Dual-namespace lookup pattern** — при extraction функций из command_handlers.py в submodule тесты могут патчить старые пути. Pattern: в command_handlers.py оставить `_BASELINE = original_fn; _resolve = lambda: submod.fn if submod else _BASELINE`. Документировать в CLAUDE.md.
- **Smart Routing observation period НАЧАЛСЯ** 27.04 — первый с живыми логами. analyze_smart_routing.py готов.
- **5-layer LLM recovery documented** — см. operational lessons ниже.
- **command_handlers.py split DONE** — дальнейшее дробление diminishing returns (dispatcher/router/register остаётся).

## Session 28 priorities

### P0 (operational — первые 24-72h)
1. **Smart Routing logs review** — `python scripts/analyze_smart_routing.py --hours 24`. Анализ false positive/negative, tune `KRAB_IMPLICIT_TRIGGER_THRESHOLD` или per-chat policy.
2. **Sentry observation** post 5-layer recovery — markers expansion + verify DB guard не даёт false positives.
3. **7 pytest hangers** — pre-existing network/asyncio в test env. Установить `pytest-timeout`, найти offending fixtures (`async_generator`, `httpx`, `anyio`).

### P1 (fixes / debt)
4. **3 pre-existing test fails** — `auth_recovery_readiness`, `policy_matrix`, `inbox_status` — domain bugs, не критичные. Один из них может быть unrelated к Session 27.
5. **Smart Routing tuning** — based на real observation data (false positive analysis, per-chat policy recommendations).
6. **OAuth refresh token proactive check** — если `~/.codex/auth.json` mtime > 24h → preventive `codex login` при startup. Пока manual.

### P2 (architectural)
7. **HNSW migration** — vec count ~72k / 250k trigger. Только мониторинг, не action.
8. **command_handlers.py final cleanup** — afk/welcome/group_admin, !browser/macos/hs, !cli остаются inline. Diminishing returns — низкий приоритет.

### P3 (backlog)
9. **Auto-load verify post next Mac reboot** — Option C fix в Stop Krab.command. Verify: `launchctl list | grep ai.krab.core` после cold reboot.
10. **Restart-loop alert** — Telegram/Sentry notification при cold_start_rate_limit triggered.

## Operational lessons (Session 27)

1. **Re-export ≠ same identity для monkeypatch** — `from .commands.X import fn` создаёт новый binding в command_handlers namespace. Monkeypatch на `command_handlers.fn` не достигает submodule. Fix: dual-namespace lookup pattern (`fbf3262`).

2. **Stale defaults опаснее silent** — `nvidia/nemotron-3-nano` default не trigger'ил годами, потом среагировал на reboot и autoload LM Studio. Code-level cleanup необходим немедленно после discovery.

3. **OAuth refresh tokens single-use** — Mac reboot mid-refresh → token race → "already used" deadlock. Safe path: check `~/.codex/auth.json` mtime, если stale > 24h → preventive `codex login`.

4. **Harness names имеют version-drift** — `codex-cli` → `codex` в OpenClaw 2026.4.24. Primary source of truth: `grep -r '"harness"' ~/.openclaw/` + `node_modules/.bin/` list.

5. **Sub-agent dispatch требует explicit scope** — git races при параллельной работе над одним файлом. Разделять по файловой области заранее.

6. **`disk I/O error` НЕ corruption** (из Session 26, подтверждено) — transient OS-level WAL contention. Excluded из HARD markers в DB guard.

## Operational commands

```bash
cd /Users/pablito/Antigravity_AGENTS/Краб
cat .remember/next_session.md                   # this file
git log --oneline -25                           # recent commits
launchctl list | grep -i krab                   # 10 active expected

# Smart Routing observation
python scripts/analyze_smart_routing.py --hours 24
grep smart_trigger_decision ~/.openclaw/krab_runtime_state/krab_main.log | tail -20
grep "feedback_negative\|feedback_positive\|chat_response_policy_auto" ~/.openclaw/krab_runtime_state/krab_main.log | tail -10

# Health check
curl -s http://127.0.0.1:8080/api/health/lite | python3 -m json.tool

# Per-chat policies
curl -s http://127.0.0.1:8080/api/chat/policies | python3 -m json.tool

# DB guard logs
grep "db_corruption_detected\|db_corruption_quarantined" ~/.openclaw/krab_runtime_state/krab_main.log | tail -5

# Restart guard log
tail -10 ~/.openclaw/krab_runtime_state/krab_cold_starts.log

# LLM recovery check (если codex опять не отвечает)
cat ~/.codex/auth.json | python3 -m json.tool   # check expiry
codex login                                     # если stale

# Session diagnostic
bash scripts/krab_session_diagnostic.sh
```

## Restart notes

- Krab manual: `/Users/pablito/Antigravity_AGENTS/new\ start_krab.command`
- Stop manual: `/Users/pablito/Antigravity_AGENTS/new\ Stop\ Krab.command`
- After Mac reboot: launchd должен auto-load (Option C fix). Verify: `launchctl list | grep ai.krab.core`. Если НЕ loaded — manual start.
- MCP: `launchctl kickstart -k gui/$(id -u)/com.krab.mcp-yung-nagato` (после server.py changes)
- DB recovery: `sqlite3 corrupt.session ".recover"` → новая чистая SQLite
- **Codex stuck**: `codex login` → verify `~/.codex/auth.json` updated, restart Krab

## Sentry post-Session 27

- Issues из Session 26 (PYTHON-FASTAPI-5Z + 5W) — resolved.
- Post 5-layer recovery: наблюдать 24-48h, расширять `_BENIGN_ERROR_MARKERS` если новые false positives.

## Files for Session 28 reference

- `docs/SMART_ROUTING_DESIGN.md` — Smart Routing architectural spec
- `scripts/analyze_smart_routing.py` — log-based decision analyzer (NEW Session 27)
- `tests/unit/test_analyze_smart_routing.py` — tests для analyzer (NEW Session 27)
- `docs/CODE_SPLITS_PLAN.md` — Phase 2 plan (command_handlers done, web_app done)
- `docs/HNSW_MIGRATION_PLAN.md` — HNSW prep (not triggered yet, ~72k/250k)
- `.env` — `RESTORE_PREFERRED_ON_IDLE_UNLOAD=0` + `LOCAL_AUTOLOAD_FALLBACK_LIMIT=0` defenses live
- `~/.openclaw/openclaw.json` — harness `codex` (not `codex-cli`) post-fix

## Test counts

- Session 26 baseline: ~10300+ collected
- Session 27: **6198 passed** (unit run) + 3 pre-existing fails + 7 hangers excluded (network/asyncio infra)
- New tests added: ai_commands (~23), swarm (~11), translator, system_commands (~371), smart_routing analyzer (TBD from sub-agent commit)
- Delta: ~500+ новых тестов в session 27
