# Session 29 — Starter Handoff (after Session 28 close + part 3 learning extension, 2026-04-28)

## Part 3 update (post-19:40 5h reset)

**13 learning features landed** в memory + smart-routing + persona слоях:

| Feature | Commit | Что |
|---|---|---|
| A | `d287131` | Successful response retrieval boost (positive/negative feedback в RRF) |
| B | `2b0aee7` | Per-user reaction memory (threshold modifier) |
| C | `d287131` | Per-chat persona drift (system prompt suffix) |
| D | `51254b8` | Memory decay (age-based RRF multiplier) |
| E | `a2aae5b` | Multi-modal memory (vision summaries в archive) |
| F | `eea64ac` | Owner mood detection (annoyed/playful/business/focused) |
| G | `517ac23` | Topic clustering (k-means без sklearn) |
| H | `7641624` | Self-correction loop (cheap model fact-check) |
| I | `fb2ecef` | Cross-chat learning transfer (cold-start bootstrap) |
| J | `517ac23` | Session goal tracking (active projects in suffix) |
| K | `7e1952b` | Thread coherence detector (drift detection) |
| L | `51254b8` | Memory consolidation (script + soft-delete) |
| M | `25cd31c` | Native userbot read tools для LLM (6 tools) |

**Wire-ups landed**:
- `c6d7896` Bug 11 (media silent skip) — `has_media` в smart trigger
- `13b10c8` Features B (user_id) + M (set_userbot_client) в bridge
- `c8fc9cf` sender_context shim (unblock test collect)

**Backlog wire-ups (для Session 29)**:
- Feature E: `save_media_summary_to_archive` hook в bridge после `process_video_message`
- Feature G: `expand_with_cluster()` в retrieval flow (memory_engine.py)
- Feature J: `system_prompt_suffix()` из session_goals в access_control.py
- Feature I: profile.py auto-call `bootstrap_borrowed_profile` в format_persona_suffix path
- Feature K: observability hook (Prometheus metrics) — собрать данные для калибровки threshold

## Session 28 stats (final)

- **Total commits part 1+2+3**: ~45
- **Phase 2 Code Splits**: 19637 → 4430 LOC (−77.4%) через **18 waves**
- **3 Krab restarts** applied, KeepAlive policy works (only manual SIGTERM observed)
- **All Sentry top-5 fixes** verified zero events post-restart
- **Inbox cleanup**: stale 31 → 4
- **Smart Routing media-aware**: photo/video_note в группах теперь reaches AI pipeline
- **Tests**: 134 default + 121 live skipped, ~10889 collected post-shim



## Status snapshot

- Branch: `fix/daily-review-20260421` — **575+ commits ahead of main** (Sessions 24-28 непрерывно), branch behind 1 (merge-base `6cba5b6`)
- **Krab production live** — два рестарта выполнены (17:52 + 18:09), новая launchd policy применена, plist sync'нут в repo
- Phase 2 command_handlers split: 19637 → **4824 LOC** (**−75.5%**) через **17 waves** (Session 28: Wave 16 state_commands −1114, Wave 17 observability_commands −711)
- Sentry root-cause фиксы deployed (PYTHON-FASTAPI-Z/1/5W/5X/6E)
- Inbox cleanup: stale 31 → 4, attention 10 → 1
- **19 коммитов Session 28** + 3 carry-over

## Session 28 wins (Part 1 — 15 commits до restart 1)

| Commit | Tag | Что |
|---|---|---|
| `ed35081` | feat | memory_doctor расширен на 12 runtime sqlite (session/cache/tasks) |
| `74a7b95` | fix | Bug 9+3+10: reply preprocessor + phrase parasite stripper + mention в reply_to |
| `3bcb000` | fix | Pyrogram storage closed-DB race (PYTHON-FASTAPI-1, 130/24h) |
| `c0dba1a` | feat | perceptor: video frame extraction (ffmpeg) + process_video_message aggregator |
| `f23dcef` | fix | **System prompt** anti-parasite + reply-first rules (root cause Bug 9) |
| `929d1c7` | feat | swarm-в-группу: additional_response_chats infrastructure |
| `e7ba873` | fix | Bug 4: `temp_msg is source_msg` guard (MESSAGE_AUTHOR_REQUIRED) + defense-in-depth |
| `f8294e3` | fix | bootstrap: flush WAL checkpoints on shutdown + retry на disk I/O preflight |
| `3c08688` | fix | memory_indexer: executor + supervised_loop guard на restart_userbot race |
| `31594b9` | fix | **launchd respawn-storm root cause**: KeepAlive Crashed + ThrottleInterval=60 + btreeinitpage HARD marker + inbox dedupe (Agent S) |
| `4b916b8` | feat | swarm wire-up: !swarm в additional_response_chats отвечает в тот же чат |
| `0bb8318` | refactor | Phase 2 Wave 16 — state_commands (clear/forget/reset/model/web/macos/browser) −1114 LOC |

## Session 28 wins (Part 2 — 4 commits после restart 1, до restart 2)

| Commit | Tag | Что |
|---|---|---|
| `f1ea08e` | feat | inbox bulk-ack helper + endpoint `/api/inbox/bulk-ack-stale` + CLI `scripts/inbox_bulk_ack.py` (Agent U) |
| `29527c3` | feat | bridge: wire `process_video_message` для video/video_note/animation (Agent T, +165 LOC) |
| `02929a2` | fix | tests: unhang openclaw model_apply (8s→0.01s) + align mark_failed emoji 👎 (Bug 8 fixture) |
| `aa34885` | refactor | Phase 2 Wave 17 — observability_commands (watch/inbox/context/memo/bookmark/note) −711 LOC |

Inbox cleanup execution:
```bash
venv/bin/python scripts/inbox_bulk_ack.py --age-hours 24 --kind proactive_action --severity warning --target done --note "Session 28 stale cleanup"
venv/bin/python scripts/inbox_bulk_ack.py --age-hours 24 --kind proactive_action --severity info --target acked --note "Session 28 stale cleanup"
```
Result: stale 31 → 4, attention 10 → 1, остались только 2 actionable (owner_task + approval).

Manual ops:
- `~/Library/LaunchAgents/ai.krab.signal-ops-guard.plist` → `.disabled.session28` (broken: запускал несуществующий script, 28МБ stderr/3 weeks)
- launchctl bootout/bootstrap `ai.krab.core` для активации новой KeepAlive policy
- Restart 1 (17:52): применил коммиты ed35081..0bb8318
- Restart 2 (18:09): применил коммиты f1ea08e..aa34885

## Sentry root-cause map

| ShortId | Events 24h | Root cause | Commit fix | Filter |
|---|---:|---|---|---|
| PYTHON-FASTAPI-Z | 337 | Restart-storm 26.04 (DB corruption + KeepAlive=true без throttle) | `31594b9` | btreeinitpage marker + plist policy |
| PYTHON-FASTAPI-1 | 130 | pyrogram closed-DB race (Session.restart) | `3bcb000` | storage shutdown guard |
| PYTHON-FASTAPI-5W | 9 | WAL не flush'ился на shutdown → next boot disk I/O | `f8294e3` | wal_checkpoint(FULL) + retry |
| PYTHON-FASTAPI-5X | 7 | memory_indexer race на restart_userbot | `3c08688` | executor + supervised_loop guards |
| PYTHON-FASTAPI-6E | 5 | runtime detection of ProgrammingError | `3bcb000`+`ed35081` | downgrade в Sentry filter |
| PYTHON-FASTAPI-60 | 24 | external openclaw cloud 500 (26.04) — self-recovered | — | можно resolve в Sentry |
| PYTHON-FASTAPI-67 | 6 | generic Traceback (no culprit) | — | требует custom investigation |
| PYTHON-FASTAPI-66 | 6 | db_corruption_detected: late marker | `31594b9` | расширенные markers |

## Что live в production (Session 28)

- **anti-parasite** в system prompt (`access_control.py:_append_runtime_constraints`) + stripper в `llm_text_processing.py` как safety net
- **reply preprocessor** в `userbot/reply_preprocessor.py` (extract_reply_segments / build_segmented_prompt / has_persona_mention_in_reply_to)
- **mention_detector(scan_reply_to=True)** — mention в теле reply_to триггерит Krab
- **launchd KeepAlive** только на crash/non-zero exit + ThrottleInterval=60 (storm proof)
- **WAL checkpoint** на shutdown + retry на disk I/O при boot
- **memory_doctor** покрывает 12 db (session/cache/tasks/archive)
- **swarm-в-группу** infra ready, How2AI требует только entry в `swarm_channels.json`
- **state_commands** module extracted (16 modules now в `src/handlers/commands/`)

## Wave 18+ backlog (из Wave 17 отчёта)

`command_handlers.py` ~4824 LOC осталось:
- **Wave 18** — memory_admin_commands (~400 LOC): handle_memory + 4 private subhandlers + 4 collect helpers + format_memory_stats
- **Wave 19** — crypto_commands (~150 LOC): encrypt/decrypt + _derive_key + _xor_crypt
- **Wave 20** — knowledge_commands (~700 LOC): weather/define/urban/news/currency/convert/color/emoji/qr
- **Wave 21** — diagnostic_commands (~остальное): bench/screenshot/eval/run/time/typing/link/say/listen/filter/chado/e2e_smoke

Также остались: handle_shop, handle_confirm, handle_help, _swarm_status_deep_report (multi-use), _reply_tech.

## Session 29 priorities

### P0 (observation — первые 24-72h)
1. **Sentry post-restart observation** — после restart 28.04 17:52 проверить через 24h:
   - PYTHON-FASTAPI-Z: ожидание ~1-2/day (manual restart only) vs 48/day baseline
   - PYTHON-FASTAPI-1: ожидание ~0/24h (storage guard)
   - PYTHON-FASTAPI-5W/5X/6E: ожидание ~0
2. **Мониторинг launchd policy** — что будет при следующем corruption: quarantine→clean exit→**no respawn** ожидается. Если respawn — investigate.
3. **Verify in How2AI**: длинная цитата с @yung_nagato в теле триггерит Krab; ответы не оканчиваются «если хочешь, могу...».

### P1 (loose ends)
4. **swarm config** для How2AI: добавить в `~/.openclaw/krab_runtime_state/swarm_channels.json`:
   ```json
   "additional_response_chats": [
     {"chat_id": -1001587432709, "title": "ЧАТ How2AI", "respond_in_same_chat": true}
   ]
   ```
   Потом invite team-аккаунты (`@kraab_traders` etc.) в чат.
5. **Vision/video bridge wire-up** — `c0dba1a` оставил `process_video_message` готовой; нужен 10-line patch в `src/userbot_bridge.py` media handler (см. example в Agent G отчёте).
6. **Inbox stale=31 bulk-ack** — старые items до Agent S fix. Можно через `/api/inbox/update` per item или новый endpoint `/api/inbox/bulk-ack-stale`.
7. **pytest unit hanger** — какой-то тест таймаутит на `waiter.acquire()` — investigate (вероятно asyncio queue lock в одном из новых tests).

### P2 (architectural)
8. **Wave 17+** — в `command_handlers.py` ~5523 LOC осталось. Кандидаты: status/inbox/memo/bookmark/note/todo + ~20 мелких.
9. **CLAUDE.md autotables refresh** — нет скрипта, придётся manual update test counts + endpoints.
10. **Merge consideration** — 571 ahead, main всего 4 ahead. Single big rebase + merge PR с rich description рекомендован (см. Session 28 анализ).

### P3 (backlog)
11. **VPN migration** по `/Users/pablito/Antigravity_AGENTS/VPN/MIGRATION_PLAN_RU.md` (OrbStack)
12. **HNSW migration prep** — vec count ~72k / 250k threshold
13. **Bug 5 video vision integration** — wire-up

## Operational lessons (Session 28)

1. **Root cause vs symptom**: anti-parasite **system prompt** rules — корень; stripper — safety net. Pyrogram storage **guard** — корень; Sentry filter — defense in depth. Не поднимать filter без guard.
2. **launchd KeepAlive=true опасно** при `sys.exit(non_zero)` — нет throttle = respawn storm. KeepAlive: { Crashed: true } restart только на signal-crash.
3. **Multi-account contamination**: `signal-ops-guard.err.log` принадлежал USER2 — multi-account setup от Session 28 unintentionally подложил под другую учётку. Verify ownership при подобных issues.
4. **Massive parallel sub-agent dispatch**: 9 агентов одновременно — 5 sonnet/4 haiku, file-ownership matrix prevented races. Haiku падает при `prompt > N tokens` (CLAUDE.md takes most of context). Sonnet безопаснее для длинных contexts.
5. **dual-namespace lookup pattern зрелый** — Wave 16 single-commit landing.
6. **Pre-commit hook auto-stages** related files (proactive_watch.py + test_inbox_dedupe ушли в commit launchd hardening — bundle by accident, не критично).

## Operational commands

```bash
cd /Users/pablito/Antigravity_AGENTS/Краб
cat .remember/next_session.md                   # this file
git log --oneline 8945a5f..HEAD                 # Session 28 commits
launchctl list ai.krab.core | grep PID          # должен быть PID, exit 0
curl -s http://127.0.0.1:8080/api/health/lite | python3 -m json.tool

# Sentry observation (24h+ after 2026-04-28 17:52 restart)
mcp__krab-p0lrd__krab_sentry_status statsPeriod=24h limit=20

# Тесты
venv/bin/python -m pytest tests/unit/ -q --tb=line --timeout=30 -x

# Memory doctor
venv/bin/python scripts/memory_doctor.py --all-db

# Verify plist policy active
plutil -p ~/Library/LaunchAgents/ai.krab.core.plist | grep -E "KeepAlive|ThrottleInterval"
```

## Restart notes

- **При следующем corruption**: quarantine → exit(78) → **launchd НЕ перезапустит** (KeepAlive: Crashed only) → manual `new start_krab.command` нужен
- Если нужен auto-recovery — добавить отдельный watchdog script (не KeepAlive=true)
- ThrottleInterval=60 защищает от любых respawn-loops в будущем

## Files for Session 29 reference

- `scripts/launchagents/ai.krab.core.plist` — committed plist (sync'нут с ~/Library/)
- `~/Library/LaunchAgents/ai.krab.signal-ops-guard.plist.disabled.session28` — disabled, restore только при необходимости
- `src/handlers/commands/state_commands.py` — Wave 16 module
- `src/userbot/reply_preprocessor.py` — Bug 3+10 fix
- `src/core/memory_indexer_worker.py` — race-proof
- `src/bootstrap/db_corruption_guard.py` — расширенные markers + WAL flush
- `tests/unit/test_inbox_dedupe_root_cause.py` — Agent S regression coverage

## Sub-agent dispatch lessons

- ✅ Best results на Sonnet model (general-purpose / krab-code-worker)
- ❌ Haiku падает с "Prompt is too long" из-за CLAUDE.md context — для read-only research лучше bash + grep напрямую
- ✅ File-ownership matrix критична: 9 агентов одновременно прошли без git conflict
- ✅ Pre-commit hook добавляет files в staged — иногда неожиданно (Agent S inbox tests)

## Test counts

- Session 28 final (estimated): **~10650 passed** (новые: state_commands 8 + reply_preprocessor 11 + parasite stripper 11 + session lifecycle 11 + sentry filter coverage + perceptor video 10 + swarm additional 15 + memory_indexer race 4 + inbox dedupe 7 + memory_doctor all-db 8 + author_required 7 + WAL checkpoint 5 + persona anti-parasite 3 = +113 новых)
- Pytest unit hanger: 1 тест зависает на waiter.acquire (--timeout=30 ловит) — backlog
