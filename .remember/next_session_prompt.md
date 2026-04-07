# Промпт для новой сессии Краба (после batch 10, 2026-04-07)

Скопируй текст ниже одним блоком в новую сессию Claude Code, находясь в worktree
`/Users/pablito/Antigravity_AGENTS/Краб/.claude/worktrees/confident-diffie`.

---

Привет. Продолжаем работу над Крабом после **batch 10 (5 коммитов на ветке `claude/confident-diffie`)**.
Полный handoff — `.remember/remember.md`. PR с этой работой — https://github.com/Pavua/Krab-openclaw/pull/3.

## Состояние на момент закрытия batch 10

**Тесты:** `tests/unit/` → **691 passed, 15 skipped, 0 failed.** Стартовали с 44 fails (handoff batch 9), финиш — 0.
Запуск: `PATH=/opt/homebrew/bin:$PATH /Users/pablito/Antigravity_AGENTS/Краб/venv/bin/python -m pytest tests/unit/ -q`

**Runtime:** OpenClaw gateway восстановлен после утреннего падения 07:55:23 (auto-update сломал config).
Версия `2026.4.5`, port 18789 LISTEN, `/health 200`. Бэкап старого config:
`~/.openclaw/openclaw.json.bak_pre_upgrade_20260407_155631`.

**Forum topics:** работают (`swarm_channels.json` заполнен, broadcasts в группу `🐝 Krab Swarm`
verified).

**Ветка:** `claude/confident-diffie`, PR #3 открыт в `Pavua/Krab-openclaw` — готов к merge.

## Приоритет 1: PR #3 → main

5 commits batch 10 + всё что было ранее. Можешь сделать `gh pr merge 3 --squash` или
`--merge`. Ничего блокирующего: 691 тест зелёный, runtime up.

## Приоритет 2: Mercadona навигация

Блокер ждёт твоих **логов терминала** (упоминалось ещё в batch 9 handoff). Без них
дальше копать не могу.

## Приоритет 3: Cleanup `.venv.OLD`

`/Users/pablito/Antigravity_AGENTS/Краб/.venv.OLD_DELETE_ME_SAFELY` (1.5GB) можно удалить
через `rm -rf` если runtime стабилен ≥1 сутки **без** повторного gateway-инцидента.
Таймер был перезапущен сегодня, так что проверь: если за прошедшие сутки ничего не
падало — удаляй.

## Приоритет 4: Аудит claude code config (уже сделан)

В `~/.claude/settings.json` отключены лишние plugins (playwright, serena, greptile,
agent-sdk-dev, plugin-dev, mcp-server-dev, fastly-agent-toolkit, ai-firstify,
ai-plugins, swift-lsp, skill-creator, claude-code-setup, ralph-loop, telegram).
Должно сэкономить ~25-30k токенов фиксированного контекста и оттянуть premium-порог
200k. Если что-то из отключённого вдруг понадобится — включи обратно одной строкой
в settings.json (`"plugin-name@claude-plugins-official": true`).

## Канонические команды

```bash
# Krab
"/Users/pablito/Antigravity_AGENTS/new start_krab.command"
"/Users/pablito/Antigravity_AGENTS/new Stop Krab.command"

# Gateway (НЕ SIGHUP openclaw)
PATH=/opt/homebrew/bin:$PATH openclaw gateway start
PATH=/opt/homebrew/bin:$PATH openclaw gateway stop

# Тесты — единый venv + PATH с node (часть тестов спавнит subprocess)
PATH=/opt/homebrew/bin:$PATH /Users/pablito/Antigravity_AGENTS/Краб/venv/bin/python -m pytest tests/unit/ -q

# PR
PATH=/opt/homebrew/bin:$PATH gh pr view 3
PATH=/opt/homebrew/bin:$PATH gh pr merge 3 --squash  # или --merge
```

## ACL (не менялось)

- OWNER = **yung_nagato** (id 6435872621), session `kraab.session`
- p0lrd (id 312322764) — FULL, но НЕ OWNER
- Тестовые команды от p0lrd — в ЛС `@yung_nagato`

## С чего начать

Запусти эти три параллельно для синхронизации:
1. `git log --oneline -10` (увидишь 5 batch-10 commits)
2. `mcp__krab-p0lrd__krab_status` (runtime жив?)
3. `PATH=/opt/homebrew/bin:$PATH gh pr view 3` (PR ещё открыт?)

Затем спроси меня что делать первым: merge PR / Mercadona логи / `.venv.OLD` cleanup / другое.
Поехали.
