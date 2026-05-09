# Session 15 Continuation Prompt (после restart Claude Desktop)

Скопируй в новый чат Claude Code после перезапуска:

---

Продолжаем Session 15 Krab после рестарта Claude Desktop (подключён Design plugin v1.2.0).

**Path:** `/Users/pablito/Antigravity_AGENTS/Краб`
**Роль:** работаю на @yung_nagato, owner = @p0lrd (user_id 312322764).
**Main HEAD:** `71a79f3` — wave 2 merged (OWNER_USER_IDS depr + 2 HTML prototypes).

## Прочитай ПЕРВЫМ ДЕЛОМ

1. `.remember/session_15_handoff_midway.md` — что сделано в первой половине сессии
2. `.remember/session_15_start_prompt.md` — исходные приоритеты сессии
3. `CLAUDE.md` — project rules

## Session 15 — сделано (8 из 12)

| # | Приоритет | Статус |
|---|-----------|--------|
| 1 | lm_studio_idle_watcher wired | ✅ 96d0c17 |
| 2 | Prometheus metrics emission (3 missing) | ✅ 96d0c17 |
| 3 | Dead alerts cleanup | ✅ 96d0c17 |
| 4 | how2ai chatban unban | ✅ inline |
| 5 | probe_gemini_key timeout | ✅ 96d0c17 |
| 6 | 29-UU cron native | ✅ 761eb0c (ранее) |
| 7 | Plist tweaks | ⏸ ждём owner confirm |
| 8 | Memory Phase 2 bootstrap | ⏸ owner экспортирует v2 (без media/public) |
| 9 | !memory rebuild | ⏸ после bootstrap |
| 10 | OWNER_USER_IDS deprecation | ✅ 71a79f3 |
| 11 | Dashboard V4 /costs | 🟡 prototype готов, integration pending |
| 12 | Pre-commit hook | ✅ 96d0c17 |

## Priority 11 — что дальше

Файлы готовы:
- `src/web/prototypes/costs_v4_claude_design.html` — vanilla JS, 1131 lines (Option A)
- `src/web/prototypes/costs_v4_shadcn.html` — React partial (Option B)
- `docs/CLAUDE_DESIGN_BRIEF_COSTS_V4.md` — spec

URL: `http://127.0.0.1:8080/prototypes/costs_v4_claude_design`

**Следующие шаги (использовать Design plugin v1.2.0):**
1. `/design-critique` на `costs_v4_claude_design.html` — structured feedback
2. `/design-handoff` — pixel-perfect spec
3. Screenshot через playwright (`mcp__playwright__browser_navigate` + `_take_screenshot`) когда load < 50
4. Применить feedback, итерировать
5. Интегрировать как route `/costs` в `src/modules/web_app.py` (заменить/дополнить legacy)

## Priority 8 — Memory Phase 2

Owner делает новый Telegram export без media/public channels (split по годам).
Когда получим путь — запустить:
```bash
venv/bin/python scripts/bootstrap_memory.py \
  --export "<path>/result.json" \
  --db ~/.openclaw/krab_memory/archive.db \
  --whitelist ~/.openclaw/krab_memory/whitelist.json \
  --verbose
```
Baseline уже есть: `DataExport_2026-04-19/result_fixed.json` (470 МБ, патчен для обрыва в left_chats).

## System state at handoff

- Load average 416 (abnormal) — после restart должно упасть
- Memory 15 GB / 36 GB used
- Disk 88% used, 116 GB avail
- Krab PID 972 alive на :8080 (но HTTP lagged из-за load)

## Design plugin v1.2.0 (новое!)

После рестарта доступны skills:
- `/design-critique` — structured design feedback
- `/design-handoff` — dev spec generation
- `/design-system` — design system audit
- `/accessibility-review` — WCAG 2.1 AA audit
- `/research-synthesis` — user research synthesis
- `/user-research` — user research planning

## Carry-forward

- Russian communication
- Sonnet agents default, Opus для архитектуры
- **fix root cause, not symptoms**
- Memory monitor: если load > 100, глушить background agents
- Max 3 parallel sonnet agents одновременно (чтобы не положить систему)

## Session-15-part-2 старт-команда

Скажи что-то вроде: "продолжаем Session 15 после рестарта. Прочитай
.remember/session_15_handoff_midway.md + проверь load упал ли, запусти
/design-critique на src/web/prototypes/costs_v4_claude_design.html и покажи
screenshot через playwright."
