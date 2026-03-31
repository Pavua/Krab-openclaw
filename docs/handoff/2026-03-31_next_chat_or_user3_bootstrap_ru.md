# Next Chat Or USER3 Bootstrap

Дата: 2026-03-31
Ветка с фиксом: `codex/telegram-runtime-recovery-handoff`

## Что уже готово

- Telegram userbot restart-path усилен и проходит live restart.
- Health truth теперь показывает `telegram_userbot_client_connected`.
- Два Telegram MCP контура на `pablito` подтверждены по session SQLite:
  - `krab-telegram` -> `@yung_nagato`
  - `krab-telegram-test` -> `@p0lrd`
- Repo-level handoff по инциденту лежит в:
  - `docs/handoff/2026-03-31_telegram_runtime_recovery_and_mcp_status.md`

## Что читать первым в новом чате

1. `docs/handoff/MASTER_PLAN_SOURCE_OF_TRUTH.md`
2. `docs/handoff/2026-03-31_telegram_runtime_recovery_and_mcp_status.md`
3. Этот файл

## Truth snapshot на момент handoff

- Runtime health: `http://127.0.0.1:8080/api/health/lite` -> `ok=true`
- Userbot state: `running`
- Userbot connected: `true`
- OpenClaw gateway health: `http://127.0.0.1:18789/health` -> `ok=true`
- Focused unit pack: `150 passed`

## Что делать в новом чате на этой же учётке

- Если MCP tools Telegram не видны в интерфейсе Codex:
  - просто перезапустить Codex или открыть новый чат;
  - `~/.codex/config.toml` уже содержит оба entry.
- Если нужно продолжить именно по recovery/MCP теме:
  - работать от ветки `codex/telegram-runtime-recovery-handoff`

## Что делать на USER3

Автосинк с `pablito` в `/Users/USER3/.codex/skills` не выполнен из-за прав доступа на домашний каталог `USER3`.
Это не баг репозитория, а нормальная граница ownership между macOS-учётками.

Запускать уже из-под `USER3`:

```bash
cd /Users/pablito/Antigravity_AGENTS/Краб
python3 scripts/sync_krab_agent_skills.py --target-home /Users/USER3 --profile dev-tools
```

Если нужен только Codex-слой:

```bash
cd /Users/pablito/Antigravity_AGENTS/Краб
python3 scripts/sync_krab_agent_skills.py --target-home /Users/USER3 --profile dev-tools --codex-only
```

## Готовый краткий prompt для нового чата

```text
Продолжаем с ветки codex/telegram-runtime-recovery-handoff в /Users/pablito/Antigravity_AGENTS/Краб.
Сначала прочитай:
1) docs/handoff/MASTER_PLAN_SOURCE_OF_TRUTH.md
2) docs/handoff/2026-03-31_telegram_runtime_recovery_and_mcp_status.md
3) docs/handoff/2026-03-31_next_chat_or_user3_bootstrap_ru.md

Текущий truth:
- userbot restart-path уже починен и live restart проходит
- health/lite отдаёт telegram_userbot_client_connected=true
- krab-telegram = yung_nagato
- krab-telegram-test = p0lrd
- focused unit pack был 150 passed

Следующая цель:
- либо довести MCP mount в новом Codex-чате до callable tools,
- либо готовить merge/push этого recovery-блока без захвата чужого грязного worktree.
```
