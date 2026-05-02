# Session 32 — Starter Handoff (after Session 31 close, 2026-05-01)

## TL;DR

- **Branches**: `main` + `fix/daily-review-20260421` — both pushed via Tor SOCKS5
- **Session 31**: 19 waves, ~25 commits, ~15000 lines, 220+ new tests
- **Bugs fixed**: 8 (reply media, timeouts, VPN tools, notifications, forward dedup, cron, CancelledError, tor_fetch)
- **Startup**: 12s → **1.55s** (8x faster)
- **codex-cli response**: 60-150s → **36s**
- **New commands**: `!contacts` (list/search/alias/resolve) + `!tor` (status/ip/newid/fetch)
- **Darknet toolkit**: Tor + HexStrike + Hive Crypto + OSINT = **620+ tools** via OpenClaw
- **Telegram resolver**: 4-strategy pipeline + contact cache + 4 new MCP tools (52 total)
- **codex-cli = intentional primary** — latency is design trade-off, NOT a bug
- **Git push**: auto via Tor (`git config --global http.https://github.com.proxy socks5h://127.0.0.1:9050`)

## P0 — Требует действий сразу

1. **Session corruption persists** — kraab.session keeps corrupting despite VACUUM suppress. Last recovery from Apr 30 backup (436 peers). Root cause may be swarm session parallel writes or launchd timing. Investigate deeper.
2. **Restart Krab** to pick up Wave 19 code (!contacts, !tor commands)
3. **How2AI admin unban** — @yung_nagato banned, hardcoded in CHAT_PERMANENT_BAN_LIST

## P1 — Следующая итерация

4. Wire resolver into MCP `send_message` (handle_say done, MCP pending)
5. Test !contacts resolve + !tor status live in Telegram
6. Enable AB testing (`KRAB_AB_TESTING_ENABLED=1`)
7. Cron LLM output quality — short/truncated responses
8. FTS5 watcher + auto-rebuild

## P2 — Архитектура

9. chunk_clusters first recluster (`scripts/memory_recluster.py`)
10. HNSW migration prep (>250k chunks)
11. Named Cloudflare Tunnel (still ephemeral)
12. Parallel tool execution (Idea 9)

## Quick reference

```bash
cd /Users/pablito/Antigravity_AGENTS/Краб

# Start/stop
"/Users/pablito/Antigravity_AGENTS/new Stop Krab.command"
"/Users/pablito/Antigravity_AGENTS/new start_krab.command"
# NEVER use kickstart -k (causes session corruption)

# Health
curl -sS http://127.0.0.1:8080/api/health/lite | python3 -m json.tool
curl -sS http://127.0.0.1:8080/api/version | python3 -m json.tool

# New Session 31 endpoints
curl -sS http://127.0.0.1:8080/api/memory/doctor
curl -sS http://127.0.0.1:8080/api/swarm/auto-executor/status
curl -sS http://127.0.0.1:8080/api/swarm/channels/status
curl -sS http://127.0.0.1:8080/api/openclaw/cron/status

# Git (auto Tor proxy for GitHub)
git push origin main fix/daily-review-20260421

# Tests
venv/bin/python -m pytest tests/unit/ -q --tb=line --timeout=30

# Session recovery
cd data/sessions && cp kraab.session.bak.1777575968 kraab.session
```

## Session 32 starter prompt

```
Привет! Krab project — /Users/pablito/Antigravity_AGENTS/Краб, ветка fix/daily-review-20260421.

ПЕРВЫЙ ШАГ — прочитай handoff:
- cat .remember/next_session.md
- git log --oneline -5

Session 31 была эпичной: 19 волн, 25 коммитов, 15000 строк, 220+ тестов.
Startup 1.55s (было 12s), codex-cli 36s (было 150s), 620+ tools через OpenClaw.

P0 для Session 32:
1. kraab.session corruption — VACUUM patch есть но corruption продолжается. Investigate swarm parallel writes.
2. Restart Krab чтобы !contacts и !tor подхватились
3. How2AI admin unban

P1:
4. Wire resolver в MCP send_message
5. Test !contacts и !tor live
6. Enable AB testing

Style: русский, parallel max на Sonnet/Haiku (10+ агентов).
Git push автоматически через Tor SOCKS5.
НИКОГДА не использовать launchctl kickstart -k (session corruption).
```
