# Session 13 Critical Notes (18.04.2026)

## Merge conflict gotcha — IMPORTANT for future sessions

When agents produce worktree branches с conflicts, and I use `git merge -X ours/theirs`, **не все conflicts могут быть auto-resolved**. Иногда leftover markers остаются в файлах after "merge completed" message. **Always verify:**

```bash
grep -c "<<<<<<<\|=======\|>>>>>>>" src/handlers/*.py src/core/*.py src/userbot_bridge.py
```

If non-zero — resolve manually before restart.

## Python .pyc cache gotcha

Even после правки .py файлов, Python может грузить stale .pyc. При SyntaxError в старом log:
```bash
find src -name "__pycache__" -type d -exec rm -rf {} +
> logs/krab_launchd.err.log  # truncate old log чтобы не misread
```

## Live Krab verification workflow

1. `launchctl bootout` + `sleep 3` + `pkill -9 -f src.main` + `launchctl bootstrap`
2. `until curl ... | grep running; do sleep 4; done` (max 60s)
3. If still не up — check `tail logs/krab_launchd.err.log` для syntax errors

## Session 12-13 major achievements recap

### Chado-inspired architecture (FULLY WIRED в production)
- `chat_window_manager.py` (LRU, env config, eviction, /api endpoints)
- `message_priority_dispatcher.py` (P0/P1/P2)
- `chat_filter_config.py` (active/mention-only/muted, `!listen`/`!mode`, hot-reload)
- `message_batcher.py` (backpressure buffer)
- `swarm_self_reflection.py` (structured pydantic schema, auto-reminders flush)
- `krab_identity.py` + `group_identity.py` (🦀 prefix + mention detection)
- Integrated в `_process_message` via Wave 17-A (9c7794c)

### Memory Layer Phase 2 (LIVE)
- Model2Vec embeddings (9131+ chunks encoded 1.9s)
- sqlite-vec integration
- Hybrid FTS+semantic RRF re-ranker
- `/api/memory/search` + `!recall` + MCP tools
- Auto-context RAG (opt-in)

### 25+ new Telegram commands + 15+ API endpoints

### Session 13 priorities (carry forward)
1. p0lrd Telegram Export >48h — bootstrap when ready (~500k messages)
2. Dashboard V4 frontend — delegate to Gemini 3.1 Pro (spec ready)
3. Memory Phase 3 prep — query expansion, diversity re-ranking
4. Disk hygiene (99% full — user cleaning externally)
5. Auto-reactions integration wiring в llm_flow (Session 13.X TODO at top of llm_flow.py)

### Live infrastructure snapshot
- Krab PID 30653+, codex-cli/gpt-5.4 primary
- archive.db ~43.2k msgs, 9156 chunks, 50+ MB
- 224 API endpoints registered
- 145 commands

## Paths to remember

- `/Users/pablito/Antigravity_AGENTS/Краб` — main worktree
- `.remember/chado_architecture_learnings.md` — interview Q1-Q5
- `.remember/benchmarks_18_04_2026.md` — perf baseline
- `.remember/next_session.md` — handoff (updated Wave 20-K)
- `.remember/disk_audit_2026_04_18.md` — storage report
- `CHANGELOG.md` — `[10.3.0]` entry
- `docs/EXPERIMENTAL_SKILLS_WORKFLOW.md` — Chado Q5 pattern
- `docs/PROMETHEUS_MONITORING.md` — metrics + alerts
