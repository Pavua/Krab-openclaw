# Быстрый старт для нового чата — актуально 01.04.2026

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
| `docs/handoff/OPENCLAW_INCIDENT_2026-04-01.md` | Апрельский incident recovery: `codex-cli`, browser drift, Telegram delivery |
| `agent_skills/<skill>/SKILL.md` | Конкретный krab-агент |

---

## Что сделано в сессии 31.03–01.04.2026

### Фиксы агентного режима OpenClaw (в main, коммит 0e94030)
- **`no_tool_activity_timeout`**: убрано условие `not received_any_chunk` — таймаут
  теперь срабатывает только ПОСЛЕ первого чанка. До него — `first_chunk_timeout_sec`.
  OpenClaw в агентном режиме буферизует tool-вызовы внутри своей цепочки и шлёт
  первый chunk только по завершении loop'а.
- **`first_chunk_timeout_sec`**: 600s → **1800s** (30 мин) для текстовых задач.
  Аналог для фото: 720s → 1200s (20 мин). Покрывает длинные agentic loop'ы.
- **Progress notice**: 3 фазы — 1й notice, 2-3й (агентный режим), 4+ (ссылка на дашборд :18789)
- **❌ сообщение улучшено**: включает имя модели + совет `!reset`

### Фиксы PNG auto-inject (в main, коммит 594f9b2)
- Валидация PNG magic bytes + мин. размер 1KB перед auto-inject из `/tmp/`
- Fallback `send_photo` → `send_document` при `IMAGE_PROCESS_FAILED`

### Из прошлой сессии (30.03.2026)
- **OpenClaw v2026.3.28 scope header** — `src/openclaw_client.py` ✓
- **49 agent skills** в `agent_skills/` ✓
- **Telegram MCP**: `krab-telegram` (yung_nagato) + `krab-telegram-p0lrd` (p0lrd) ✓

### Cleanup (01.04.2026)
- `krab-telegram-test` удалён из `~/.codex/config.toml` — был дубликатом p0lrd
- `MCP_DOCKER` удалён из `~/.codex/config.toml`
- `context7@claude-plugins-official` → false в `~/.claude/settings.json`

### Addendum 01.04.2026 21:30+ — incident recovery после апдейта OpenClaw
- Исправлен drift runtime registry для `codex-cli/gpt-5.4`:
  `scripts/openclaw_model_registry_sync.py` теперь досеивает provider-shape (`baseUrl`, `api`) для alias-провайдеров.
- Исправлена cloud retry-логика в `src/openclaw_client.py`:
  после `provider_timeout` / `provider_error` клиент идёт дальше по runtime fallback chain,
  а не сдаётся после первого cloud fallback.
- Live-подтверждение после последовательного restart gateway:
  два прямых запроса к `http://127.0.0.1:18789/v1/chat/completions`
  вернули `200 OK` и ответы `OK-CODEX` / `OK-CODEX-2`.
- Browser incident пока не закрыт полностью:
  workspace truth всё ещё расходится между `9223`, legacy `9222` и OpenClaw browser `18800`.
- Детальный разбор и остаточные риски вынесены в
  `docs/handoff/OPENCLAW_INCIDENT_2026-04-01.md`.

### Addendum 01.04.2026 поздний browser fix-pass
- Ветка helper-worktree: `codex/owner-browser-passive-truth`
- `src/integrations/browser_bridge.py`:
  bridge теперь читает runtime CDP truth из `mcporter.json` / `remote_debugging_port.txt`
  и больше не подмешивает legacy `9222`, если runtime уже объявил другой endpoint.
- `src/modules/web_app.py`:
  owner Chrome readiness переведён на изолированный ordinary-contour probe
  + неинвазивный `passive_probe()` вместо `action_probe()`.
- `capability_registry` / `runtime_handoff`:
  system-control browser truth теперь тоже собирается через owner Chrome probe,
  а не через runtime singleton bridge.
- Truthful overall:
  `/api/openclaw/browser-mcp-readiness` теперь учитывает и `owner_chrome.readiness`,
  чтобы зелёный debug browser не маскировал сломанный ordinary Chrome.
- Проверка:
  таргетные unit: `148 passed`, `2 failed` baseline-only;
  временный UI новой ветки на `:18081` открылся в браузере;
  после `Синхронизировать данные` owner Chrome tab-list не изменился (`9222` = 3 page-target, `9223` = 0).
  Дополнительный таргетный набор на owner/browser registry layer: `4 passed`.

---

## Текущее состояние (01.04.2026)

```
main = 0e94030
```

### MCP Claude Code (должны быть ✓)
```
plugin:github   openclaw-browser
krab-telegram   krab-telegram-p0lrd
```
⚠️ `krab-telegram-test` удалён из `.codex/config.toml` — вступит в силу после рестарта Claude Code

### Config файлы изменены (вне git)
- `~/.codex/config.toml` — убраны MCP_DOCKER, krab-telegram-test
- `~/.claude/settings.json` — context7 plugin disabled

### Stale-lock quick fix (если krab-telegram упал)
```bash
kill $(lsof -t ~/.krab_mcp_sessions/kraab_cc_mcp.session 2>/dev/null) 2>/dev/null
kill $(lsof -t ~/.krab_mcp_sessions/p0lrd_cc_mcp.session 2>/dev/null) 2>/dev/null
```

---

## Открытые вопросы / следующая сессия

| Задача | Статус |
|---|---|
| Chrome extension OpenClaw "Off" — расследовать | Medium |
| Browser/CDP drift `9223` vs `9222` vs `18800` — довести до одного source-of-truth | High, но owner-readiness side effect уже ослаблен |
| Mercadona навигация — поиск не работает в UI | Medium |
| iMessage фильтрация — пропускает ненужное | Medium |
| `parallel mode` (4 агента / 8 субагентов) — включить/тестировать | Low |
| Ответ в Telegram когда OpenClaw ответил после ❌ | Future |
| Переводчик (продвинули в другом диалоге) | Low |

## Важно: параллельный режим OpenClaw
Кнопка "4 агента / 8 субагентов" в панели `:8080` — это многопользовательский
режим (обрабатывать несколько чатов одновременно), а НЕ ускорение одного ответа.
Для одного запроса скорость = скорость модели. Полезно если несколько пользователей.

---

## Копипаст для нового чата

> Продолжаем работу с Краб / OpenClaw. Ветка: main (0e94030).
> Прошлая сессия: фиксы агентного режима (first_chunk_timeout 30мин, no_tool_activity_timeout только post-first-chunk), PNG auto-inject, прогресс-нотисы, cleanup MCP.
> Файлы: CLAUDE.md + docs/handoff/SESSION_HANDOFF.md + docs/handoff/QUICK_START_NEXT_SESSION.md
