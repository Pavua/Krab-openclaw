# Claude Desktop Routines — Krab project

Git-tracked source of truth для routines которые Claude Desktop исполняет
из `~/.claude/scheduled-tasks/<name>/SKILL.md`.

## Почему копируются сюда

Claude Desktop читает routines из user-home dir (`~/.claude/scheduled-tasks/`).
Эта папка НЕ git-tracked (вне repo). Для version control + collaboration
копируем SKILL.md в `scripts/claude_routines/` где они версионируются.

## Установка на новую машину / после сброса

```bash
mkdir -p ~/.claude/scheduled-tasks
for dir in scripts/claude_routines/krab-*; do
  name=$(basename "$dir")
  cp -r "$dir" ~/.claude/scheduled-tasks/
done
```

После — перезапуск Claude Desktop чтобы routines появились в sidebar.

## Список routines

| Name | Frequency (est.) | Quota/day | Purpose |
|------|------------------|-----------|---------|
| krab-commit-review | Daily weekdays | 0.71 | Review git log → Linear issues |
| krab-sentry-digest | Daily weekdays | 0.71 | Top unresolved errors → Telegram + Linear |
| krab-linear-sync | Daily weekdays | 0.71 | Active/todo/stale summary → Telegram |
| krab-lunch-status | Weekdays 12:17 | 0.71 | Middle-day ecosystem snapshot |
| krab-evening-cleanup | Daily 22:23 | 1.00 | Auto-close Linear issues matching commits |
| krab-weekly-recap | Sunday 18:07 | 0.14 | Canva infographic generate |
| krab-monthly-arch | 1st day 01:13 | 0.03 | Refresh architecture Canva design |
| krab-openclaw-chado-sync | Sunday 19:07 | 0.14 | Cross-AI digest → How2AI Forum Topic crossteam + DM @callme_chado |

**Total: ~4.15 fires/day** (if all run).

## Link с launchd routines

Parallel automation track — launchd plists в `scripts/launchagents/`:
- ai.krab.leak-monitor (30 min) — openclaw orphan killer
- ai.krab.health-watcher (15 min) — panel + gateway + disk
- ai.krab.ear-watcher (15 min) — Krab Ear monitor
- ai.krab.backend-log-scanner (4 h) — log anomaly detection
- ai.krab.daily-maintenance (daily 02:07) — backup + log rotation

**launchd routines FREE** (не тратят Claude routines quota). Together 5 + 8 = 13 routines total.
