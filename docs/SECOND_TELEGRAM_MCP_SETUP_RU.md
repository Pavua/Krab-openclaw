# SECOND TELEGRAM MCP SETUP

Этот документ фиксирует оптимальный способ подключить второй личный Telegram-аккаунт для transport-тестов, не ломая текущий контур `Yung Nagato`.

## Зачем нужен второй аккаунт

С текущим MCP под `Yung Nagato` уже можно проверять:
- self-flow;
- owner text+voice smoke;
- наличие voice delivery;
- отсутствие scratchpad leakage в простом сценарии.

Но для полного live Telegram E2E этого недостаточно. Второй аккаунт нужен, чтобы честно проверять:
- `mention-gated/group flow`;
- `reply_to_me` в группе;
- forwarded burst / debouncing;
- inbound не от self;
- reserve fallback в более реалистичном сценарии.

## Рекомендованная схема

- `Yung Nagato` оставить как боевой userbot/runtime аккаунт.
- Подключить второй личный аккаунт как отдельный Telegram MCP server.
- Создать отдельную тестовую группу, где будут:
  - `Yung Nagato`
  - второй аккаунт
  - reserve bot
  - позже при желании `p0lrd`

## Шаг 1. Авторизовать вторую MCP-сессию

Используй launcher:

`/Users/pablito/Antigravity_AGENTS/Краб/Login Second Telegram MCP Account.command`

Он:
- спросит `session name` (по умолчанию `krab_test`);
- запустит `mcp-servers/telegram/auth_setup.py` через repo-level `venv`, где уже установлен `pyrofork 2.3.69` (импортируется как модуль `pyrogram`);
- сохранит отдельный session-файл вида:
  - `~/.krab_mcp_sessions/krab_test_mcp.session`

Важно:
- используются те же `TELEGRAM_API_ID` и `TELEGRAM_API_HASH` из `.env`;
- отдельность достигается через другой `TELEGRAM_SESSION_NAME`, а не через смену API-ключей.

## Шаг 2. Добавить второй MCP entry в Codex

В `~/.codex/config.toml` добавь ещё один сервер рядом с существующим `krab-telegram`:

```toml
[mcp_servers.krab-telegram-test]
command = '/Users/pablito/Antigravity_AGENTS/Краб/venv/bin/python'
args = [
  '/Users/pablito/Antigravity_AGENTS/Краб/scripts/run_telegram_mcp_account.py',
  '--session-name',
  'krab_test',
  '--transport',
  'stdio',
]
startup_timeout_ms = 20000
```

Если выберешь другой alias вместо `krab_test`, замени его и здесь.

## Шаг 3. Перезапустить Codex

После правки `~/.codex/config.toml` перезапусти Codex-сессию, чтобы новый MCP server появился в инструментах.

## Что получится после этого

У агента будут два независимых Telegram-контура:

- `krab-telegram`:
  - текущий аккаунт `Yung Nagato`
- `krab-telegram-test`:
  - второй личный аккаунт для live transport-проверок

Это позволит:
- отправлять реальные сообщения не от self;
- делать `@mention` и `reply_to_me` в тестовой группе;
- проверять burst/debounce без подмены условий;
- не трогать боевой userbot-контур.

## Чего делать не надо

- Не перелогинивать `Yung Nagato` в другой аккаунт.
- Не заменять существующий `krab-telegram` новым аккаунтом.
- Не тестировать group-flow в реальных боевых чатах вслепую, если нет safe-target.

## Минимально достаточный вариант

Если хочешь самый быстрый practical path:

1. Запусти `Login Second Telegram MCP Account.command`
2. Выбери alias `krab_test`
3. Добавь `krab-telegram-test` в `~/.codex/config.toml`
4. Перезапусти Codex
5. Дай мне chat id тестовой группы

После этого можно честно добить:
- `mention-gated/group flow`
- `reply_to_me`
- `forwarded burst debouncing`
- live reserve fallback в группе
