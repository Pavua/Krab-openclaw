# Session 37 — Starter Handoff (after Session 36 close, 2026-05-04)

## TL;DR

- **main HEAD**: `f6a6861` — **30+ commits** Session 36 over `ba02171`
- **Krab live**: running, integrity ok после 3 OOM reboot'ов MacBook (Krab Ear подозревается)
- **Wave 18-H последний**: GenerateContentConfig flat fields fix — google_direct bypass PRODUCTION-VERIFIED
- **Google direct bypass работает**: `google_direct_bypass_engaged` + `google_genai_direct_complete_done` в логах, HTTP query за 5.5s
- **~68 тестов добавлено** в Session 36 (Waves 16-P + 17-A/B/C + 18-A/B)

## Что сделано в Session 36 (Wave 16-P → 18-H, 30+ commits)

### Wave 16-P: code review LOW fixes
- **HermesACPBridge async singleton**: `get_hermes_bridge()` — asyncio.Lock + double-checked locking, thread-safe
- **SkillCurator evaluate+apply atomicity**: `evaluate_ab_test_and_apply` под одной team-lock — нет gap между evaluate+apply
- **openclaw_runtime_repair.py**: `--session-path` CLI arg + `KRAB_SESSION_PATH` env override (нет hardcoded path)
- +9 тестов

### Wave 17-A: test coverage gaps
- `_telegram_session_snapshot` async non-blocking integration test
- `/api/runtime/recover` HTTP 503 e2e test для exit 78
- Wave 16-I full integration test с dynamic `_active_tool_calls`
- +10 тестов

### Wave 17-B: Hermes Phase C live wiring
- `src/core/agent_engine_openclaw.py` — OpenClawAdapter (реализует AgentEngineClient Protocol)
- `src/core/agent_engine_resolver.py` — `get_engine_for_route()` (chat→room→env priority + health gate)
- archive.db migration: `agent_engine_runs` таблица
- `/api/agent-engine/comparison`, `/api/agent-engine/runs`, `/api/agent-engine/status` endpoints
- Prometheus: `krab_agent_engine_runs_total`, `_latency_seconds`, `_fallback_total`
- ENV gate `KRAB_AGENT_ENGINE_DISPATCH_ENABLED` (default OFF — zero risk в production)
- +18 тестов

### Wave 17-C: убран hardcoded How2AI fallback
- `src/config.py`: убран `or ['-1001587432709']` — How2AI больше НЕ автоматом в CHAT_PERMANENT_BAN_LIST
- Удалена переменная `CHAT_PERMANENT_BAN_LIST=disabled` из .env
- +3 теста против регрессии

### Wave 18-A: session backup retention policy
- `src/bootstrap/session_recovery.py`: `cleanup_old_backups()` — 7 категорий бэкапов, keep_recent=3, max_age_days=14
- `scripts/openclaw_runtime_repair.py`: Step 5 cleanup integration
- +9 тестов

### Wave 18-B: Google direct SDK bypass (КЛЮЧЕВАЯ ФИЧА)
- `src/integrations/google_genai_direct.py` — direct google.genai SDK call, минует OpenClaw WebSocket→openresponses
- `src/openclaw_client.py`: bypass wire-up в `send_message_stream`
- Новая dep: `google-genai>=1.62`
- ENV gate `KRAB_GOOGLE_DIRECT_BYPASS_ENABLED` (default ON)
- +19 тестов

### Wave 18-D/E/G/H: fix chain для bypass
- **18-D**: bypass проверяется КАЖДЫЙ attempt в for loop (был только initial)
- **18-E**: `_has_photo_bypass = bool(images)` — explicit local (silent NameError fix)
- **18-G**: relative import fix `from ..config` → `from .config` (silent ImportError fix)
- **18-H**: `GenerateContentConfig` flat fields (`max_output_tokens` плоско, не вложенный `generation_config`) — legacy SDK pattern исправлен

### Production verification (Session 36 close)
```
HTTP query → "Привет." за 5.5s через google_direct channel ✅
Logs: google_direct_bypass_engaged + google_genai_direct_complete_done
OpenClaw 2026.5.2 broken WebSocket→openresponses обходится
```

## Settings обновлены (.env)

```
CHAT_PERMANENT_BAN_LIST=disabled  → УДАЛЕНА (Wave 17-C)
MODEL=codex/gpt-5.5              → codex-cli/gpt-5.5
KRAB_GOOGLE_DIRECT_BYPASS_ENABLED=1  (implicit default ON)
```

## OpenClaw config (`~/.openclaw/openclaw.json`) — fallback chain

```
primary:    codex-cli/gpt-5.5
fallbacks:
  - google/gemini-3.1-pro-preview          ← #1, bypass engages здесь
  - google/gemini-3-pro-preview
  - google/gemini-flash-latest
  - google-gemini-cli/gemini-3.1-pro-preview  (OAuth — token EXPIRED ~2026-04-13, last resort)
```

## Session 36 final state

```
Branch: main (HEAD = f6a6861)
Commits Session 36: 30+ (ba02171 → f6a6861)
Krab process: running, integrity ok
Google direct bypass: LIVE (5.5s response verified)
Test additions Session 36: ~68 новых тестов
3× MacBook OOM reboots: Krab пережил, Krab Ear подозреваемый виновник
agent-client-protocol: 0.9.0 (Wave 16-B, Session 35)
```

## Backlog для Session 37

### P0 / Критические

1. **Memory leak / OOM investigation** — MacBook перегружался 3 раза за Session 36. Подозревается параллельная Krab Ear сессия. Нужно: psutil baseline для Krab процесса + Krab Ear процесса, memory growth graph, потенциальный leak в audio buffering или embedding pipeline. Action: `pip install psutil` + мониторинг RSS 30 мин.

2. **gemini-cli OAuth re-auth** — `google-gemini-cli/gemini-3.1-pro-preview` token expired ~2026-04-13. Fallback chain деградирован если codex+gemini-direct упадут. Action: `gemini auth login` в терминале.

3. **Wave 18-I (in flight)** — empty response retry с `thinking_budget=0`. Bypass иногда возвращает пустой text при thinking-heavy моделях. Fix: после пустого ответа — retry без thinking budget.

### Hermes / Agent Engine

4. **Hermes Phase D** — wire-up SkillCurator A/B → `swarm.py`. Foundation готова (Waves 16-A/D + 17-B). Нужно: `select_variant()` в `AgentRoom.run()`, `record_round_metric()` после каждого раунда, `evaluate_ab_test_and_apply` cron trigger.

5. **Hermes binary install** — `hermes` не в PATH, Phase C (Wave 17-B) создала adapter но активация требует `KRAB_AGENT_ENGINE_DISPATCH_ENABLED=1` + hermes binary. Пока безопасный stub.

6. **OpenClaw upstream issue report** — WebSocket→openresponses transport regression для Google в OpenClaw 2026.5.2 нужно репортить upstream (если есть канал). Bypass обходит симптом, но не фиксит root cause в OpenClaw.

### Test coverage

7. **Integration test full bypass flow** — mock google.genai SDK + проверить что NameError/ImportError paths не silent. Wave 18 fix chain (D/E/G/H) показал что 4 silent bugs прошли review.

8. **Agent engine dispatch integration test** — когда `KRAB_AGENT_ENGINE_DISPATCH_ENABLED=1`, проверить что `get_engine_for_route()` возвращает корректный engine и fallback работает.

### Carryover

9. **Paperclip server start** (carryover Session 33+) — до сих пор не сделано
10. **Hermes selective cherry-picks** (agentskills.io, Honcho memory plugin)

## Operational quick reference

```bash
cd /Users/pablito/Antigravity_AGENTS/Краб

# Krab control
"/Users/pablito/Antigravity_AGENTS/new Stop Krab.command"
"/Users/pablito/Antigravity_AGENTS/new start_krab.command"

# Health
curl -sS http://127.0.0.1:8080/api/health/lite | python3 -m json.tool
curl -sS http://127.0.0.1:18789/health  # gateway direct

# Google direct bypass verify
grep "google_direct_bypass_engaged\|google_genai_direct" ~/.openclaw/logs/krab.log | tail -5

# Manual recovery (если session corrupt)
venv/bin/python scripts/openclaw_runtime_repair.py --check-only
venv/bin/python scripts/openclaw_runtime_repair.py

# Agent engine (Phase C, default OFF)
# Активировать: KRAB_AGENT_ENGINE_DISPATCH_ENABLED=1 в .env
# Status: GET /api/agent-engine/status
# Сравнение: GET /api/agent-engine/comparison

# SkillCurator (Steps 1-4):
# !curator dry-run [team] / propose <team> / proposals / show <id>
# !curator apply <proposal_id> [--force] / rollback <team>
# !curator ab start <team> <id> [--rounds N] / status / evaluate / cancel / list

# Engine commands (Hermes Phase B):
# !engine show / here <openclaw|hermes|auto> / room <name> <engine|clear> / status
```

## Critical operational notes

- **НИКОГДА** не использовать `launchctl kickstart -k` (causes session corruption)
- **30s settle** между Stop+Start
- **Google direct bypass ON по умолчанию** — если нужно отключить: `KRAB_GOOGLE_DIRECT_BYPASS_ENABLED=0`
- **KRAB_AGENT_ENGINE_DISPATCH_ENABLED** — default OFF, безопасно включать на тест
- **OpenAI ключ мёртвый** — НЕ возвращать `openai/*` в fallback chain
- **gemini-cli OAuth expired** — последний резерв в chain неработоспособен, нужен re-auth
- **Krab Ear + Krab параллельно** — risk OOM на 36GB M4 Max, мониторить RSS
- **Wave 16-N auto-recovery** — session recovery автоматический при preflight corrupt detect
- **google-genai>=1.62 требуется** — добавлена в requirements (Wave 18-B)

## Session 36 stats

- **30+ commits** (ba02171 → f6a6861)
- **5 новых модулей**: `google_genai_direct.py`, `agent_engine_openclaw.py`, `agent_engine_resolver.py` + 2 test modules
- **~68 тестов добавлено** (Wave 16-P: 9, 17-A: 10, 17-B: 18, 17-C: 3, 18-A: 9, 18-B: 19)
- **3× MacBook OOM reboot** — Krab выжил все 3 раза, integrity ok
- **Production bypass verified** — 5.5s Google response без OpenClaw WebSocket path
- **0 critical bugs** в финальном code review
