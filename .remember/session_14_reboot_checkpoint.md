# Session 14 Reboot Checkpoint (2026-04-20, memory pressure)

## Why reboot
Activity Monitor: 32.1 GB used / 36 GB physical, swap 9.73 GB, compressed 16.43 GB.
Docker + LM Studio crashed earlier (user confirmed). **20+ orphan `openclaw` procs** (400-600 MB каждый) накопились от Wave 29-X SIGTERM циклов — total ~8-10 GB.

Claude Code 35 GB (big context accumulation). Ear session parallel.

## Git state (safe to reboot)
- **Branch:** `main` @ `b0868f8`
- **Session 13+14: 66 commits merged** (071e45d → HEAD)
- All Wave 29 A–YY merged except 4 in-flight agents
- Docs synced: CLAUDE.md / CHANGELOG v10.4.0 / IMPROVEMENTS / session_14_start_prompt

## In-flight at checkpoint (may be killed by reboot)
- **29-UU** native Python cron fallback (files cron_native_store.py + cron_native_scheduler.py ALREADY в main через `4b7126c` preservation commit, но без wire + tests)
- **29-ZZ** !help command coverage update
- Google Gemini cloud diagnostic (read-only)
- Full pytest baseline audit (read-only)

Their results may resurface в next session start (git log --oneline --all).

## Stash
- stash@{0}: `pre-vv-merge` (dropped)
- stash@{1+}: older stashes from previous sessions (kept)

## Key merged fixes
| Wave | Commit | Что |
|------|--------|-----|
| 29-A | 77d3c18 | !bench _safe_reply |
| 29-B | 1623461 | !archive stats/growth |
| 29-C | 6c49d7c | MEMORY_ADAPTIVE_RERANK_ENABLED |
| 29-E | 26b8b66 | handle_confirm/bench import tests |
| 29-G | a9e519c | _safe_reply sweep 18 calls |
| 29-H | 448a24f | chat_window_manager rewrite |
| 29-I | diag | !cron BROKEN (OpenClaw CLI freeze) |
| 29-J | ab2309b | +5 Prometheus alerts |
| 29-N | 519d69d | sqlite-vec repair script |
| 29-R | 46b251f | +6 alerts (14 total) |
| 29-T | 29-T | 33 branches deleted |
| 29-KK | b0d2da6 | unified is_owner_user_id |
| 29-LL | 5b4be76 | classify_priority tests |
| 29-MM | 68f5626 | ruff stash 190 test files |
| 29-OO | cd3027f | DM reactions skip |
| 29-PP | 8de3555 | FTS5/vec orphans в !health deep |
| 29-QQ | e70762d | Session 14 kickoff docs |
| 29-RR | b5551ae | LM Studio idle watcher (not wired) |
| 29-SS | 8194f4d | auto_restart skip high load |
| 29-TT | 5d78915 | chat_ban_cache auto-expire |
| 29-VV | b0868f8 | !swarm status deep |
| 29-WW | 4b7126c | OpenClaw watchdog |
| 29-XX | 5086622 | vision routing CLI redirect |
| 29-YY | 4175ae6 | chat_ban periodic wire |

## .env fix applied
`OWNER_USER_IDS=312322764` добавлен (swarm listeners разблокированы).

## User actions post-reboot
1. **Launch order**: Docker → LM Studio (unload модель сразу после launch!) → Krab (`new start_krab.command`)
2. **OpenClaw зомби**: после reboot зомби-procs мертвы. Пусть single launchd instance поднимется.
3. **Telegram export** можно запустить снова (предыдущий CHANNEL_PRIVATE error).
4. Опционально: plist edits (ExitTimeout=120, ThrottleInterval=5).
5. `!memory rebuild` для sqlite-vec orphans — когда удобно.

## Session 15 priority
1. Wire `lm_studio_idle_watcher` в bootstrap (29-RR не wired)
2. Fix 3 missing metrics emission (chunks_embedded, llm_route_latency, auto_restart_attempts)
3. Remove 6 dead alerts из krab_alerts.yml
4. Complete 29-UU cron native (files есть, нет tests + wire)
5. 29-ZZ !help coverage (in-flight)
6. Google Gemini cloud re-enable if possible (vision routing fallback for photo)

## Next session starter
`.remember/session_14_start_prompt.md` + `.remember/next_session.md` — ready.
Main HEAD check: `git log main --oneline -1` должно показать `b0868f8` (или новее если in-flight merged).
