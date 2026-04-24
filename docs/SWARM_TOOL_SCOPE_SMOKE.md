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

### `coders` (whitelist = 19 + base)
- **allowed:** `web_search`, `*__krab_memory_search`,
  `*__krab_run_tests`, `*__krab_tail_logs`, `yung-nagato__krab_status`,
  `yung-nagato__krab_restart_gateway`, `claude_cli`, `codex`, `gemini`,
  `filesystem__fs_read_file`, `filesystem__fs_search`, `filesystem__fs_list_dir`,
  `git__git_status`, `git__git_log`, `git__git_diff`, `system_info`,
  `http_fetch`, `time_now`, `time_parse`, `db_query` (MCP commit `aa7cf30`)
- **blocked:** `*__telegram_*`, `peekaboo`, `tor_fetch`

### `analysts` (whitelist = 11 + base)
- **allowed:** `web_search`, `tor_fetch`, `peekaboo`,
  `*__krab_memory_search`, `yung-nagato__krab_memory_stats`,
  `yung-nagato__telegram_search`, `yung-nagato__telegram_get_chat_history`,
  `filesystem__fs_read_file`, `filesystem__fs_search`, `db_query`, `http_fetch`
  (MCP commit `aa7cf30`)
- **blocked:** `git__*`, `filesystem__fs_list_dir`, `*__krab_run_tests`,
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
| coders | `fs_*`, `git_*` | **ДА** (после MCP aa7cf30) | coders и analysts расширены `fs_read_file`, `fs_search`, `fs_list_dir`, `git_status`, `git_log`, `git_diff`, `system_info`, `http_fetch`, `time_now/parse`, `db_query`. Фильтр по базовому имени — работает независимо от server-префикса. |
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
