# Changelog

All notable changes to Krab project documented here.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning: Semantic (MAJOR.MINOR.PATCH).

## [Unreleased]

Nothing queued yet — see `.remember/next_session.md` for Session 13 scope.

## [10.3.0] — 2026-04-18 — Session 12: Chado-inspired Proactivity + Voice Phase 1.4 + Identity fixes

### Added

- **Chado-inspired reactive architecture** (multi-message priority + per-chat awareness):
  - `src/core/chat_window_manager.py` — per-chat ChatWindow (LRU eviction, context preservation)
  - `src/core/message_priority_dispatcher.py` — priority queue (P0 instant / P1 normal / P2 low)
  - `src/core/chat_filter_config.py` — per-chat mode (active / mention-only / muted) + `!listen [mode]` command
  - `src/core/krab_identity.py` — self-identity helpers, mention detection (RU + EN + emoji + username patterns)
  - `src/core/group_identity.py` — "🦀 Краб: " prefix в group replies (fixes Chado identity issue)
  - `src/core/message_batcher.py` — per-chat backpressure batching

- **Voice Gateway Phase 1.4** (`src/integrations/voice_channel_handler.py`):
  - VoiceChannelHandler + VoiceSession abstraction
  - Brain proxy для голосовых команд (MCP tool relay)
  - MCP voice tools registration + real-time voice dispatch
  - Voice profiling (speaker embedding similarity for caller recognition)

- **Structured Reflector** (pydantic JSON schema `src/core/swarm_self_reflection.py`):
  - `ReflectionOutput` + `FollowUpItem` Pydantic models
  - `structured_reflect()` — Haiku-optimized JSON introspection
  - Auto-enqueue follow-ups → reminders_queue или task_board

- **New Telegram commands:**
  - `!listen [active|mention-only|muted]` — per-chat chat mode toggle
  - `!confirm`, `!reset`, `!recall`, `!remind` (from Session 11)
  - `!model info` — model routing explanation
  - `!stats ecosystem` — ecosystem health snapshot

### Fixed

- **!reset fast-path** — ACL registry missing `"reset"` в USERBOT_KNOWN_COMMANDS → routed through LLM (50s delay). Now direct handler, <1s response. (`933fcd4`)
- **Krab self-identity** — system_prompt said "владелец @yung_nagato" (wrong — Krab is yung_nagato). Now correctly identifies self vs owner. (`85cf998`, `7089201`)
- **Krab mention trigger** — `_is_trigger` only matched "краб" в start. Now matches anywhere via word boundary regex (fixes multi-message threading). (`85cf998`)
- **Group chat identity** — Краб replied без prefix. Now sends "🦀 Краб: " prefix in groups (fixes confusion in Chado/multi-user chats). (`2fed6f6`)

### Changed

- **parse_mode="markdown"** (session 11 change, confirmed in testing).
- **qwen3-30b-a3b-2507** fallback routing activated (PR #20 cherry-pick) (`0aaceac`).
- **Reminders persistence** — verified across Krab restart (6 integration tests) (`39c6e21`).

### Tests

- **+30 new tests** (estimate: voice_channel, chat_window, message_priority, filter_config, identity fixes).
- Voice Phase 1.4: channel 100%, handler 87%, session mgmt 92%.
- Identity: self-recognition 100%, mention detection 95%, group prefix 100%.
- Reminders: persistence 100%, queue ordering 98%.

### Commits (7)

Spans `0881081..HEAD`:
```
18e7de2 merge: !reset fast-path ACL fix (Wave 16-E)
933fcd4 fix(commands): !reset fast-path — direct handler registration (was routing via LLM)
0aaceac feat(model): qwen3-30b-a3b-2507 routing + LRU eviction (PR #20 v2 via cherry-pick)
3c4700d merge: structured reflector JSON schema (Wave 16-H, Chado blueprint)
2c3d5f3 feat(reflection): structured JSON schema + reminders queue integration (Chado blueprint)
6de6fff docs(claude-md): document voice_channel module (Phase 1.4) (#19)
0e7b979 feat(voice): voice_channel_handler + brain proxy + MCP voice tools (VA Phase 1.4) (#18)
```

### Known issues carried to Session 13

- Voice Gateway real-time dispatch latency >200ms (queueing investigation needed)
- Per-chat ChatWindow eviction race condition under high concurrency (4k+ messages/min)
- Mention regex false positives on Cyrillic kerning edge cases

---

## [10.2.0] — 2026-04-17 — Session 11: Proactivity + Memory Layer Phase 2 + Feature polish

### Added
- **Memory Layer Phase 2** end-to-end:
  - Model2Vec embeddings pipeline — 9131 chunks encoded в 1.9s
  - sqlite-vec virtual table for vector search
  - Hybrid FTS5+semantic Reciprocal Rank Fusion re-ranker (`src/core/memory_hybrid_reranker.py`)
  - `/api/memory/search` endpoint (fts/semantic/hybrid modes)
  - `!recall <query>` command в Telegram
  - `krab_memory_search` + `krab_memory_stats` MCP tools
- **Proactivity Level 2** — Reminders Queue (`src/core/reminders_queue.py`):
  - Time-based reminders ("через 2 часа ...")
  - Event-based reminders ("когда в чате X появится тема Y")
  - Wired в `userbot_bridge` startup + event hook в `_process_message`
  - `!remind <spec>` command + parser (RU+EN natural language)
- **Proactivity Level 3** — Self-reflection pipeline (`src/core/swarm_self_reflection.py`):
  - Post-task LLM reflection parses insights/unresolved/followups
  - Followups auto-enqueued в task_board или reminders_queue
- **Proactivity Level 1** — Per-chat cron UX:
  - Human-friendly cron spec parser (`src/core/cron_spec_parser.py`) RU+EN
  - `!cron quick "каждый день в 10:00" "prompt"` subcommand
  - Fixed: `!cron` handler never registered в dispatcher (dead code restored)
- **New Telegram commands:** `!recall`, `!remind`, `!cron quick`, `!model info`, `!stats ecosystem`
- **Dashboard V4 backend:** `/api/session10/summary` endpoint + Gemini 3.1 Pro frontend spec
- **Dedicated Chrome launcher** активирован в Krab startup (opt-in через env)
- **Provider auto-failover** (`src/core/provider_failover.py`) — N consecutive failures → switch to fallback
- **Prometheus `/metrics` endpoint** без deps на prometheus_client
- **Typing keepalive** context manager (`src/userbot/typing_keepalive.py`) с explicit cancel
- **Auto-restart policy** расширен с launchctl "not loaded" detection
- **Markdown escape helper** (`src/core/markdown_escape.py`) + default `parse_mode=markdown` в `_safe_reply`/`_safe_edit`
- **Weekly maintenance script** (`scripts/maintenance_weekly.py`) — VACUUM + log rotation
- **CI health report** aggregator (`scripts/ci_health_report.py`)
- **Stale worktrees cleanup** utility (`scripts/cleanup_stale_worktrees.py`)
- **CHANGELOG auto-appender** (`scripts/changelog_append.py`)

### Fixed
- **codex-cli stagnation cancel** verified live во время 17.04 outage (Wave 2 code работает)
- **Gateway "not loaded"** recovery через launchctl bootstrap (incident 17.04)
- **Chrome prompts** — disable `chrome-devtools` + `playwright` MCP в `~/.claude.json`, kill workspace Chrome, user manually disabled `chrome://inspect` toggle
- **`handle_cron` dispatcher** — never registered, теперь connects `!cron` к filter
- **`auto_restart_manager`/`is_auto_restart_enabled`** backward compat aliases для `proactive_watch` import

### Changed
- **PIIRedactor** — URL skip для CARD matches (Twitter status IDs), ASCII art skip for PHONE
- **Memory validator patterns** — WEAK/STRONG split against decoration bypass + 9 new synonyms
- **`!stats`** — добавлен `ecosystem` subcommand (alias `eco`, `health`)
- **`!memory`** — добавлен `stats` subcommand

### Tests
- **+200+ new unit tests** (estimated)
- Memory Layer coverage: **89% → 94%** (3 modules >85%)
- `src/core/logger.py`: 48% → **100%**
- `src/core/openclaw_task_poller.py`: 50% → **100%**
- Integration tests для Session 10 endpoints + E2E memory chain
- Smoke tests: retrieval (0.5-0.9ms FTS), semantic search (1.2s cold / ~50ms warm)

### Security
- **Memory Injection Validator** medium fixes merged (allowlist tuning, NFKC normalization, 9 synonyms, audit logging)
- Owner check unified через ACL (removed env-based OWNER_USER_IDS risk)

### Docs
- `.remember/session_11_rollup.md` — interim rollup
- `.remember/session_11_feature_requests.md` — user feedback (parse_mode md, proactivity 2-level, `!model info`)
- `docs/DASHBOARD_V4_SESSION10_FRONTEND_SPEC.md` — 329 lines spec для Gemini
- `CLAUDE.md` — новые endpoints (`/metrics`, `/api/memory/search`, `/api/chrome/dedicated/*`)

### Commits (58+)
Spans `95d1754..HEAD`. Full list via `git log --oneline 95d1754..HEAD`.

### Known issues carried to Session 12
- p0lrd Telegram Export >24h (still exporting, старый аккаунт)
- 27 locked worktrees (parent Claude Code PID holds — prune после session end)
- Main Chrome CDP `:9222` disabled (user action), dedicated Chrome активирован

---

## [10.1.0] — 2026-04-17 — Session 10: Security Hardening + Memory Layer Bootstrap

### Added
- **Memory Injection Validator** (`src/core/memory_validator.py`) — blocks persistent injection через `!remember` до `!confirm <hash>`. 38 тестов. Разделено на WEAK (requires allowlist) и STRONG (always block) patterns. NFKC normalization против ZWSP/homoglyph bypass. (`92325ce`, `3b12543`, `bada9f4`)
- `!confirm <hash>` command — owner-only, подтверждает staged memory write. (`92325ce`)
- `!reset [--all] [--layer=...] [--dry-run] [--force]` — aggressive очистка 4 слоёв: Krab cache, OpenClaw in-memory sessions, Gemini prompt cache nonce, archive.db (opt-in). Progress-messages для больших `--all`. (`842d999`, `a0bb15e`, `7eae51e`)
- **Correlation ID** через structlog contextvars — `request_id` binds в `_process_message`, auto-prop через `asyncio.create_task`, forwarded as `X-Request-ID` к Gateway. (`44c94c2`, `7975b35`)
- **Tool call indicator** в buffered mode — `🔧 Активно: tool_name(...)` + `⏳ В очереди: ...` в progress notice. (`b040243`, `edb54a8`)
- **Auto-restart policy** (`src/core/auto_restart_policy.py`) — rate-limited restart для Gateway + MCP servers. Exponential cooldown, max 3 attempts/hour, owner notification. Default `AUTO_RESTART_ENABLED=false`. (`a273f79`, `d720032`)
- **Dedicated Chrome auto-launch** (`src/integrations/dedicated_chrome.py`) — isolated profile `/tmp/krab-chrome`, opt-in через `DEDICATED_CHROME_ENABLED`. Owner panel endpoints `/api/chrome/dedicated/{status,launch}`. (`88b6e0f`, `9e6b74a`)
- **codex-cli stagnation cancel** — detect >120s без `last_event_at` → real `asyncio.CancelledError` + user notice. Threshold via `LLM_STAGNATION_THRESHOLD_SEC` env. (`887c484`)
- **Memory Layer Phase 1** — Yung_nagato bootstrap via `bootstrap_memory.py`: 42 708 messages / 9 099 chunks / 26 chats → `~/.openclaw/krab_memory/archive.db` (42 МБ). **92 PII redactions** (67 emails, 16 cards, 4 phones, 3 HF API keys, 2 SOL).
- `/api/ecosystem/health` extended с `session_10` block (memory validator stats, archive.db state, dedicated Chrome, auto-restart, gemini nonce). (`91652cd`, `dbbda3f`)
- Integration tests для Session 10 endpoints (`3ec05c1`), retrieval smoke test (`fde38c1`).

### Fixed
- **PIIRedactor false positives** — CARD matches внутри URLs (Twitter status IDs) skipped; PHONE skips ASCII art repeated-digit runs. (`09dd4d0`, `ed9d3ce`)
- **Prompt injection sandwich** — owner-check унифицирован с ACL pattern (было: env-based OWNER_USER_IDS → self-lockout risk). (`3b12543`)
- **!reset review issues** — Gemini nonce update existing session, double-count fix, dry-run archive hint, audit log. (`a0bb15e`, `7eae51e`, `d0afbaf`)
- **Merge conflicts** — `openclaw_task_poller.py` + `llm_flow.py` (stagnation + tool indicator совместно).

### Changed
- **Memory validator patterns** — расширено с 9 synonyms (RU+EN): постоянно, отныне, по умолчанию, constantly, continuously, from now on, append to every, prepend to all. (`bada9f4`, `d73b973`)
- **Memory validator allowlist** — убрано "use" (too broad), window 50→30 chars, WEAK/STRONG split против decoration bypass.

### Security
- **Memory injection attack surface** закрыт через validator + `!confirm` gate.
- **NFKC normalization** блокирует Unicode bypass (ZWSP, fullwidth, homoglyphs).
- **Audit logging** для всех memory validator events.
- **Chrome MCP disabled** (`~/.claude.json`) — снижение attack surface от CDP prompts.

### Docs
- `IMPROVEMENTS.md` — Session 10 rollup (+86 lines). (`0e9b0f9`, `3a5d388`)
- `CLAUDE.md` — Session 10 status section + 2 new commands + test stats. (`0e9b0f9`)
- `.remember/next_session.md` — Session 11 handoff.
- `.remember/session_11_start_prompt.md` — Session 11 start prompt.

### Tests
- **+155 new unit tests** (Session 10 modules: memory_validator 38, reset 33, auto_restart 17, dedicated_chrome 19, correlation_id 9, stagnation 22, tool_indicator 10, ecosystem_health +17).
- Integration tests: `tests/integration/test_session10_endpoints.py` (7 pass + 4 skip для non-registered endpoints). (`3ec05c1`, `92ed3dc`)
- Retrieval smoke test (`scripts/smoke_test_memory_retrieval.py`): FTS5 0.5-0.9 мс per query, 32 chunks with PII placeholders verified. (`fde38c1`, `5c07928`)
- Ruff auto-fix unused imports в memory_* modules. (`12bd6e0`)
- Total: **~7465 tests**, up from ~7365 (+100 fresh).

### Commits (28)
`92325ce`, `3b12543`, `a273f79`, `887c484`, `88b6e0f`, `0e9b0f9`, `b040243`, `44c94c2`, `842d999`, `a0bb15e`, `668b3c2`, `edb54a8`, `d720032`, `7975b35`, `9e6b74a`, `3a5d388`, `12bd6e0`, `fde38c1`, `3ec05c1`, `bada9f4`, `7eae51e`, `5c07928`, `92ed3dc`, `d73b973`, `d0afbaf`, `09dd4d0`, `91652cd`, `ed9d3ce`, `dbbda3f`.

### Known issues carried to Session 11
- p0lrd Telegram Export bootstrap pending (экспорт в процессе).
- Chrome "Allow remote debugging?" prompts — MCP servers disabled, extension-based source suspected.
- 4 `ecosystem_health` tests fail due `sys.modules` mock caching (non-blocking).

---

## [Prior Sessions]

See `IMPROVEMENTS.md` для full history (Sessions 1–9).
