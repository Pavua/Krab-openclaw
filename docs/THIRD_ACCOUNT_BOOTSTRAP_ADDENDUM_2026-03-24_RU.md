# Third Account Bootstrap Addendum 2026-03-24

Этот addendum фиксирует launcher-правду, подтверждённую после live-проверки
на helper-учётке `USER3`.

## Что изменилось

Для `USER2` / `USER3` больше нельзя предполагать, что текущий repo всегда содержит
локальные `new start_krab.command` и `new Stop Krab.command`.

Актуальный baseline:

- `Start Full Ecosystem.command` ищет рабочий Krab start-launcher по цепочке:
  `repo new` -> `/Users/$USER/Antigravity_AGENTS/new start_krab.command`
  -> legacy fallback;
- `Stop Full Ecosystem.command` ищет stop-launcher по той же схеме;
- `Voice Gateway` хранит `PID` и `gateway.log` в
  `~/.openclaw/krab_runtime_state/voice_gateway`.

## Что проверено

Проверен полный live-цикл:

1. запуск `Start Full Ecosystem.command`
2. достижение `kraab_running`
3. `./venv/bin/python scripts/r20_merge_gate.py` -> `ok: true`
4. `Stop Full Ecosystem.command`
5. подтверждение, что `:8080` и `:8090` больше не слушают

## Быстрая preflight-проверка для новой учётки

```bash
ls -l "/Users/$USER/Antigravity_AGENTS/new start_krab.command" \
      "/Users/$USER/Antigravity_AGENTS/new Stop Krab.command"
```

Если оба файла существуют и исполняемы, helper-учётка сможет использовать
shared repo без привязки к `pablito` как единственному владельцу launcher-слоя.
