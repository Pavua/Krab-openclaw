# Changelog

All notable changes to Krab project documented here.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning: Semantic (MAJOR.MINOR.PATCH).

## [Unreleased]

Nothing queued yet — see `.remember/next_session.md` for Session 11 scope.

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
