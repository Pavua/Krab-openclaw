# Handoff для новой сессии

## State (2026-04-06, session batch 9)
Коммиты на `main` за эту сессию:
- `0cad624` — fix(logger): write structlog output to runtime log file
- `3ba6d93` — refactor(venv): prefer unified venv/ over legacy .venv/ in all paths
- `75d9bba` — fix(userbot): safe _stop_swarm_team_clients when not initialized

## Итоги сессии batch 9 (продолжение batch 7-8)

### ✅ Warm-up team clients ВЕРИФИЦИРОВАН В RUNTIME
Фикс из коммита `6306885` работает: после рестарта в `krab_main.log`
видны все 4 события для каждой команды:
```
swarm_team_client_started (traders/coders/analysts/creative)
swarm_team_client_warmed_up (все 4)
swarm_team_client_bound (все 4)
swarm_team_clients_ready
```
`get_dialogs(limit=50)` прогревает peer cache для каждого team client,
CHAT_ID_INVALID больше не возникает.

### ✅ Signal gateway — угашен
Два LaunchAgent висели в infinite restart loop:
- `ai.openclaw.signal-cli` — телефон `+34603834299` не зарегистрирован
  в Signal, daemon падал с exit 1, накопил 2.1MB err лога
- `ai.krab.signal-ops-guard` — ссылался на удалённый
  `scripts/signal_ops_guard.py`, exit 2 каждую минуту

Действия: `launchctl bootout gui/$UID/ai.openclaw.signal-cli`
и `ai.krab.signal-ops-guard`. Plist оставлены для восстановления
когда/если будет зарегистрирован Signal-аккаунт.

Logs очищены: `signal-daemon.err.log`, `signal-ops-guard.*.log`.

### ✅ krab_main.log пишется снова
Root cause: `src/core/logger.py` использовал `structlog.PrintLoggerFactory`,
писал только в stdout. Старый launcher делал `tee -a krab_main.log`,
новый `new start_krab.command` — нет. Все runtime логи уходили в pipe
wrapper'а и терялись.

Фикс: `structlog.stdlib.LoggerFactory` + stdlib handlers (stdout +
FileHandler на `krab_main.log`). Путь через env:
- `KRAB_LOG_FILE=...` — переопределение
- `KRAB_LOG_FILE=""` / `none` — отключить файл
- Default: `$KRAB_RUNTIME_STATE_DIR/krab_main.log` или
  `~/.openclaw/krab_runtime_state/krab_main.log`

### ✅ Venv унификация добита
Из batch 7 остались хардкоды `.venv/bin/python` в 5 местах:
- `mcp-servers/telegram/server.py:613` (krab_run_tests MCP)
- `src/core/mcp_registry.py:64` (LM Studio launcher)
- `src/core/translator_finish_gate.py:30` (python bin picker)
- `src/modules/web_app.py:2855, 11039, 11097` (subprocess launchers)

Все пути теперь резолвятся в порядке `venv → .venv → fallback`.
После рестарта MCP серверов warning от pytest указывает на
`venv/lib/python3.13/site-packages/pyrogram/sync.py:33` — значит единый
venv реально используется везде.

`.venv/` (1.5GB, Python 3.12 anaconda, old pyrogram) переименован в
`.venv.OLD_DELETE_ME_SAFELY`. Физически не удалён — пусть полежит
сутки, потом можно `rm -rf /Users/pablito/Antigravity_AGENTS/Краб/.venv.OLD_DELETE_ME_SAFELY`.

### ✅ primary модель: gemini-3.1-pro-preview → gemini-pro-latest
Gemini 3.1 pro-preview стабильно возвращал "An unknown error occurred"
(retry loop в gateway.err.log с 2026-04-06 02:02 — уже сутки).
После рестарта Краб грелся на старом primary и swarm commands уходили
в retry бесконечно.

Фикс: `~/.openclaw/openclaw.json.agents.defaults.model`:
```
OLD primary: google/gemini-3.1-pro-preview
NEW primary: google/gemini-pro-latest
fallbacks: gemini-2.5-pro-preview-06-05 → gemini-3-pro-preview →
           gemini-3-flash-preview → gemini-2.5-pro → gemini-flash-latest
           → gemini-3.1-pro-preview (в конце, как последний фолбек)
```
Бэкап: `~/.openclaw/openclaw.json.bak_pre_gemini_swap_20260406_234544`

Этот конфиг НЕ в git (runtime state). Если нужно откатить — `cp` бэкап.

### ✅ Bugfix: _stop_swarm_team_clients AttributeError
Регрессия из `876f3a5`: метод обращался к `self._swarm_team_clients`
без `getattr`. Test fixture, вызывающий `stop()` без `start()`, падал
`AttributeError`. Фикс: `getattr(self, "_swarm_team_clients", None)`
с early return если пусто. Test `test_stop_awaits_background_tasks_...`
теперь зелёный.

## Нерешённое (следующая сессия)

### ⚠️ Forum topics в swarm_channels.json не конфигурированы
`~/.openclaw/krab_runtime_state/swarm_channels.json`:
```json
{"forum_chat_id": null, "team_topics": {}, "team_chats": {"traders": -100, "coders": -200}}
```
`swarm_channels.configured()` возвращает False → Forum broadcast молча
скипается. Warm-up верифицирован через логи, но полный E2E
(сообщение от @p0lrdp_AI в топик Forum группы) пока не прошёл.

Fix: запустить `!swarm setup` от yung_nagato в группе 🐝 Krab Swarm
(chat_id `-1003703978531`) с включёнными Topics. Либо заполнить
`forum_chat_id` + `team_topics` вручную через Owner panel.

### ⚠️ Pre-existing test failures (не блокер, но раздражает)
44 тестов в `tests/unit/` падают из-за drift между тестами и кодом:
- `test_access_control`: `cap` добавлен в OWNER_ONLY_COMMANDS без обновления теста
- `test_userbot_stream_timeouts`: ожидает "Использую инструмент", код — "Выполняется"
- `test_openclaw_model_registry_sync`: 2 теста по reasoning
- `test_web_app_runtime_endpoints`: 13 translator тестов (config drift)

Все 137 тестов по изменённым/критичным модулям — зелёные.

### ⚠️ Mercadona навигация — блокер user (не мой)
Нужны логи терминала при воспроизведении. Без логов диагностировать нельзя.

## Canonical runtime commands
```bash
# Запуск / стоп Krab (НЕ Restart Krab.command!)
"/Users/pablito/Antigravity_AGENTS/new start_krab.command"
"/Users/pablito/Antigravity_AGENTS/new Stop Krab.command"

# Gateway — через openclaw binary, НЕ SIGHUP
PATH=/opt/homebrew/bin:$PATH openclaw gateway start

# Проверка warm-up в логах
grep swarm_team_client_warmed_up ~/.openclaw/krab_runtime_state/krab_main.log

# Тесты через единый venv
/Users/pablito/Antigravity_AGENTS/Краб/venv/bin/python -m pytest tests/unit/test_swarm_channels.py -q
```

## Team accounts mapping (как было)
| Команда | Username | User ID | Session |
|---------|----------|---------|---------|
| traders | @p0lrdp_AI | 1861168302 | swarm_traders.session |
| coders | @p0lrdp_worldwide | 5929474128 | swarm_coders.session |
| analysts | @hard2boof | 6539946601 | swarm_analysts.session |
| creative | @opiodimeo | 5920778135 | swarm_creative.session |

Конфиг: `~/.openclaw/krab_runtime_state/swarm_team_accounts.json`
Forum группа: 🐝 Krab Swarm (`-1003703978531`) — в handoff помечена как
готовая, но `swarm_channels.json` этого не знает.

## ACL (напоминание)
OWNER = **yung_nagato** (userbot, session `kraab.session`, id 6435872621).
Оператор **p0lrd** (id 312322764) имеет FULL, но НЕ OWNER.
Команды от p0lrd — в ЛС `@yung_nagato`, не в Saved Messages.
