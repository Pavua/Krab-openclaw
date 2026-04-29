# Changelog — 2026-04-24

_Since `3cb0276` — 48 commits_

## Features

- feat(mcp): dev-loop tools pack — integrated restore ([1819a4c](https://github.com/Pavua/Krab-openclaw/commit/1819a4c042d065f1cca004c4196cf49c5dbfa579))
- feat(sentry): Performance Monitoring — trace LLM + memory retrieval spans ([5be4ad3](https://github.com/Pavua/Krab-openclaw/commit/5be4ad39983c346632c7153f7db649ed1fce61bf))
- feat(perf): C4 MMR vec-cache + Sentry Performance Monitoring ([7161885](https://github.com/Pavua/Krab-openclaw/commit/71618855ce9fe166ae855bee87b6ce09a1b5bf0c)) — Sentry: [PYTHON-FASTAPI-5D](https://sentry.io/organizations/krab/issues/?query=PYTHON-FASTAPI-5D)
- feat(cmd): !diag — one-shot diagnostic summary for owner ([7c445e0](https://github.com/Pavua/Krab-openclaw/commit/7c445e0a804fcbc39197f26f50d819a4ca0dae50))
- feat(ops): git post-commit hook — auto push + Sentry auto-resolve + optional e2e ([59b2430](https://github.com/Pavua/Krab-openclaw/commit/59b2430a331d9a0d751a03d8b110476cc7fb1f08)) — Sentry: [PYTHON-FASTAPI-XX](https://sentry.io/organizations/krab/issues/?query=PYTHON-FASTAPI-XX)
- feat(observability): install prometheus-client + Grafana dashboard JSON ([03ddb78](https://github.com/Pavua/Krab-openclaw/commit/03ddb78847fe4f71044ddde1e2ffeb95f768a7b5))
- feat(memory): C7 - vec_chunks_meta DDL + embedder writes (follow-up) ([2065c69](https://github.com/Pavua/Krab-openclaw/commit/2065c69724fe2aa36b6cf53ae060fb3de0ffa180))
- feat(memory): C6 - Prometheus metrics for retrieval mode + latency ([2df5bcf](https://github.com/Pavua/Krab-openclaw/commit/2df5bcf58536cc3d84c6c0f8aeae6a8582fee00f))
- feat(memory): C3 - RRF vector weight parametrization + helper ([16cfc4d](https://github.com/Pavua/Krab-openclaw/commit/16cfc4d8ea16d7cd05ad28f6f6d8d6423cf85e11))
- feat(memory): C1 - _vector_search() real implementation + feature flag ([e14e457](https://github.com/Pavua/Krab-openclaw/commit/e14e457b300940e386f1bc3de2dafed155ecc5bc))
- feat(mcp): Apple Notes + iMessage + Reminders + Calendar tools ([ffbfd30](https://github.com/Pavua/Krab-openclaw/commit/ffbfd30b00fad431d02abc83284c2ade02a3ee8d))
- feat(mcp+memory): db_query tool + cosine MMR on-the-fly encode ([258f777](https://github.com/Pavua/Krab-openclaw/commit/258f777897520df8dabfd6b8af2939030fa019ce))
- feat(ops): populate cron jobs + enable all swarm listeners ([4170284](https://github.com/Pavua/Krab-openclaw/commit/4170284b9ec91d26982a3b122531ba5aea01d4d3))
- feat(mcp): filesystem + git + system + http + time tools ([aa7cf30](https://github.com/Pavua/Krab-openclaw/commit/aa7cf303fa305bd1e65bb3eb61fffaac3c32efc4))
- feat(ops): gateway watchdog + error_digest first-run delay ([a24adcd](https://github.com/Pavua/Krab-openclaw/commit/a24adcd65ac8ad4641a1889c7534ca33514ecd16))
- feat(memory): MMR diversity + query expansion (P2 carry-over) ([675da20](https://github.com/Pavua/Krab-openclaw/commit/675da20f476c582783a7bd6be15e2fbe3fb37c83))
- feat(ops): activate workspace_backup + log_rotation launchagents ([4904cc2](https://github.com/Pavua/Krab-openclaw/commit/4904cc21ccdeec46a7eacfadd3523802d48d8427))
- feat(swarm): per-team tool allowlist ([8d58c5d](https://github.com/Pavua/Krab-openclaw/commit/8d58c5da2d8d50db44f2b27d4b24108f115834c8))
- feat(alerts): self-healing Cloudflare Tunnel + Sentry webhook sync ([4ec5a3b](https://github.com/Pavua/Krab-openclaw/commit/4ec5a3b5f136f038cee35971dbda7f70345b1f9e))
- feat(sentry): setup_sentry_alerts.py — one-shot автоматизация alert rules ([1363209](https://github.com/Pavua/Krab-openclaw/commit/13632096678ea01333cb6f9ec8b28edca367d792))
- feat(sentry): /api/hooks/sentry endpoint + formatter — Telegram alerts from Sentry webhooks ([64cbe27](https://github.com/Pavua/Krab-openclaw/commit/64cbe27ac209e869338585a44908e3d38d7edfed))

## Bug Fixes

- fix(bridge): W32 hotfix v2 — blocklist singleton (was module import AttributeError silent fail) ([03b9e0d](https://github.com/Pavua/Krab-openclaw/commit/03b9e0ded176894050b0b38850a72680180d2eef)) — Sentry: [PYTHON-FASTAPI-5E](https://sentry.io/organizations/krab/issues/?query=PYTHON-FASTAPI-5E)
- fix(memory): Phase 2 smoke validation findings ([4dd63a1](https://github.com/Pavua/Krab-openclaw/commit/4dd63a154b9cf25034f309488351cde1ce9a56f8))
- fix(pyrogram): apply WAL + busy_timeout pragma to prevent SQLite locked ([47404df](https://github.com/Pavua/Krab-openclaw/commit/47404df7716d235d23a122a6d64d064c48e83c1b)) — Sentry: [PYTHON-FASTAPI-5A](https://sentry.io/organizations/krab/issues/?query=PYTHON-FASTAPI-5A)
- fix(model): /api/model/switch endpoint — use correct ModelManager API ([99ed09e](https://github.com/Pavua/Krab-openclaw/commit/99ed09e2e1b89cebdd95e9c60c0d89fb6232fe1f))
- fix(bridge): W32 — queue event-loop rebinding prevents RuntimeError after restart ([38a801f](https://github.com/Pavua/Krab-openclaw/commit/38a801f50148549cb29aec3427c56c45e94ad2f4))
- fix(memory): MemoryEmbedder thread-safe SQLite connection via threading.local ([ad7e453](https://github.com/Pavua/Krab-openclaw/commit/ad7e453bcaaa304cfcbf869efe7cdbca8f9f8d40))
- fix(bridge): W32 — !status spam loop in How2AI (critical prod regression) ([8b8b383](https://github.com/Pavua/Krab-openclaw/commit/8b8b3832beec305602a77d81817db18568cde1a2))
- fix(guard+acl): phantom precision + ACL key migration тишина→silence ([3951980](https://github.com/Pavua/Krab-openclaw/commit/3951980986e11adec1e5772a80ae915dd2b61652))
- fix(ops+tests): shell scripts status enforcement + swarm ContextVar concurrency tests ([68877a2](https://github.com/Pavua/Krab-openclaw/commit/68877a29c59b3b25f36ffcf051a701008307afe2))
- fix(memory): narrow BLE001 exceptions + WARN level for silent failures ([99c059d](https://github.com/Pavua/Krab-openclaw/commit/99c059d591c78511971b690cbd165bbea1ec9aca))
- fix(mcp): db_query proper SQL gate — sqlite3.complete_statement + comment stripping ([28968b0](https://github.com/Pavua/Krab-openclaw/commit/28968b03243fc7c9eae3d1b78e08546b41ddf4ae))
- fix(sentry): hard-require SENTRY_WEBHOOK_SECRET + auto-generate at boot ([8737999](https://github.com/Pavua/Krab-openclaw/commit/87379998c7144627e0cb4012a790ab088936f31c))
- fix(mcp): SSRF guard + content-type check in http_fetch ([edc656e](https://github.com/Pavua/Krab-openclaw/commit/edc656e57cb74ed19259b682fec8727bc06ff417))
- fix(bridge): register !version + !silence handlers — E2E regression fix ([bf75cf2](https://github.com/Pavua/Krab-openclaw/commit/bf75cf2d48258b5f65bb9f98e4a6d8d227bb1d59))
- fix(digest): error_digest_loop 6h→24h + Prometheus metric krab_error_digest_fired_total ([4487045](https://github.com/Pavua/Krab-openclaw/commit/4487045c8ff0abf98f84820137f89e4cd0ab4255))
- fix(digest): weekly fires on startup, nightly_summary wired to bootstrap ([a4b0114](https://github.com/Pavua/Krab-openclaw/commit/a4b011433fa4113efb9f8c2100c6e32699d24c8d))
- fix(phantom-guard): + messageId/delivery-confirmed patterns after live-test regression ([d6f62ac](https://github.com/Pavua/Krab-openclaw/commit/d6f62ac52c12061944eec2cf2b77fb1b05cf466a))

## Performance

- perf(memory): C4 - MMR vec-cache reads pre-computed embeddings (10× MMR speedup) ([935184f](https://github.com/Pavua/Krab-openclaw/commit/935184f96198b5d1cabf070384122a995e7d214d))
- perf(memory): pre-warm Model2Vec always on bootstrap (fixes 1.8s cold) ([cc9829b](https://github.com/Pavua/Krab-openclaw/commit/cc9829bee24fd29cc96201673d64bad10726edff))
- perf(memory): C5 - dedicated ThreadPoolExecutor for embedder (persistent connection) ([757edc4](https://github.com/Pavua/Krab-openclaw/commit/757edc4956ff687f5491712df84df7d48c5f9a99))

## Documentation

- docs(audit): Session 21 infrastructure status snapshot ([5e02ce0](https://github.com/Pavua/Krab-openclaw/commit/5e02ce034cdd56af8c953ee0ddd71e24996e9c05))
- docs(memory): Phase 2 implementation plan (8 commits, feature-flagged) ([28a3602](https://github.com/Pavua/Krab-openclaw/commit/28a3602c67802ca3993e2c6c8986ac7b94a5e94c))
- docs: Memory Phase 2 activation + sqlite-vec desync diagnosis ([01a650e](https://github.com/Pavua/Krab-openclaw/commit/01a650e2ea3ddfddf1bbe7c5a51a7cb4bdccd9d5))
- docs(swarm): tool-per-team scoping implementation plan ([10e00ef](https://github.com/Pavua/Krab-openclaw/commit/10e00ef5bafb62feb962c63bb38417f013218060))

## Tests

- test(security): comprehensive tests for operator_info_guard + sentry_webhook_formatter ([8c21c5a](https://github.com/Pavua/Krab-openclaw/commit/8c21c5a027840d6f6e0fa66837f139a86b888b0f))
- test(e2e): MCP-based smoke harness for W26/W31 regressions ([808a508](https://github.com/Pavua/Krab-openclaw/commit/808a508021f445eef1f58098845e30bde1ddc9a6))

## Uncategorized

- docs(audit) + fix(cron): routines profit audit + silence 1230 warn/session spam ([f85646c](https://github.com/Pavua/Krab-openclaw/commit/f85646c7076bfe86708d9699174622dbd5b2e84a))

## Stats

- 48 commits
- 1 authors: Pavua
- +13724 LOC, -379 LOC
