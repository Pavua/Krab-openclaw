# Swarm Team Tool-Per-Team Scoping — Implementation Plan

**Status:** Design complete, pending implementation.
**Created:** 2026-04-24, Session 19+
**Agent research:** general-purpose (sonnet), ~77s, 9 tool uses.

## 1. Финальный whitelist

Базовый набор для всех (минимум): `web_search`, `krab_memory_search`.

**traders** (рынок + риск):
- `web_search` (Coingecko/TradingView/DefiLlama/Investing)
- `krab_memory_search`, `krab_memory_stats`
- `tor_fetch` (если `TOR_ENABLED`)
- *(future)* `crypto_price`, `crypto_orderbook`

**analysts** (исследование):
- `web_search`, `tor_fetch`
- `krab_memory_search`, `krab_memory_stats`
- `telegram_search`, `telegram_get_chat_history` (OSINT)
- `peekaboo` (скриншот источника)

**coders** (разработка):
- `krab_run_tests`, `krab_tail_logs`, `krab_status`, `krab_restart_gateway`
- `krab_memory_search`
- `web_search` (доки PyPI/SO/MDN)
- CLI: `claude_cli`, `codex`, `gemini` (если зарегистрированы как tools)

**creative** (контент):
- `web_search`
- `krab_memory_search`
- `telegram_send_message`, `telegram_edit_message` (публикация)
- TTS/img-gen — когда станут MCP-tools

**default** (fallback): пустой whitelist → все tools (backward-compat).

## 2. Точки hook-а

Реальный manifest формируется в **`src/openclaw_client.py:2023`**:
```python
tools = [] if _dt else await mcp_manager.get_tool_manifest()
```

Текущий `swarm_tool_scope.py` влияет только на prompt-hint — **LLM получает полный manifest**, это и есть утечка. Hook нужен на уровне actual manifest.

### Минимально-инвазивная архитектура:

1. Расширить `_AgentRoomRouterAdapter.__init__` (`src/handlers/command_handlers.py:347`) параметром `team_name: str | None`.
2. В `route_query` (строка 351) перед вызовом `send_message_stream` выставить `contextvars.ContextVar`, напр. `_swarm_team_ctx.set(team_name)`.
3. В `_openclaw_completion_once` (`openclaw_client.py:2023`) сразу после `tools = await mcp_manager.get_tool_manifest()` добавить:
   ```python
   team = _swarm_team_ctx.get(None)
   if team:
       tools = filter_tools_for_team(tools, team)
   ```
4. Реализовать `filter_tools_for_team(manifest, team)` в **новом модуле** `src/core/swarm_tool_allowlist.py`.
5. В `command_handlers.py:1112, 1190, 4299` и `userbot_bridge.py:1696` — пробросить `team_name` в адаптер.
6. Сбросить контекст в `finally` после стрима (иначе протекает на не-swarm запросы).

**Почему ContextVar:** `send_message_stream` уже имеет 8+ kwargs, `_openclaw_completion_once` вызывается из retry-loop. ContextVar пробрасывается через async без правки сигнатур.

## 3. Структура данных

```python
TEAM_TOOL_ALLOWLIST: dict[str, frozenset[str]] = {
    "traders":  frozenset({"web_search", "krab_memory_search", ...}),
    "analysts": frozenset({...}),
    "coders":   frozenset({...}),
    "creative": frozenset({...}),
}
```

- `dict` — lookup по имени команды
- `frozenset[str]` — `O(1)` `in`-check на каждый tool, иммутабельность
- НЕ `Enum` — MCP-tools регистрируются динамически
- Резолвить алиасы через `resolve_team_name()` из `swarm_bus.py` ДО lookup

## 4. Edge-case: запрещённый tool

**Рекомендация: silent strip + WARN log.**

- LLM не знает что tool запрещён (его нет в manifest) → запросить может только галлюцинацией
- Hard-fail убивает раунд → плохой UX, ломает многошаговые цепочки
- В `mcp_client.call_tool_unified` добавить guard: если `_swarm_team_ctx` активен и tool не в allowlist → `{"error": "tool_not_allowed_for_team", "team": ..., "tool": ...}`
- Метрика: `krab_swarm_tool_blocked_total{team, tool}` — для тюнинга allowlist

**Исключение для hard-fail:** owner-only/destructive tools (`krab_restart_gateway`, `telegram_send_message` для не-creative) — вообще не попадают в manifest никакой команды кроме явно разрешённой.

## 5. Риски

- **`tests/unit/test_swarm_*`** — должны пройти (manifest мокается)
- **`test_openclaw_client.py`** — если тест "manifest passed-through unchanged" → update
- **`swarm_research_pipeline.py`** — использует тот же router, пробросить team (research = analysts)
- **`swarm_team_listener.py`** — автономные DM-команды: забыть пробросить team → silent fallback на full manifest
- **Markdown/JSON tools** (`web_fetch`, `read_file`) — `_BASE_ALLOWLIST` (memory_search + web_search) и объединять с per-team
- **Backward-compat:** `team_name` пустая → `tools` как есть; защита userbot-команд (`!ask`, `!search`)
- **Performance:** filter O(n) по 20-30 tools, незаметно

## 6. Тесты

Файл: `tests/unit/test_swarm_tool_allowlist.py`

1. **`test_filter_keeps_only_allowed_tools`** — manifest 5 fake-tools, team=traders → только web_search/krab_memory_search
2. **`test_unknown_team_returns_full_manifest`** — team="unknown" → input == output (backward-compat)
3. **`test_alias_resolution`** — team="трейдеры" → фильтрация как для "traders"
4. **(опционально)** integration — мок `_swarm_team_ctx`, вызов `_openclaw_completion_once`, проверка filter

## Key files

- `src/core/swarm_tool_scope.py` — существует, prompt-only (не трогать)
- `src/core/swarm_bus.py:32` — TEAM_REGISTRY, alias resolver
- `src/handlers/command_handlers.py:337` — адаптер, точка проброса team_name
- `src/openclaw_client.py:2023` — где tools попадают в payload (**главный hook**)
- `src/mcp_client.py:193` — `get_tool_manifest` (не трогать, фильтр снаружи)

## Estimated complexity

- ~150 LOC production + ~80 LOC tests
- 1 новый модуль (`swarm_tool_allowlist.py`)
- 4 файла правок (адаптер, openclaw_client, mcp_client guard, swarm_team_listener)
- 1 ruff pass + pytest run
