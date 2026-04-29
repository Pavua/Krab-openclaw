# Session 29 — Starter Handoff (after Session 28 close, 2026-04-29)

## TL;DR

- **607 commits** на ветке `fix/daily-review-20260421` vs origin/main (1 trivial conflict in README.md)
- **926 files / +171k insertions / −53k deletions / +118k net LOC** за 12 дней
- **Phase 2 splits complete**: command_handlers.py 19637 → ~1226 LOC (**−93.8%**) через **21 waves** + 22 modules в `src/handlers/commands/`
- **13 learning features** (A-M) landed
- **18 idea modules** landed (1, 4, 5, 6, 7, 8, 10, 11, 12, 13, 14, 16, 17, 18, 19, 21, 22, 23, 24, 25, 26, 28+29, 30, 32, 33, 35, 36, 38)
- **All Sentry top issues** killed or stable; closed-DB regression deep fix landed
- **Krab live**: PID up via kickstart, telegram ready, gpt-5.5
- **kraab.session recovered** через sqlite `.recover` (88 peers preserved) после corruption 17:18

## Recommended next-steps order

1. **Merge to main** — single `git merge --no-ff fix/daily-review-20260421` (KA анализ recommends). Resolve trivial README conflict (additive both sides). См. отчёт KA.
2. **Sentry observation 24-72h** — после merge стабильность всех fix'ов
3. **Wire-ups bulk batch** — большая часть idea modules в backlog (pure singletons): vision-aware photo handler, channel digest cron tick, reply_scheduler tick loop, voice fingerprint ML embeddings
4. **swarm config How2AI** — 3-line JSON edit + invite team accounts (опционально)

## Что landed в Session 28 (по 7 частям)

### Part 1 (handoff push 8c9986b → ~30 commits)
- Bug 9+3+10 (reply preprocessor + parasite stripper + mention в reply_to)
- Pyrogram closed-DB race fix (`3bcb000`)
- Bug 4 (MESSAGE_AUTHOR_REQUIRED guard)
- WAL checkpoint shutdown + retry on disk I/O
- memory_indexer race fix
- Launchd respawn-storm root cause: KeepAlive Crashed + ThrottleInterval=60 + btreeinitpage marker
- Inbox dedupe + bulk-ack
- Wave 16 state_commands extracted

### Part 2 (~30 commits)
- Wave 17 observability_commands extracted
- Wave 18 memory_admin_commands extracted
- Vision/video frame extraction (perceptor)
- USER_BANNED_IN_CHANNEL + slowmode + NoneType filter
- Bug 11 (media silent skip in groups)
- Bug 12 (deferred handoff в группах)

### Part 3 (~14 commits — 13 learning features)
- A: Response retrieval boost
- B: Per-user reaction memory
- C: Per-chat persona drift
- D: Memory decay
- E: Multi-modal memory (vision summaries)
- F: Owner mood detection
- G: Topic clustering (k-means)
- H: Self-correction loop
- I: Cross-chat learning transfer
- J: Session goal tracking
- K: Thread coherence detector
- L: Memory consolidation
- M: Native userbot read tools

### Part 4 (~10 commits — first 5 ideas + bug fixes)
- DD: Pyrogram NoneType.to_bytes guard
- DE: 35 test fixes (snippet + landmines)
- DH: Idea 17 owner offline holdover
- DI: Idea 22 REPL session
- DJ: Idea 32 proactive suggestions
- EC: Idea 21 TODO extractor

### Part 5 (~14 commits)
- Wave 19+20 crypto + info commands
- closed-DB regression deep fix (swarm guard + sentry filter)
- audio summarizer (Idea 35)
- anomaly detector (Idea 26)
- tool result cache (Idea 10)
- cost-aware routing (Idea 8)
- LLM ensemble (Idea 11)
- tool composition memory (Idea 7)
- rolling auto-summarization (Idea 14)
- pyrogram guard rewrite via accessor wrapping

### Part 6 (~10 commits)
- Constitutional guardrails (Idea 12)
- Per-handler latency dashboard (Idea 23)
- Prompt A/B testing (Idea 24)
- Joke calibration (Idea 33)
- Screenshot analyzer (Idea 38)
- pyrogram patch disable+rewrite

### Part 7 (~12 commits — final batch)
- Wave 21 diagnostic_commands (−1767 LOC)
- Source attribution (Idea 16)
- Named entity memory (Idea 13)
- Conversation replay (Idea 25)
- Forget-me tool (Idea 30)
- Reply scheduling (Idea 5)
- Calendar integration (Idea 19)
- Channel digest (Idea 6)
- Auto-translate per chat (Idea 4)
- Voice message dispatcher (Idea 1)
- Voice fingerprinting (Idea 36)
- Bulk wire-ups for ideas 4/5/7/33

## Technical foundation

### File ownership matrix (для будущих parallel agents)
- **command_handlers.py** — слой re-exports + dual-namespace lookup wrappers (ne to extract more than 1226 LOC)
- **userbot_bridge.py** — message dispatch, smart routing, media handling, swarm bootstrap, learning singletons bootstrap
- **userbot/llm_flow.py** — LLM stream + reply preprocessor + anti-parasite stripper + self-correction hook + Prometheus coherence hook
- **bootstrap/pyrogram_patch.py** — apply_pyrogram_session_guard via accessor wrapping (не _get обёртку)
- **core/memory_archive.py** — schema + 4 sidecar tables: response_feedback, chunk_clusters, cluster_meta, message_media_summaries
- **core/memory_hybrid_reranker.py** — RRF + MMR + feedback boost + decay multiplier + cluster expand

### Active learning singletons (state under `~/.openclaw/krab_runtime_state/`)
- chat_ban_cache.json, chat_response_policy.json, owner_presence.json, repl_session_audit.log
- proactive_suggestions.json, owner_mood.json (cache TTL 30min)
- chat_persona_profile.json, swarm_channels.json
- entities.json (named), anomaly_baselines.json, sensitive_chats.json
- auto_translate_chats.json, scheduled_replies.json, tool_composition.json, joke_calibration.json
- voice_fingerprints.json, ab_experiments.json, session_goals.json

## Sentry — current state (Session 28 final, 24h)

| Issue | Events | Status |
|---|---:|---|
| PYTHON-FASTAPI-Z (CancelledError) | 344 | stable, only restart spam |
| PYTHON-FASTAPI-1 (closed-DB) | 140 | regression killed via swarm guard `94546da` |
| PYTHON-FASTAPI-5W (disk I/O) | 11 | +2 — transient on rapid restart, WAL retry helps |
| PYTHON-FASTAPI-67/66 (Traceback / db late) | 8 / 8 | stable |
| PYTHON-FASTAPI-6E (db_corruption_runtime) | 7 | +2 — kraab.session btreeinitpage corruption (recovered manually) |
| PYTHON-FASTAPI-6G (NoneType.to_bytes) | 4 | quiet after disable+rewrite of guard |
| PYTHON-FASTAPI-6K (no such column: _accessor) | 3 | NEW — broken DD patch artifact, disabled |
| 6H/6J (USER_BANNED) | 1+1 | filtered out via Sentry markers |

## Backlog для Session 29

### P0 — Wire-ups для landed ideas (большинство pure singletons)
- Idea 5 (reply_scheduler) — actual sender tick loop в bridge background tasks
- Idea 6 (channel_digest) — cron job для daily publish
- Idea 11 (LLM ensemble) — wire в `llm_flow` для critical queries
- Idea 12 (guardrails) — pre-send filter в llm_flow после anti-parasite stripper
- Idea 23 (handler latency) — `time_handler` decorators в Wave 11-21 modules
- Idea 24 (AB testing) — wire в system prompt builder
- Idea 26 (anomaly detector) — proactive_watch hook with cooldown
- Idea 28+29 (privacy) — wire в `memory_archive.add_message`
- Idea 32 (proactive suggestions) — pattern_detector recording в incoming hook
- Idea 33 (joke calibration) — record_joke в feedback_tracker (на reactions)
- Idea 38 (screenshot analyzer) — bridge media handler (если photo > N kb)
- Feature G (topic clustering) — `expand_with_cluster` в retrieval flow
- Feature K (thread coherence) — Prometheus metric collection (already ready)

### P1 — Architecture
- Merge to main (single `--no-ff`, 607 commits)
- WAL flush wait sentinel before rapid respawn (5W transient fix)
- kraab.session backup automation (auto `.recover` script на boot)
- pyrogram patch — observability за 24-48h before re-enabling

### P2 — Optional ideas not yet landed
- Idea 2 (sticker replies)
- Idea 3 (inline mode)
- Idea 9 (parallel tool execution)
- Idea 15 (time-aware retrieval boost)
- Idea 20 (email digest)
- Idea 27 (archive.db encryption — heavy)
- Idea 31 (multi-persona switcher)
- Idea 34 (dynamic avatar)
- Idea 37 (image generation на запрос)

## Operational notes

- Krab restart требует launchctl kickstart после первого clean exit (KeepAlive Crashed-only policy)
- Если disk I/O error — try `scripts/memory_doctor.py --all-db --json` first, then `.recover` для broken db
- Pyrogram Session.start race для NoneType.to_bytes: guard active via apply_pyrogram_session_guard
- На rapid restart cycle всегда возможен transient WAL contention — wait 30s между Stop и Start

## Files for Session 29 reference

- `scripts/memory_doctor.py --all-db --json` — DB integrity sweep
- `scripts/inbox_bulk_ack.py` — inbox cleanup CLI
- `scripts/forget_me.py` — privacy compliance scrub
- `scripts/replay_conversation.py` — dev tool для re-running history
- `scripts/memory_recluster.py` — Feature G recluster trigger
- `scripts/memory_consolidate.py` — Feature L compression
- `scripts/memory_backfill_media.py` — Feature E backfill (if needed)
