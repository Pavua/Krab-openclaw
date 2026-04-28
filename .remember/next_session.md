# Session 28 — Starter Handoff (after Session 27 close, 2026-04-27)

## Status snapshot

- Branch: `fix/daily-review-20260421` — **290 commits** (Sessions 24-27 непрерывно)
- **Krab production live** — восстановлен через 5-layer recovery 27.04.2026
- Phase 2 command_handlers split **COMPLETE** — 19637 → 6637 LOC (**−66.2%**) через **15 waves**
- 15 модулей в `src/handlers/commands/` + `_shared` + `policy_commands`
- Smart Routing **active** — 5-stage pipeline, observation period (analyse_smart_routing.py готов)
- archive.db: ~506 MB / 753k+ msgs — memory_doctor.py 5/5 OK
- Tests: **10561 passed** / 93 skipped / 8 pre-existing fails / 0 hangers (pytest-timeout=30)
- 10 LaunchAgents активны

## Session 27 wins (~27 commits)

### Phase 2 command_handlers split (Waves 11-15, продолжение)
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
| `f348f69` | Wave 11 | admin_commands (config/set/acl/scope/role/notify/silence/...) |
| `06dfc0a` | Wave 12 | cli_commands (codex/gemini/claude/opencode/hs) |
| `450cfed` | Wave 13 | fileio_commands (ls/read/write/paste/export) |
| `240807d` | Wave 14 | group_admin_commands (afk/welcome/chatmute/slowmode/blocked/contacts/invite/members/profile/mark) |
| `9a1b3f6` | Wave 15 | content_commands (yt/img/ocr/snippet/template/grep/top/collect/fwd/spam/id/backup) |

### Bug fixes (Session 27)
| Commit | Bug |
|---|---|
| `e1ac040` | Bug 1: @yung_nagato mention не триггерил Krab |
| `5cf00ec` | Bug 3: reply_to_message context не передавался в LLM query |
| `2e873a9` | Bug 2: TTS лимит 600→1800 chars + env override |
| `a215e4a` | Bug 5 diag: расширенный media_diag log (photo/video) |
| `80221b3` | Bug 5: expand media filter — video/video_note/animation/sticker |
| `28850e4` | Bug 6: sender_name attribution в group chats |
| `1866376` | Bug 8: remove ❌ from reaction whitelist (premium-only emoji) |

### Recovery & infra
| Commit | Что |
|---|---|
| `fbf3262` | fix: dual-namespace lookup — repair Phase 2 split regressions (158→0) |
| `847786f` | fix: dual-namespace Wave 11 cleanup (49→0 regressions) |
| `dfae124` | fix: re-import chat_ban_cache как patch surface |
| `68111fc` | fix: purge stale model defaults (nvidia/nemotron-3-nano) |
| `66ae8b8` | perf: async subprocess + 60s cache для openclaw CLI hot-paths |
| `3ca34a0` | chore: pytest-timeout + 7 hangers RCA |
| `d4fb33e` | fix: repair 3 pre-existing test failures (auth_recovery/policy_matrix/inbox_status) |
| `0e6337c` | fix: memory_indexer graceful skip при executor shutdown race |
| `0c7f89d` | feat: smart_routing analyzer (22 tests) |
| `baa00e6` | docs: Yung Nagato persona (userbot vs reserve_bot channels) |

### 5-layer LLM recovery (27.04 operational)
1. `RESTORE_PREFERRED_ON_IDLE_UNLOAD=0` + `LOCAL_AUTOLOAD_FALLBACK_LIMIT=0` в `.env`
2. `codex login` (OAuth refresh token race после reboot)
3. `openclaw.json` + `agent.json` — harness `codex-cli` → `codex`
4. `npm i -g @openai/codex@latest` (0.115 → 0.125)
5. `68111fc` — code-level stale defaults purge

## Что live в production (Session 27)

- **15 handler modules** в `src/handlers/commands/`: text_utils / chat / scheduler / voice / memory / social / ai / swarm / translator / system / admin / cli / fileio / group_admin / content
- **Dual-namespace lookup pattern** — `_X_BASELINE + _ch_attr("X", _X_BASELINE)` для монкипатчинга в тестах
- **Smart Routing** observation period начался — логи пишутся
- **subprocess hot-path cache** (60s) — openclaw CLI calls не блокируют asyncio
- **REACTION_INVALID** silent — whitelist только universal emoji (👍👎❤️🔥🤔 etc.)

## Session 28 progress (2026-04-28)

- **Multi-account Codex setup:** готов и запушен (`c6198dd`). `pablito` настроен; для `USER2`/`USER3` установка требует запуска `/Users/Shared/Antigravity_AGENTS/Install Krab Codex Dev Layer.command` из соответствующей macOS-учётки из-за прав доступа.
- **Sentry:** официальный MCP `https://mcp.sentry.dev/mcp` авторизован через Safari; локальный `krab_sentry_status` smoke через `krab-telegram-test` вернул HTTP 200 и 7 unresolved issues за 24ч.
- **P0 test failures:** focused failures в `test_userbot_document_flow` + `test_userbot_message_batching` сведены к 0; root cause — устаревшие test doubles `_build_effective_user_query` после добавления `reply_context`.
- **Smart Routing observation:** `scripts/analyze_smart_routing.py` починен под ANSI structlog; за 24ч найдено 9 решений, 0 failed, 0 anomalies, все `regex_low`, response rate 0%.

## Session 28 priorities

### P0 (operational — первые 24-72h)
1. ✅ **P0 focused test failures** — document/message batching подняты до 0 в focused pack.
2. ✅ **Smart Routing logs review** — analyzer fixed + observation снят: 9 decisions / 0 failed / 0 anomalies. Данных мало, tuning не требуется.
3. **Merge consideration** — 291+ commits на ветке. Рассмотреть merge → main (ветка существует с Session 24).

### P1 (debt / fixes)
4. **Wave 16+ remaining handlers** — в command_handlers.py ещё ~6637 LOC. Оценить что осталось: clear/forget/reset/model/browser/web/macos/inbox/memo/bookmark + ~30 мелких.
5. **Bug 4** (403 MESSAGE_AUTHOR_REQUIRED) — pre-existing c Apr 8, fallback работает. Root cause не fix'нут.
6. **Vision/video frame extraction** — photo+video persona ответы без OCR.

### P2 (architectural)
7. **VPN migration** по `/Users/pablito/Antigravity_AGENTS/VPN/MIGRATION_PLAN_RU.md` (OrbStack recommendation).
8. **Subprocess phase 2** — реальный async hookup в `_build_runtime_cloud_presets` вместо cache workaround.

### P3 (backlog)
9. **HNSW migration prep** — vec count ~72k / 250k threshold. Только мониторинг.
10. **Auto-load verify** post next Mac reboot — Option C fix в Stop Krab.command.

## Operational lessons (Session 27)

1. **Dual-namespace lookup pattern зрелый** — при extraction в submodule: `_X_BASELINE = original; _ch_attr("X", _X_BASELINE)`. Тесты патчат command_handlers.X, а не submod.X.
2. **5-layer LLM recovery** — Mac reboot после OOM ломает: OAuth refresh tokens, Codex CLI version, harness names, stale defaults, env defenses. Каждый слой отдельный fix.
3. **macos-mcp memory leak** — Claude Desktop extension ~1 GB/час. Periodic kill recovers.
4. **Sentry "не наша проблема" tag** — KRAB-EAR-AGENT-* = другой проект, скип. PYTHON-FASTAPI-* = наш скоп.
5. **Timeout decisions** — `OPENCLAW_FIRST_CHUNK_TIMEOUT_SEC` 1 час разумно для agentic codex turns. Не резать без измерений.
6. **REACTION_INVALID** — ❌ ⚙️ 🧠 premium-only на free Telegram. Whitelist: только universal emoji.

## Operational commands

```bash
cd /Users/pablito/Antigravity_AGENTS/Краб
cat .remember/next_session.md                   # this file
git log --oneline 8945a5f..HEAD                 # Session 27 commits
launchctl list | grep -i krab                   # 10 active expected
curl -s http://127.0.0.1:8080/api/health/lite | python3 -m json.tool

# Smart Routing observation
python scripts/analyze_smart_routing.py --hours 24
grep smart_trigger_decision ~/.openclaw/krab_runtime_state/krab_main.log | tail -20

# Tests
venv/bin/python -m pytest tests/unit/ -q --tb=line --timeout=30

# DB integrity
venv/bin/python scripts/memory_doctor.py

# LLM recovery check
cat ~/.codex/auth.json | python3 -m json.tool
codex login   # если stale
```

## Restart notes

- Start: `/Users/pablito/Antigravity_AGENTS/new\ start_krab.command`
- Stop: `/Users/pablito/Antigravity_AGENTS/new\ Stop\ Krab.command`
- После Mac reboot: launchd должен auto-load. Verify: `launchctl list | grep ai.krab.core`
- Codex stuck: `codex login` → verify `~/.codex/auth.json`, restart Krab

## Files for Session 28 reference

- `docs/SMART_ROUTING_DESIGN.md` — Smart Routing spec
- `scripts/analyze_smart_routing.py` — log-based analyzer (Session 27; Session 28 ANSI structlog fix)
- `docs/MULTI_ACCOUNT_CODEX_SETUP.md` — Session 28 multi-account Codex setup/runbook
- `scripts/sync_codex_dev_layer.py` — безопасный sync Codex dev-layer для USER2/USER3
- `/Users/pablito/Antigravity_AGENTS/VPN/MIGRATION_PLAN_RU.md` — VPN migration plan (Session 27)
- `src/handlers/commands/` — 15 extracted modules
- `~/.openclaw/openclaw.json.bak_session27_*` — pre-recovery backups

## Session 28 environment/bootstrap notes

- Multi-account Codex dev-layer подготовлен для `pablito` → `USER2`/`USER3` без копирования секретов.
- Запуск из helper-учётки: `/Users/Shared/Antigravity_AGENTS/Install Krab Codex Dev Layer.command`.
- Shared source snapshot: `/Users/Shared/Antigravity_AGENTS/codex_dev_layer_source` (232 skills, 152 imported Claude skills, 14 plugin manifests; без auth/state).
- Repo launchers добавлены: `Prepare Next Account Session.command`, `Sync Krab Agent Skills.command`, `Check New Account Readiness.command`, `Check Current Account Runtime.command`, `Check Shared Repo Drift.command`.
- Безопасно синхронизируются только `~/.codex/skills`, `~/.codex/plugins/cache`, `~/.codex/vendor_imports`, `AGENTS.md`, `check_codex_tooling.command`, переносимый `config.toml`.
- Не копируются: `auth.json`, OAuth/browser profiles, Telegram sessions, `~/.openclaw`, runtime locks/PID/socket/state.
- `pablito` Codex MCP baseline: 18 MCP. Sentry MCP добавлен и OAuth confirmed через Safari; Linear/Notion/Context7/Figma тоже показываются OAuth, GitHub Copilot через `GITHUB_PERSONAL_ACCESS_TOKEN`.
- `krab-telegram` MCP config исправлен: убран устаревший `--transport stdio`, которого уже нет в `scripts/run_telegram_mcp_account.py`.
- Planetscale MCP намеренно не включён по умолчанию: OAuth запросил широкие write/delete scopes.
- GitLab/HuggingFace/Intercom/Slack/Vercel/Zapier MCP добавлены в переносимый шаблон как enabled, но требуют отдельного OAuth в каждой учётке при первом использовании.

## Test counts

- Session 27 final: **10561 passed** / 93 skipped / **8 pre-existing fails** / 0 hangers (pytest-timeout=30)
- Новые тесты в Session 27: Phase 2 Waves 11-15 coverage + smart_routing analyzer (22) + bug fix regressions
- Session 28 focused pack: **55 passed** / 0 failed — `test_userbot_document_flow`, `test_userbot_message_batching`, `test_userbot_buffered_stream_flow`, `test_userbot_photo_flow`, `test_analyze_smart_routing`.
