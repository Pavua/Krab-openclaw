# Промпт для новой сессии Краба (после batch 9, 2026-04-07)

Скопируй текст ниже в новую сессию Claude Code:

---

Привет. Продолжаем работу над Крабом после batch 9 (4 коммита на `main`:
`0cad624`, `3ba6d93`, `75d9bba`, `05a6a1c`). Полный handoff —
`.remember/remember.md`.

## Текущий runtime (на момент завершения batch 9)
- Krab up, primary model = `google/gemini-pro-latest` (gemini-3.1-pro-preview
  свапнут из-за loop "unknown error", бэкап:
  `~/.openclaw/openclaw.json.bak_pre_gemini_swap_20260406_234544`)
- Все 4 swarm team clients прогреты (`swarm_team_client_warmed_up` в
  `~/.openclaw/krab_runtime_state/krab_main.log` после рестарта)
- `krab_main.log` снова пишется (structlog file handler починен)
- Единый `venv/` (Py 3.13, pyrofork 2.3.69) везде; `.venv/` переименован
  в `.venv.OLD_DELETE_ME_SAFELY` (1.5GB, можно `rm -rf` если всё ок)
- Signal LaunchAgent угашены (`ai.openclaw.signal-cli`,
  `ai.krab.signal-ops-guard`)

## Приоритет 1: Forum topics для Swarm
**Главный недоделанный E2E.** `swarm_channels.json` пуст:
```json
{"forum_chat_id": null, "team_topics": {}, "team_chats": {"traders": -100, "coders": -200}}
```
→ broadcast в Forum молча скипается, warm-up верифицирован только через
логи, сообщения от `@p0lrdp_AI` в топиках Forum группы пока не появлялись.

Что нужно сделать:
1. В группе **🐝 Krab Swarm** (`-1003703978531`) проверить что Topics
   включены
2. Запустить `!swarm setup` от yung_nagato в самой группе (НЕ в ЛС) —
   это должно создать топики и заполнить `swarm_channels.json`
3. После этого `!swarm traders <topic>` от p0lrd → @yung_nagato должен
   привести к broadcast от `@p0lrdp_AI` в топик `traders` Forum группы

Альтернатива: заполнить `forum_chat_id` + `team_topics` вручную через
Owner panel `:8080` или прямой edit JSON.

## Приоритет 2: Pre-existing test failures (cleanup)
44 теста падают из-за drift между тестами и кодом. Не блокеры, но
портят сигнал. Низковисящие фрукты:
- `test_access_control::test_full_profile_cannot_execute_owner_only_commands`
  — добавить `cap` в expected set
- `test_userbot_stream_timeouts::test_build_openclaw_progress_wait_notice_mentions_running_tool`
  — обновить assert ("Использую инструмент" → "Выполняется")
- `test_openclaw_model_registry_sync` — 2 теста по reasoning logic
- `test_web_app_runtime_endpoints` — 13 translator тестов

Спросить юзера какие фиксить.

## Приоритет 3: Mercadona навигация
Блокер user — нужны логи терминала при воспроизведении. Без логов
диагностировать нельзя.

## Приоритет 4: Удалить .venv.OLD_DELETE_ME_SAFELY
Если runtime стабилен ≥1 сутки на едином `venv/`:
```bash
rm -rf /Users/pablito/Antigravity_AGENTS/Краб/.venv.OLD_DELETE_ME_SAFELY
```

## Канонические команды
```bash
# Запуск/стоп (НЕ Restart Krab.command!)
"/Users/pablito/Antigravity_AGENTS/new start_krab.command"
"/Users/pablito/Antigravity_AGENTS/new Stop Krab.command"

# Gateway (НЕ SIGHUP)
PATH=/opt/homebrew/bin:$PATH openclaw gateway start

# Логи runtime
tail -f ~/.openclaw/krab_runtime_state/krab_main.log
grep swarm_team_client_warmed_up ~/.openclaw/krab_runtime_state/krab_main.log

# Тесты на едином venv
/Users/pablito/Antigravity_AGENTS/Краб/venv/bin/python -m pytest tests/unit/test_swarm_channels.py -q
```

## ACL напоминание
- OWNER = **yung_nagato** (id 6435872621), session `kraab.session`
- p0lrd (id 312322764) — FULL, но НЕ OWNER
- Команды от p0lrd для теста — в ЛС `@yung_nagato`, не в Saved Messages

Начни с `git log --oneline -8 main`, затем `mcp__krab-p0lrd__krab_status`,
затем спроси что приоритетнее: Forum topics setup (нужна моя помощь
с UI/edit) или test cleanup. Поехали.
