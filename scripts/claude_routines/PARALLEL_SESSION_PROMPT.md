# Prompt для параллельной Claude session

> Скопируй всё ниже (между линиями) в параллельную сессию Claude Desktop где уже работали `krab-ear-*` routines. Эта сессия знает workflow создания routines через Desktop UI.

---

Нужно создать 7 Claude Desktop routines для parallel project "Krab-openclaw" (Telegram userbot) по тому же механизму как ты сделал 10 krab-ear routines. Все промпт-контенты уже лежат в `~/.claude/scheduled-tasks/krab-openclaw-*/SKILL.md` — просто нужно зарегистрировать schedule.

Создай через Desktop UI "+ New routine" 7 routines:

## 1. krab-openclaw-commit-review
- **Schedule**: Weekdays at 8:57 AM
- **Description**: Daily commit review — flag concerns to Linear
- **Prompt**: `cat ~/.claude/scheduled-tasks/krab-openclaw-commit-review/SKILL.md`

## 2. krab-openclaw-sentry-digest
- **Schedule**: Weekdays at 9:03 AM
- **Description**: Sentry unresolved errors digest + auto-Linear for critical
- **Prompt**: `cat ~/.claude/scheduled-tasks/krab-openclaw-sentry-digest/SKILL.md`

## 3. krab-openclaw-lunch-status
- **Schedule**: Weekdays at 12:17 PM
- **Description**: Middle-day Krab ecosystem health snapshot
- **Prompt**: `cat ~/.claude/scheduled-tasks/krab-openclaw-lunch-status/SKILL.md`

## 4. krab-openclaw-linear-sync
- **Schedule**: Weekdays at 6:09 PM
- **Description**: Daily Linear active/stale issues digest
- **Prompt**: `cat ~/.claude/scheduled-tasks/krab-openclaw-linear-sync/SKILL.md`

## 5. krab-openclaw-evening-cleanup
- **Schedule**: Every day at 10:23 PM
- **Description**: Auto-close Linear issues matching recent commits
- **Prompt**: `cat ~/.claude/scheduled-tasks/krab-openclaw-evening-cleanup/SKILL.md`

## 6. krab-openclaw-weekly-recap
- **Schedule**: Every Sunday at 6:07 PM
- **Description**: Weekly recap via Canva infographic
- **Prompt**: `cat ~/.claude/scheduled-tasks/krab-openclaw-weekly-recap/SKILL.md`

## 7. krab-openclaw-monthly-arch
- **Schedule**: First day of month at 1:13 AM
- **Description**: Refresh architecture Canva design with latest metrics
- **Prompt**: `cat ~/.claude/scheduled-tasks/krab-openclaw-monthly-arch/SKILL.md`

**Проверка после**: В sidebar Routines должно появиться 7 новых под секцией "Krab-openclaw" (или вместе с ear — total 17).

Project это **Krab userbot** (не Ear). Paths:
- Project root: `/Users/pablito/Antigravity_AGENTS/Краб`
- Linear project: "Krab Session 16 — Wave 4 + Memory + Ops V4"
- Telegram handle: @yung_nagato

Спасибо за помощь!
