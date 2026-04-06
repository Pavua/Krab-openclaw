# Промпт для новой сессии Краба

Скопируй текст ниже в новую сессию Claude Code:

---

Продолжаем работу над Крабом (Telegram userbot на MTProto/pyrofork).

Прочитай `.remember/remember.md` — там handoff с предыдущей сессии.

## Что было сделано в прошлой сессии (3 коммита на main)

1. **Pyrofork унификация** (`00d6a41`) — убрали 4 getattr-костыля, единый `venv/`
2. **Per-team TG аккаунты для свёрма** (`876f3a5`) — traders/coders/analysts/creative пишут от своих аккаунтов (@p0lrdp_AI, @p0lrdp_worldwide, @hard2boof, @opiodimeo)
3. **Warm-up peer cache** (`6306885`) — фикс CHAT_ID_INVALID на team clients через `get_dialogs()` при старте

## Статус

4 team TG аккаунта авторизованы, добавлены в Forum группу 🐝 Krab Swarm (-1003703978531).
Конфиг: `~/.openclaw/krab_runtime_state/swarm_team_accounts.json`.
Фикс warm-up уже в коде — но **нужен рестарт Краба** чтобы заработал E2E.

## Задачи этой сессии (приоритет сверху)

### 1. Верификация per-team accounts (главное)
- Рестартни Краба: `new Stop Krab.command` → `new start_krab.command` (и `openclaw gateway start` после)
- Проверь логи `swarm_team_client_warmed_up` для всех 4 команд
- Пошли `!swarm traders тест: BTC тренд, одно предложение` через MCP telegram на @yung_nagato
- Проверь что сообщения в Forum Topics приходят от @p0lrdp_AI (traders), @p0lrdp_worldwide (coders), @hard2boof (analysts), @opiodimeo (creative) — а не от @yung_nagato

### 2. Signal gateway (если #1 OK)
- SSE поток сломан (`fetch failed`)
- LaunchAgent `ai.openclaw.signal-cli` exit code 1
- Диагностировать и починить

### 3. Cleanup
- Удалить `.venv/` после проверки что MCP серверы (yung-nagato, p0lrd) стабильны на `venv/`
- Проверить pre-existing `CancelledError` при stop Краба (не блокер, но неприятно)

## Контекст (важное)

- OWNER в Krab = **yung_nagato** (сам userbot, session: `data/sessions/kraab.session`)
- Оператор **p0lrd** (id: 312322764) имеет **FULL** доступ (не OWNER)
- Команды для p0lrd отправлять в чат с @yung_nagato (id: 6435872621), не в Saved Messages
- OpenClaw gateway после рестарта Краба: `PATH=/opt/homebrew/bin:$PATH openclaw gateway start`
- Логи рантайма: `~/.openclaw/krab_runtime_state/krab_main.log`
- Канонические лаунчеры: `/Users/pablito/Antigravity_AGENTS/new start_krab.command` и `new Stop Krab.command`
- Единый venv: `venv/` (pyrofork 2.3.69, Python 3.13)

## Что нельзя ломать
- Не SIGHUP openclaw — только `openclaw gateway start/stop`
- Не редактировать working .venv/ пока не подтверждено что всё на venv/ работает
- LaunchAgent plists: MCP серверы уже переведены на venv/, не откатывать

Начни с прочтения `.remember/remember.md`, затем рестартни Краба и проверь warm-up логи.
