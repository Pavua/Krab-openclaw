# Новый Чат — Готовый Prompt

Скопируй текст ниже в новый диалог вместе с этой папкой.

```text
Продолжаем работу в репозитории /Users/pablito/Antigravity_AGENTS/Краб.

Сначала прочитай:
1. docs/handoff/2026-03-31_transition_bundle/01_CURRENT_STATE_RU.md
2. docs/handoff/2026-03-31_transition_bundle/04_GIT_AND_EVIDENCE_RU.md
3. docs/handoff/MASTER_PLAN_SOURCE_OF_TRUTH.md

Текущий truth:
- активная ветка: codex/telegram-runtime-recovery-handoff
- draft PR: https://github.com/Pavua/Krab-openclaw/pull/2
- Telegram userbot restart recovery уже усилен и live restart проходит
- /api/health/lite теперь отдаёт telegram_userbot_client_connected=true
- krab-telegram = yung_nagato
- krab-telegram-test = p0lrd
- focused unit pack был 150 passed

Если Telegram MCP tools не видны в новом чате, сначала проверь, перечитал ли Codex ~/.codex/config.toml после нового запуска/нового чата.

Работай дальше от этой recovery-точки и не трогай unrelated грязные файлы worktree без необходимости.
```

## Если нужен короткий prompt

```text
Продолжаем с ветки codex/telegram-runtime-recovery-handoff в /Users/pablito/Antigravity_AGENTS/Краб.
Прочитай bundle:
- 01_CURRENT_STATE_RU.md
- 04_GIT_AND_EVIDENCE_RU.md

Нужно продолжать от уже закрытого recovery-блока без потери truth:
- userbot restart recovery работает
- MCP mapping truth подтверждён
- PR #2 уже открыт draft
```
