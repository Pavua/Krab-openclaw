# Swarm tool-per-team allowlist — live smoke (Session 21)

**Дата:** 2026-04-25 00:05 CEST
**Worktree:** `.claude/worktrees/zealous-hellman-804688` (branch `claude/zealous-hellman-804688`)
**Базис:** `fix/daily-review-20260421` @ `49a7cc9`
**Проверяемые коммиты:**
- `8d58c5d` — feat(swarm): per-team tool allowlist
- `1268842` — feat(swarm): expand coders+analysts allowlist with fs/git/system/db tools

## TL;DR

- ✅ **Filter logic полностью валиден** на реальной форме manifest (synthetic harness, 9/9 sanity-чеков pass).
- ✅ **Guard `is_tool_allowed` + counter `record_blocked_tool`** отрабатывают по всем 4 командам.
- ✅ Метрика `krab_swarm_tool_blocked_total{team,tool}` корректно зарегистрирована в `prometheus_metrics.py:385-402` (emit только при >0, что верно).
- ⚠️ **Live round не завершён**: Krab core находится в restart-loop из-за `OperationalError('disk I/O error')` на swarm-team session DBs (`data/sessions/swarm_*.session`). Это блокер runtime'а, **не имеет отношения к allowlist коммитам**.

## 1. Trigger

```text
p0lrd → @yung_nagato:  "!swarm coders кратко что такое MMR для vector retrieval (2-3 предложения)"
sent at 2026-04-24T23:56:35
```

История чата: ответа за 8+ минут не пришло. Userbot main client успевал подняться (`telegram_userbot_state=running, connected=True` на ~23:58), но в течение 30-60 секунд после старта `telegram_session_watchdog` дёргает `/api/krab/restart_userbot` (см. ниже).

## 2. Runtime блокер (root cause restart loop)

В логе 81 вызов `restart_userbot_endpoint_called` от `python-httpx/0.28.1` (это `scripts/telegram_session_watchdog.py`, `RESTART_URL = http://127.0.0.1:8080/api/krab/restart_userbot`, см. строка 56).

Причина — повторяющиеся ошибки старта swarm-team клиентов:

```
swarm_team_client_start_failed  error="OperationalError('disk I/O error')"  team=traders
swarm_team_client_start_failed  error="OperationalError('disk I/O error')"  team=coders
swarm_team_client_start_failed  error="OperationalError('disk I/O error')"  team=analysts
swarm_team_client_start_failed  error="OperationalError('disk I/O error')"  team=creative
```

Файлы `data/sessions/swarm_{team}.session` (Pyrofork SQLite) либо повреждены, либо удерживаются другим процессом. WAL-файлы постоянно пересоздаются с размером 0:

```
swarm_traders.session-wal     0 bytes    Apr 25 00:02
swarm_coders.session-wal      0 bytes    Apr 25 00:01
swarm_analysts.session-wal    0 bytes    Apr 25 00:02
swarm_creative.session-wal    0 bytes    Apr 25 00:02
```

`swarm_clients_startup_complete count=4 started=0` — ни один не поднимается.

Health-эндпоинт, читающий swarm-team state, реагирует деградацией → watchdog рестартует userbot → loop. **Это блокер для любых live swarm-сценариев**, рекомендация: Wave 30 — диагностика swarm session DB (vacuum/recreate) и временное отключение свёрм-клиентов в watchdog health-criteria.

## 3. Synthetic smoke (filter logic)

`venv/bin/python scripts/swarm_tool_scope_smoke.py` — manifest 29 tools (yung-nagato + p0lrd + filesystem + git + native).

| Team | Whitelist size | Allowed | Blocked |
|------|----------------|---------|---------|
| traders | 5 | 5 | 24 |
| coders | 11 | 9 | 20 |
| analysts | 12 | 8 | 21 |
| creative | 4 | 6 | 23 |
| unknown_team | passthrough | 29 | 0 |

**Sanity checks 9/9 PASS:**
- traders видит `web_search`, `krab_memory_search` — но НЕ `krab_run_tests`, НЕ `filesystem__read_file`
- coders видит `krab_run_tests` — но НЕ `telegram_send_message`
- analysts видит `telegram_search`
- creative видит `telegram_send_message`
- unknown_team → passthrough (backward-compat)

Лог-сигнал `swarm_tool_manifest_filtered` корректно эмитится для каждой команды:
```
swarm_tool_manifest_filtered  team=coders   kept=9  dropped=20
swarm_tool_manifest_filtered  team=traders  kept=5  dropped=24
swarm_tool_manifest_filtered  team=analysts kept=8  dropped=21
swarm_tool_manifest_filtered  team=creative kept=6  dropped=23
```

## 4. Guard + counter (call_tool_unified silent-strip)

Прямой вызов `is_tool_allowed` + `record_blocked_tool`:

```
traders   → filesystem__write_file:    allowed=False
coders    → filesystem__write_file:    allowed=False
analysts  → krab_restart_gateway:      allowed=False
creative  → filesystem__write_file:    allowed=False

get_blocked_tool_stats() = {
  ('traders',  'filesystem__write_file'): 1,
  ('coders',   'filesystem__write_file'): 1,
  ('analysts', 'krab_restart_gateway'):   1,
  ('creative', 'filesystem__write_file'): 1,
}
```

Prometheus экспозиция (`prometheus_metrics.py:385-402`) корректна — метрика `krab_swarm_tool_blocked_total{team,tool}` появится в `/metrics` сразу после первого реального silent-strip в `mcp_client.call_tool_unified`. На момент теста счётчик = 0 (live round не дошёл до tool-call), поэтому в `/metrics` отсутствует — ожидаемое поведение.

## 5. Verdict

| Проверка | Результат |
|----------|-----------|
| Filter logic корректен | ✅ |
| Per-team whitelists соответствуют ролям | ✅ |
| Backward-compat (unknown team → passthrough) | ✅ |
| Silent-strip guard работает | ✅ |
| Counter инкрементируется | ✅ |
| Prometheus метрика зарегистрирована | ✅ |
| Live `!swarm coders` round | ⚠️ заблокирован swarm-session DB I/O error |
| Per-team rounds (traders/analysts/creative) | ⚠️ тот же блокер |

## 6. Issues found

1. **(P1, не allowlist) Swarm-session DB I/O error** — `data/sessions/swarm_*.session` либо повреждены, либо удерживаются. Лечение: остановить все Krab-процессы, удалить `swarm_*.session*`, дать swarm-listener'у пересоздать. Альтернатива: VACUUM. Watchdog следует ослабить, чтобы он не рестартовал userbot из-за деградации только swarm-клиентов.

2. **(observability) `krab_swarm_tool_blocked_total` приходится "будить"** — Prometheus экспозиция отсутствует пока счётчик = 0. Можно эмитить hint-line `# HELP krab_swarm_tool_blocked_total ...` с empty samples, чтобы dashboard'ы видели метрику сразу. Cosmetic.

## 7. Sample response per team

Не удалось — restart loop. После фикса swarm-session DB рекомендуется повторить тест с p0lrd MCP по 1 short round на team.

---

**Conclusion:** Allowlist commits `8d58c5d` + `1268842` работают корректно (filter + guard + counter). Live e2e перенесён до устранения swarm-session DB issue (Wave 30 candidate).
