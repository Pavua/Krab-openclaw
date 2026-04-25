# Session 23 — Starter Handoff (after Session 22 close, 2026-04-25)

## Status snapshot

- **Sessions 20+21+22 closed** on branch `fix/daily-review-20260421`
- **88 commits shipped** (`808a508..c35ae0c`), не запушено в origin (owner-driven push)
- **257 API endpoints**, 151+ команд, ~6826+ тестов
- Krab running (`ai.krab.core` launchd), OpenClaw Gateway live
- archive.db ~506 MB / 752k+ msgs / 72k+ vec_chunks

## Live in production (enabled by default)

- **Memory Phase 2: LIVE** — `KRAB_RAG_PHASE2_ENABLED=1` + `KRAB_RAG_PHASE2_SHADOW=1` + `KRAB_RAG_MMR_ENABLED=1` в `.env`
  - Hybrid retrieval (FTS5 + vec_chunks RRF + MMR diversity)
  - **Recall@5 +37.67 Δ verified** in production (план был +30%)
  - MMR latency 5-10ms (10× speedup через vec-cache)
  - Model2Vec pre-warmed на bootstrap (cold-start 1.8s → <100ms)
- **Cron pipeline FIXED end-to-end** — был silent no-op 3 сессии, теперь evening-recap фигачит за 6 секунд
  - sender bind на cron_native_scheduler ✓
  - numeric chat_id + 90s timeout ✓
  - `adapter.route_query()` (вместо несуществующего `.stream()`) ✓
  - Context-augmented prompts (sub-30s exec, без tool-chain) ✓
- **Sentry Performance Monitoring** — LLM + memory spans, `/api/hooks/sentry` webhook → Telegram
  - `userbot_not_ready` → 503+Retry-After (no spam during boot, -80 events expected)
- **MCP — 44 tools**: filesystem/git/system/http (SSRF-guarded)/time/db_query + Apple Notes/iMessage/Reminders/Calendar + dev-loop pack
- **9 LaunchAgents**: ai.krab.core, ai.openclaw.gateway, mcp-yung-nagato, mcp-p0lrd, mcp-hammerspoon, cloudflared-tunnel, cloudflared-sentry-sync, workspace-backup, log-rotation, inbox-watcher, gateway-watchdog
- **Grafana**: `http://localhost:3000/d/krab-main` (admin/krab_local), 18 panels verified
- **UI panel V4**: hardcoded model names removed, live active model отображается
- **Wake-up message**: 60min rate limit (no more Saved Messages spam)
- **message_batcher**: preserves messages buffered during LLM processing
- **Models**: GPT-5.5 / GPT-5.5-pro / Opus 4.7 / DeepSeek V4 family в `models.json`

## Wave 11 / 12 wins

- **Test cleanup**: 324 → 17 failures (-95%) — translator/swarm/web/memory/voice/userbot/vision pollution fixes
- **Ultrareview integration**: 4 real bugs fixed (message_batcher drops, heatmap bucket_hours, dashboard sync subprocess, restart endpoint rate limit)
- **88 commits production-hardening**: cron, Sentry, panel, MCP, ops, swarm, security guards

## Known residual issues (carry-over to Session 23)

1. **Cron LLM output quality** — короткие/обрезанные ответы; нужно поднять reasoning depth (low → medium)
2. **DB-locked retest** — WAL+busy_timeout pragma применён, нужна 24h production observation
3. **sqlite-vec `vec_chunks_meta` desync** — Session 13 carry-over (Wave 29-N)
4. **KrabEar hanging** — STT pipeline иногда зависает, нужна диагностика
5. **LM Studio 401** — local fallback broken (long-standing carry-over)
6. **FTS5 watcher** — нет auto-detect/rebuild при corruption
7. **Named Cloudflare Tunnel** — всё ещё quick-tunnel ephemeral URL
8. **17 остаточных test failures** — flaky LLM variance + 2-3 истинных хвоста

## Session 23 priorities (top 6)

1. **Cron LLM quality** — поднять reasoning depth для evening-recap, verify читабельный output
2. **DB-locked 24h soak observation** — production regression check после WAL fix
3. **vec-optimization** — `vec_chunks_meta` desync resolution; sqlite-vec upgrade
4. **KrabEar diagnostics** — instrument STT hang, найти root-cause
5. **Wave 12 final cleanup** — закрыть 17 остаточных test failures
6. **LM Studio 401** — auth handshake inspection, restore local fallback tier

## First commands for Session 23

```bash
cd /Users/pablito/Antigravity_AGENTS/Краб
cat .remember/next_session.md              # you're reading it
cat docs/SESSION_22_FINAL_REPORT.md        # full context (88 commits)
cat docs/SESSION_21_FINAL_REPORT.md        # previous session (47 commits)
cat docs/PHASE2_MIGRATION_GUIDE.md         # Phase 2 reference (historical now)
git log --oneline -15                      # recent commits
git status                                 # any drift
pytest tests/ -q --tb=no 2>&1 | tail -10   # suite health (target: 17 failures)
curl -s http://127.0.0.1:8080/api/ecosystem/health | python3 -m json.tool | head -30
curl -s http://127.0.0.1:8080/api/endpoints | python3 -c 'import json,sys;print(len(json.load(sys.stdin)))'
grep KRAB_RAG_ .env                        # confirm Phase 2 LIVE flags
```

## Restart notes

- Krab: `/Users/pablito/Antigravity_AGENTS/new\ Stop\ Krab.command` → wait → `new\ start_krab.command`
- OpenClaw: `openclaw gateway` (NOT SIGHUP)
- MCP: `./scripts/restart_mcp_servers.command` if transport closed
- Memory doctor: `./scripts/memory_doctor.command --fix` if stale chunks
- Single-service kick: `launchctl kickstart -k gui/$(id -u)/ai.krab.core`

## Push reminder

Ветка `fix/daily-review-20260421` имеет 88 unmerged commits локально — owner отдельно решает когда пушить и мержить в `main`. Не пушь автоматически.

## Key files for context

- `docs/SESSION_22_FINAL_REPORT.md` — финальный отчёт сессии 22 (88 commits broken down)
- `docs/SESSION_21_FINAL_REPORT.md` — отчёт сессии 21 (47 commits)
- `docs/PHASE2_MIGRATION_GUIDE.md` — Phase 2 activation procedure (now historical)
- `docs/MEMORY_PHASE2_IMPLEMENTATION_PLAN.md` — 8-commit план (C1-C8)
- `docs/CRON_JOBS.md` — active cron schedule
- `docs/CRON_EVENING_RECAP_FIRST_RUN.md` — first verified fire
- `docs/SENTRY_PERFORMANCE.md` — tracing taxonomy
- `CLAUDE.md` — обновлён под Session 22 close

## Orchestration reminders

- **Parallel orchestration pattern** держал стабильно 3 сессии подряд — переиспользуй
- **Feature-flag discipline**: каждое risky изменение за `KRAB_*` env-var, default off (Phase 2 — образец)
- **Cron pipeline lesson**: silent no-op 3 сессии = отсутствие observability. Всегда WARN logs для "skipped" path и manual run_now endpoint для tight feedback loops
