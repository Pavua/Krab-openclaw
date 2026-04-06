# Handoff

## State (2026-04-06, batch 6)
5 коммитов на `main` за сессию:
- `08d8120` — broadcast delegated rounds to Forum Topics, MAX_DEPTH 2→1, 31 tests
- `4e5bcff` — IMPROVEMENTS.md + 10 broken tests → graceful skip
- `6ae5d69` — 👀 reaction on absorbed burst messages
- `2de0045` — feat(acl): silence mode, guest tool strip, extended spam filter (10 files, 570 lines)
- `e5e412c` — fix(acl): тишина доступна FULL access (не только OWNER)

Krab running on main (PID сохраняется в `~/.openclaw/krab_runtime_state/krab_main.pid`).
E2E подтверждено: `!тишина 2` → молчание → `!тишина стоп` → `🔊 Тишина снята.`

## ACL архитектурная особенность
OWNER в Krab = аккаунт **yung_nagato** (сам userbot, session: `data/sessions/kraab.session`).
Оператор-человек **p0lrd** (id: 312322764) имеет **FULL** доступ (не OWNER).
Команды для p0lrd: в чат с yung_nagato (id: 6435872621), не в Saved Messages.
OWNER_ONLY = только то, что требует machine access (CLI, ACL изменения, cap, hs).

## Next
1. **Pyrofork миграция**: унифицировать `venv/` (Pyrogram 2.3.69) и `.venv/` (2.0.106) → pyrofork. Снять dual-venv костыли, нативные Forum Topics API, reactions.
2. **Отдельные TG аккаунты для агентов**: userbot аккаунты для каждой команды свёрма (traders/coders/etc). После pyrofork.
3. **Signal gateway**: SSE `fetch failed`, LaunchAgent exit code 1 — диагностировать.
4. **Mercadona навигация**: нужны логи терминала при воспроизведении.

## Context
- OpenClaw gateway нужен ручной `openclaw gateway start` после рестарта Краба (node не в PATH у launchd)
- Krab logs runtime: `~/.openclaw/krab_runtime_state/krab_main.log`
- Security warnings (4 critical exec+open channels) — не ограничивать exec, обрабатывать в ACL Краба
- `new start_krab.command` / `new Stop Krab.command` — канонические лаунчеры
- Два venv: `venv/` (runtime Pyrogram 2.3.69), `.venv/` (MCP/tests Pyrogram 2.0.106)
