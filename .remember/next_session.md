# Session 35 — Starter Handoff (after Session 34 close, 2026-05-03 ~00:30)

## TL;DR

- **main HEAD**: `9cd6f5f` — Session 34 добавил **8 commits** над `a6ed8cc`
- **Krab live**: status=up, telegram_session=ready, integrity=ok, новая fallback chain активна
- **Все P0/P1 Session 34 закрыты** (включая P1.4 Step 4 A/B framework)
- **3 параллельных worktree-agent (Sonnet)** → 4 ветки merge clean без конфликтов
- **Fallback chain переделан**: Gemini 3.1 Pro теперь #1 (был openai/gpt-5.5 — нет баланса)

## Что сделано в Session 34

### Production stability ✅
- 14h post-Wave-14 verified: `krab_session_corruption_total=0`, integrity ok
- Live-confirm: Wave 14-K codex-cli first-chunk hang 45s + silent fallback **видим в Telegram** ("⏱️ Переключаюсь на резервную модель…")

### Wave 16-A: SkillCurator Step 3 (apply_with_approval)
- `active_overlays` + `last_apply_at` в `CuratorState` (atomic, backward compat)
- `apply_with_approval(proposal_id, *, force=False, idle_check=True)`:
  - per-team `asyncio.Lock` mutex
  - idle-check через swarm_channels
  - weekly rate-limit (7 days) per team
  - snapshot текущего effective prompt в `~/.openclaw/krab_runtime_state/curator/prompts_archive/{team}/v{N}_{ts}.md`
  - atomic overlay persist в state.json
- `rollback(team, *, version=-1)` к baseline или конкретной версии
- `get_team_system_prompt()` теперь overlay-aware с **30s TTL cache** + invalidate-on-apply
- Команды: `!curator apply <proposal_id> [--force] / rollback <team> [--version N] / overlays`
- **+16 тестов**, 51 passed total в `test_skill_curator_*.py`

### Wave 16-B: Hermes Phase B (ACP bridge foundation)
- `agent-client-protocol==0.9.0` pinned в `requirements.txt`
- `src/core/agent_engine.py` — `AgentEngineClient` Protocol + `StreamChunk`/`EngineHealth` dataclasses
- `src/integrations/hermes_acp_bridge.py` — `HermesACPBridge`:
  - lazy subprocess spawn (`hermes acp` stdio)
  - 60s health caching
  - graceful degrade когда binary не найден (тихо `is_healthy=False`)
  - singleton `get_hermes_bridge()`
- `src/core/agent_engine_router.py` — `resolve_engine()`:
  - priority: chat override → room policy → env default
  - persist в `agent_engine_overrides.json` / `swarm_engine.json`
- Команда `!engine` (owner-only): show / here / room / status, `engine` зарегистрирован в `command_registry.py`
- **+25 тестов**: 15 router + 10 bridge
- ⚠️ **БЕЗ интеграции в `llm_flow.py`** — defer Phase C

### Wave 16-C: 3 starlette test unskips (Wave 15-A backlog)
- Все 3 файла были **false-positive скипы** (Session 33 wrongly diagnosed как starlette hangs)
- Реальность: `test_photo_dm_owner.py` и `test_reply_media_extraction.py` вообще не используют starlette
- `test_web_acl_api.py`: dual-namespace patches работают корректно
- **24 теста pass** (12+5+7) за 2.2с

### Wave 16-D: SkillCurator Step 4 (A/B framework)
- `start_ab_test(team, candidate_proposal_id, *, n_rounds=10)` — file-backed JSON в `curator/ab_tests/`
- `select_variant(ab_id, round_id) -> "control"|"candidate"` (round-robin deterministic via hash)
- `record_round_metric(ab_id, round_id, metrics)` — atomic append
- `evaluate_ab_test(ab_id) -> dict` decision criteria per design §4:
  - candidate wins: `success_rate >= control + 0.05` AND `cost <= control * 1.10` AND `latency <= control * 1.10`
- `evaluate_ab_test_and_apply(ab_id) -> tuple[dict, bool]` async auto-apply pattern
- `cancel_ab_test(ab_id, *, reason)`, `list_ab_tests(team, status)`, `get_ab_test(ab_id)`
- per-team A/B mutex + `state.active_ab_tests: dict[team, ab_id]`
- Команды: `!curator ab start/status/evaluate/cancel/list`
- **+15 тестов**, 66 passed total в `test_skill_curator_*.py`
- ⚠️ **НЕ интегрировано в `swarm.py`** — wire-up Phase D

### Fallback chain rebuild (live config)
В `~/.openclaw/openclaw.json` (backup в `~/.openclaw/openclaw.json.bak-<ts>`):
```
primary:    codex-cli/gpt-5.5         (оставлен)
fallbacks:  google/gemini-3.1-pro-preview         ← #1 (был openai/gpt-5.5 — мёртвый ключ)
            google-gemini-cli/gemini-3.1-pro-preview
            google/gemini-3-pro-preview
            anthropic/claude-opus-4-7
            google/gemini-2.5-pro-preview-06-05
```
`.env`: `KRAB_CODEX_CLI_FALLBACK_MODEL=google/gemini-3.1-pro-preview` (Wave 14-K override для codex first-chunk hang).

⚠️ **Google API $300 бонус активен ещё несколько дней** — после истечения пересмотреть приоритеты в цепочке.

## Session 34 final state

```
Branch: main (HEAD = 9cd6f5f)
Commits Session 34: 8 (4 feat + 4 merge --no-ff)
Krab process: running, telegram session ready
Pragmas live: synchronous=FULL, temp_store=MEMORY, 64MB cache, WAL+autocheckpoint
Corruption events (current process): 0
Test additions Session 34: +56 (16 apply + 25 hermes/router + 15 ab + unskip 24=fix)
  - test_skill_curator_*.py: 66 passed (3 файла)
  - test_agent_engine_router.py: 15 passed
  - test_hermes_acp_bridge.py: 10 passed
  - test_web_acl_api/photo_dm_owner/reply_media_extraction: 24 passed (unskipped)
Worktrees: cleaned (4 agent-* removed)
agent-client-protocol: 0.9.0 в shared venv
```

## Backlog для Session 35+

### High priority

1. **Bug: media transcription для audio files** (НЕ voice messages):
   - User прислал audio file (`kubael — Лестат`, 02:15, 2.9MB) → Краб всё-таки получил его в reply context (после repeat), но **транскрипция не сработала** — Краб ушёл по обычному chat-пути → codex-cli timeout
   - Voice messages (Telegram voice) транскрибируются ✅
   - Audio files (Telegram audio/document mime=audio/mpeg) — НЕТ
   - Investigate: media extraction в `src/userbot/llm_flow.py` + transcription pipeline
   - Симметрично с voice transcription pipeline

2. **24h Sentry observation post Session 34 restart**:
   - Verify corruption events trend
   - Track `pyrogram_sqlite_malformed_swallowed` count
   - Health probe success rate
   - Sentry transaction errors trend (особенно с новой fallback chain — Gemini ratelimits?)

3. **SkillCurator integration в swarm.py** (Phase D):
   - Wire `select_variant` в `swarm.py` round execution path
   - `record_round_metric` после каждого round (cost/latency/tool_calls/verifier_pass/user_reaction)
   - `evaluate_ab_test_and_apply` cron trigger (weekly)
   - Endpoints: `GET /api/curator/state`, `/api/curator/ab/<ab_id>`, `POST /api/curator/dry-run`

4. **Hermes Phase C — live wiring**:
   - Wrap `OpenClawClient` чтобы satisfy `AgentEngineClient` Protocol
   - Modify `llm_flow.py` чтобы выбирать engine через `agent_engine_router.resolve_engine()`
   - archive.db migration: `agent_engine_runs` table
   - Endpoints: `GET /api/agent-engine/comparison`, `/api/agent-engine/runs`
   - Prometheus: `krab_agent_engine_latency_seconds{engine}`, `runs_total`, `fallback_total`
   - Roll out analysts swarm room на Hermes (когда Hermes binary будет установлен)

### Medium priority

5. **Hermes binary install** (Phase C trigger):
   - User действие: `~/.hermes/` уже configured (Wave 15-D), launch script готов
   - После install bridge сам поднимется через `_ensure_started()`
6. **A/B framework UI**: dashboard endpoint + frontend (Phase D)
7. **Paperclip server start** (carryover Session 33): `nohup npx paperclipai run` + Krab integration через `src/integrations/paperclip_bridge.py`

### Low priority

8. **Hermes selective cherry-picks** (agentskills.io, Honcho memory plugin)
9. **OpenClaw → Hermes migration tool**
10. **IDE integration via ACP** (VS Code/Zed)

## Operational quick reference

```bash
cd /Users/pablito/Antigravity_AGENTS/Краб

# Krab control
"/Users/pablito/Antigravity_AGENTS/new Stop Krab.command"
"/Users/pablito/Antigravity_AGENTS/new start_krab.command"

# Health
curl -sS http://127.0.0.1:8080/api/health/lite | python3 -m json.tool
curl -sS http://127.0.0.1:8080/api/uptime

# Curator full suite (Steps 1-4):
# !curator dry-run [team]                 — read-only analyzer
# !curator propose <team>                 — LLM proposes prompt diff
# !curator proposals                      — list pending
# !curator show <id>                      — display diff
# !curator apply <id> [--force]           — apply approved proposal
# !curator rollback <team> [--version N]  — rollback to baseline or version
# !curator overlays                       — show active overlays
# !curator ab start <team> <id> [--rounds N]  — start A/B test
# !curator ab status [team]               — current A/B test
# !curator ab evaluate <ab_id> [--apply]  — decision + optional auto-apply
# !curator ab cancel <ab_id>
# !curator ab list [team]

# Engine (Hermes Phase B):
# !engine                                 — show resolution + health
# !engine here <openclaw|hermes|auto>     — per-chat override
# !engine room <name> <engine|clear>      — per-swarm-room policy
# !engine status                          — both engines health
```

## Critical operational notes

- **НИКОГДА** не использовать `launchctl kickstart -k` (causes session corruption)
- **30s settle** между Stop+Start (rapid cycle = disk I/O error)
- **Pre-commit hook** иногда auto-stage'ит соседние файлы — verify после dispatch
- **Multi-agent dispatch** работает плотно (5-7 sonnet OK), используй `isolation: "worktree"` чтобы избежать conflicts (Session 34 4 agents без overlap = 0 conflicts)
- **Reasoning depth**: medium для оркестрации/merge, high для архитектурных решений
- **OpenAI ключ мёртвый** — НЕ возвращать `openai/*` в fallback chain
- **Google API $300 bonus** активен ещё несколько дней — пересмотреть приоритеты после истечения
- **Wave 14-K fallback message visible to user** — UX-страховка от codex-cli hangs работает в проде
