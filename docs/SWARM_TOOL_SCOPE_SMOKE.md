# Swarm tool-per-team allowlist — smoke report

Commit под проверкой: `8d58c5d feat(swarm): per-team tool allowlist`
Branch: `fix/daily-review-20260421` (HEAD: `03b9e0d`, `8d58c5d` — ancestor).
Дата: 2026-04-24.
Runner: `scripts/swarm_tool_scope_smoke.py` + `tests/unit/test_swarm_tool_allowlist.py`.

## Запуск

```bash
venv/bin/python scripts/swarm_tool_scope_smoke.py
venv/bin/python -m pytest tests/unit/test_swarm_tool_allowlist.py -q
```

## Что проверено

1. Базовый набор (`_BASE_ALLOWLIST`): `web_search`, `krab_memory_search`.
2. Фильтр `filter_tools_for_team(manifest, team)` на manifest'е с реальной
   формой имён `{server}__{tool}` (yung-nagato, p0lrd, filesystem, git +
   нативные `web_search` / `peekaboo` / `tor_fetch`).
3. ContextVar `_swarm_team_ctx` set/reset per team — leak-check после каждой
   итерации.
4. Backward-compat: `unknown_team` → passthrough (full manifest).

## Live-manifest note

`mcp_manager.get_tool_manifest()` в изолированном smoke-процессе вернул
5 tools (нативные + voice-assistant) — SSE-сессии yung-nagato/p0lrd
инициализируются только в live бот-процессе (`bootstrap/runtime.py`).
Отчёт ниже — на synthetic manifest с 29 tools, точно совпадающим по форме
с тем, что отдают LaunchAgents MCP-серверы.

## Sample output per team (synthetic 29-tool manifest)

### `traders` (whitelist = 4 + base)
- **allowed (5):** `web_search`, `tor_fetch`, `p0lrd__krab_memory_search`,
  `yung-nagato__krab_memory_search`, `yung-nagato__krab_memory_stats`
- **blocked:** все `git__*`, `filesystem__*`, `*__krab_run_tests`,
  `*__krab_tail_logs`, `*__telegram_*`, `peekaboo`, `*__krab_restart_gateway`

### `coders` (whitelist = 9 + base)
- **allowed (9):** `web_search`, `*__krab_memory_search`,
  `*__krab_run_tests`, `*__krab_tail_logs`, `yung-nagato__krab_status`,
  `yung-nagato__krab_restart_gateway`
- **blocked:** все `git__*`, `filesystem__*`, `*__telegram_*`,
  `peekaboo`, `tor_fetch`

### `analysts` (whitelist = 7 + base)
- **allowed (8):** `web_search`, `tor_fetch`, `peekaboo`,
  `*__krab_memory_search`, `yung-nagato__krab_memory_stats`,
  `yung-nagato__telegram_search`, `yung-nagato__telegram_get_chat_history`
- **blocked:** `git__*`, `filesystem__*`, `*__krab_run_tests`,
  `*__krab_tail_logs`, `*__telegram_send_message`, `*__telegram_edit_message`

### `creative` (whitelist = 4 + base)
- **allowed (6):** `web_search`, `*__krab_memory_search`,
  `*__telegram_send_message`, `yung-nagato__telegram_edit_message`
- **blocked:** `git__*`, `filesystem__*`, `*__krab_run_tests`,
  `*__krab_tail_logs`, `peekaboo`, `tor_fetch`, `*__telegram_search`

### `unknown_team` → passthrough
- **allowed (29/29)** — backward-compat OK.

## Verdict

**PASS** — allowlist реально фильтрует manifest на реальной форме имён.
Все 9 санити-проверок зелёные:

- ✅ traders sees web_search
- ✅ traders sees krab_memory_search (any server prefix)
- ✅ traders does NOT see krab_run_tests
- ✅ traders does NOT see filesystem__read_file
- ✅ coders sees krab_run_tests (some server)
- ✅ coders does NOT see telegram_send_message
- ✅ analysts sees telegram_search
- ✅ creative sees telegram_send_message
- ✅ unknown_team passthrough

Unit suite `tests/unit/test_swarm_tool_allowlist.py`: **9/9 PASS**.

## Gap-анализ allowlist'а vs ожиданий задачи

Задание упоминало некоторые инструменты, которых **нет ни в allowlist'е, ни в
реальном manifest'е LaunchAgents MCP** — фиксируем как backlog, не баг:

| Team | Ожидалось | Реально в allowlist | Комментарий |
|------|-----------|---------------------|-------------|
| coders | `fs_*`, `git_*` | НЕТ | filesystem/git MCP на портах 8011/8012 вообще НЕ экспортируют tools с именами `fs_*` или `git_*`; `yung-nagato`/`p0lrd` экспортируют `krab_*` + `telegram_*`. Либо (a) добавить `filesystem__*` / `git__*` в coders allowlist, либо (b) забыть — coders сейчас работают через `!codex`/`!claude_cli` и прямой `krab_run_tests`. |
| creative | `img_*`, `tts_*` | НЕТ | в manifest'е нет `img_generate` / `tts_speak` как MCP-tool'ов — они вызываются через userbot-команды `!img` / `!tts`. LLM свёрма их и так не может дёрнуть. Фикс не нужен, но whitelist стоит документировать в коде. |

Это **не блокер** для commit `8d58c5d` — фильтр работает для тех tools, что
реально присутствуют в manifest'е. Но если в будущем filesystem/git MCP
реально подключат, coders окажутся без fs/git доступа — надо будет расширить
whitelist (отдельный commit).

## Live swarm round — SKIPPED

Krab в момент прогона **не запущен** (`curl :8080/api/v1/health` = connection
refused, `curl :8080/api/uptime` = refused). Поэтому:

- `!swarm coders напиши краткий docstring…` не отправлен;
- счётчик `krab_swarm_tool_blocked_total` не проверен на живых попытках LLM;
- p0lrd MCP send + history verify не выполнены.

**Рекомендация:** после старта Krab (`new start_krab.command`) вручную
выполнить из owner DM:

```
!swarm coders напиши краткий docstring для forward_to_owner.py
```

и проверить `curl :8080/metrics | rg krab_swarm_tool_blocked_total` — ожидается
0 блоков (coders whitelist достаточен для типичной задачи), либо >0 если LLM
попытался вызвать, скажем, `telegram_send_message` для ответа — это будет
признаком, что надо расширить creative/coders.

## Файлы

- `scripts/swarm_tool_scope_smoke.py` — smoke-раннер (новый).
- `docs/SWARM_TOOL_SCOPE_SMOKE.md` — этот отчёт.
- Модуль под проверкой: `src/core/swarm_tool_allowlist.py`.
- Guard в runtime: `src/mcp_client.py:292-316` (`call_tool_unified`).
- Unit: `tests/unit/test_swarm_tool_allowlist.py`.
