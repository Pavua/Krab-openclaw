# Быстрый старт для нового чата — актуально 31.03.2026

## Минимальный набор файлов для нового чата

```
CLAUDE.md
docs/handoff/SESSION_HANDOFF.md
docs/handoff/QUICK_START_NEXT_SESSION.md
```

Добавляй только если тема сессии касается этого:

| Файл | Когда нужен |
|------|-------------|
| `src/openclaw_client.py` | OpenClaw HTTP-клиент, scope-header fix |
| `src/userbot_bridge.py` | MEDIA:/flow/transport |
| `src/core/access_control.py` | Права команд |
| `~/.openclaw/openclaw.json` | Routing/провайдеры |
| `docs/handoff/PROVIDER_STATUS.md` | Диагностика провайдеров |
| `agent_skills/<skill>/SKILL.md` | Конкретный krab-агент |

---

## Что сделано в сессии 30–31.03.2026

### Критический фикс (в main)
- **OpenClaw v2026.3.28 scope header** — `src/openclaw_client.py`:
  - Добавлен `x-openclaw-scopes: operator.write,operator.read` в default headers
  - Добавлен `_sync_token_from_runtime_on_init()` — защита от doctor --fix ротации
  - Подтверждено: status=200 в логах Краба (23:06 и 23:13, 30.03.2026)

### Agent skills (в main, коммит f1dd4dc)
- 32 USER2-скилла перенесены в `agent_skills/`
- `~/.claude/krab-agents/krab_agents.json` = **49 агентов** (было 17)
- `sync_krab_agent_skills.py --profile full --claude-only` регенерирует их

### Telegram MCP для Claude Code
- `krab-telegram` (yung_nagato): сессия `kraab_cc_mcp.session`, user-scope
- `krab-telegram-p0lrd` (p0lrd): сессия `p0lrd_cc_mcp.session`, user-scope
- Сессии в `~/.krab_mcp_sessions/` — аутентифицированы, не нужно повторять
- Stale-lock фикс: `run_telegram_mcp_account.py` убивает зависшие PIDs автоматически

### Cleanup
- Удалён `MCP_DOCKER`, отключены `learning-output-style` и `code-simplifier`

---

## Текущее состояние (31.03.2026)

```
main = f1dd4dc
```

### MCP Claude Code (все должны быть ✓)
```
plugin:context7   plugin:github   openclaw-browser
krab-telegram     krab-telegram-p0lrd
```

### Stale-lock quick fix (если один из krab-telegram упал)
```bash
kill $(lsof -t ~/.krab_mcp_sessions/kraab_cc_mcp.session 2>/dev/null) 2>/dev/null
kill $(lsof -t ~/.krab_mcp_sessions/p0lrd_cc_mcp.session 2>/dev/null) 2>/dev/null
```

---

## Бэклог

| Задача | Приоритет |
|---|---|
| Mercadona навигация — поиск не работает в UI | Medium |
| iMessage фильтрация — пропускает ненужное | Medium |
| openclaw_no_tool_activity_timeout 5мин | Medium |
| Call translator — voice-first не в daily-use | Low |

---

## Копипаст для нового чата

> Продолжаем работу с Краб / OpenClaw. Ветка: main (f1dd4dc).
> Прошлая сессия: OpenClaw scope-header fix, 49 krab skills, Telegram MCP для Claude Code.
> Файлы: CLAUDE.md + docs/handoff/SESSION_HANDOFF.md + docs/handoff/QUICK_START_NEXT_SESSION.md
