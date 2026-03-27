# Telegram Group Fallback Recovery — 2026-03-27

## Контекст
- Канал: `YMB FAMILY FOREVER` (`-1001804661353`)
- Инициатор: второй Telegram MCP аккаунт `p0lrd`
- Цель: подтвердить, что group/background flow после фикса больше не выдаёт сырой fallback `No response from OpenClaw.` как пользовательский ответ.

## Исторический сбой
- trigger `764818`
- text fallback `764822`: `No response from OpenClaw.`
- voice `764823`

Это доказывало, что transport и inbox lifecycle живы, но user-facing content деградировал слишком сыро.

## Фикс
- Нормализация сырых fallback-строк OpenClaw в [src/userbot_bridge.py](/Users/pablito/Antigravity_AGENTS/Краб/src/userbot_bridge.py)
- Запрет TTS/voice для error-surface
- Запрет auto-inject `/tmp/voice_reply.*` поверх error-surface

## Live E2E после фикса
- trigger: `764827`
- ack: `764828`
- final text: `764829`
- final voice: `764830`

Текст финального ответа:

```text
GROUPP0-FIX2-20260327-1834 🦀 Краб на связи, всё работает стабильно и чётко.
```

Persisted inbox:
- dedupe key: `incoming:-1001804661353:764827`
- status: `done`
- workflow: `created -> background_started -> reply_sent`

## Вывод
- Mention-gated/group flow через второй аккаунт работает end-to-end.
- Сырой fallback `No response from OpenClaw.` больше не является целевым user-facing surface для этого сценария.
- Voice остался рабочим только для содержательного ответа, а не для transport/model error.
