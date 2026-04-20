# Design A/B Helper — Canva vs Claude Design

> Session 16 tool: для любой design задачи параллельно генерировать в обоих
> инструментах → practical A/B comparison.

## Workflow

### 1. Brief подготовлен

Для каждой design задачи пишется короткий brief (< 500 char)
в `docs/design_briefs/<task>.md`. Формат:

```markdown
# Brief: <Task Name>

**Project**: krab_userbot | krab_ear
**Design type**: infographic | poster | presentation | flyer | card
**Target**: docs cover | social | architecture | session recap

## Description (the brief itself — paste into tool)
<content, < 500 char>

## Context
- Style: dark navy #0a0a1a, cyan/green accents, liquid-glass
- Language: RU/EN mix
- Use case: <where it'll be used>
```

### 2. Claude генерирует в обоих

**Canva path** (через MCP `mcp__64aef4e6-.../generate-design`):
- 4 candidates returned
- Pick best → `create-design-from-candidate`
- Save: `docs/artifacts/<task>_canva.url`

**Claude Design path** (через Claude in Chrome MCP):
- `navigate` to `claude.ai/design`
- Click "New project"
- Paste brief text
- Generate
- Save URL: `docs/artifacts/<task>_claude.url`

### 3. Compare + decide

- Side-by-side screenshots
- Track в `docs/artifacts/ab_log.md`:
  ```
  | Task | Winner | Notes |
  |------|--------|-------|
  | session_15_recap | Canva | Быстрее генерирует, понятный layout |
  | architecture | Claude Design | Больше control, html/css export |
  ```

### 4. Future use

После 5-10 tasks, смотришь win rate:
- Canva wins для quick infographics / recaps
- Claude Design wins для interactive UI mockups / complex layouts

## Quota & tab management

**Canva**:
- MCP-first, без браузера
- Quota не заявлено явно (probably subject to Canva free tier limits)

**Claude Design**:
- Browser-only через Claude in Chrome MCP
- Quota account-level (shared между Claude sessions!)
- Tab isolated per Claude session (own tab group)
- `switch_browser` если несколько Chrome profiles

## Protocol для parallel Claude sessions

- **Проект-based split**: session A — Ear, session B — userbot
- Перед началом heavy task спрашиваем другую session через .remember/
- Если quota < 20% → только одна session использует Claude Design

## Example: Krab architecture diagram (moя task)

Brief готов в `docs/design_briefs/architecture_diagram.md`.

Parallel execution:
```
1. Canva: generate-design({query: "...", design_type: "infographic"})
2. Claude Design: browser → claude.ai/design → paste → generate
3. Await both (5 min)
4. Screenshot + save URLs
5. Open A/B visual compare
```
