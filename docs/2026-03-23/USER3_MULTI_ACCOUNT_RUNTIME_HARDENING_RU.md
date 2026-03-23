# USER3 Multi-Account Runtime Hardening

Дата: 2026-03-23

## Зачем нужен этот документ

Этот срез фиксирует доработки, которые были подтверждены в `USER3`-контуре во время работы
с shared-копией Краба, и отделяет:

- что уже надёжно работает в writable-клоне `USER3`;
- что нужно перенести обратно в боевой контур `pablito`;
- что является именно multi-account hardening и не обязательно для single-account запуска.

## Подтверждённые исправления

### 1. Критичный crash на импорте `handle_shop` устранён

- Проблема: при старте Краб падал с `ImportError: cannot import name 'handle_shop' from 'src.handlers'`.
- Причина: `src/handlers/__init__.py` не реэкспортировал `handle_shop`, хотя `userbot_bridge.py` его импортирует.
- Исправление:
  - добавлен реэкспорт `handle_shop` в `src/handlers/__init__.py`;
  - добавлен regression-тест `tests/unit/test_handlers_exports.py`.
- Проверка:
  - `pytest tests/unit/test_handlers_exports.py -q` — OK;
  - `from src.userbot_bridge import KraabUserbot` — OK;
  - live-start launcher-а больше не падает на этом импорте.

### 2. Логи launcher/watchdog/proxy переведены в per-account runtime-state

- Проблема: shared `/tmp/claude_proxy.log` и `/tmp/krab_session_watchdog.log` приводили к `Permission denied`
  при переключении между macOS-учётками.
- Исправление:
  - launcher пишет в `~/.openclaw/krab_runtime_state/claude_proxy.log`;
  - launcher пишет в `~/.openclaw/krab_runtime_state/krab_session_watchdog.log`;
  - `scripts/telegram_session_watchdog.py` научен брать лог-путь из `KRAB_RUNTIME_STATE_DIR`.
- Проверка:
  - после smoke-старта исчезли ошибки на `/tmp/*`;
  - файлы логов создаются в per-account runtime-state;
  - `pytest tests/unit/test_telegram_session_watchdog.py -q` — OK.

### 3. Убран ложный alarm по OpenClaw gateway в LaunchAgent-режиме

- Проблема: launcher иногда считал, что gateway на `:18789` не поднялся, хотя health уже был `ok`.
- Причина: `lsof` не всегда успевал корректно показать listener, и launcher делал лишний `launchctl kickstart`.
- Исправление в launcher:
  - `is_gateway_listening()` теперь умеет fallback на прямой TCP connect;
  - добавлен `wait_gateway_ready()` с совместной проверкой listener + health;
  - success-path больше не печатает ложный fail, если health догнался с задержкой.
- Проверка:
  - live-start печатает `OpenClaw gateway уже слушает 18789, повторный старт не требуется`;
  - после фикса исчезла лишняя churn-активность в `gateway.err.log`.

### 4. Krab Voice Gateway теперь поднимается в USER3 даже при shared repo

- Проблема: `scripts/start_gateway.command` из symlink-каталога `Krab Voice Gateway`
  пытался писать `.gateway.pid` и `gateway.log` прямо в каталог `pablito`, поэтому в `USER3`
  падал с `permission denied`.
- Исправление в launcher:
  - если штатный Voice Gateway launcher не стартует, включается per-account fallback;
  - fallback поднимает Uvicorn напрямую из кода `Krab Voice Gateway`;
  - приватный venv, pid и log живут в `~/.openclaw/krab_runtime_state/voice_gateway`.
- Проверка:
  - live-start дошёл до `✅ Krab Voice Gateway слушает порт 8090 и проходит health-check.`;
  - `curl http://127.0.0.1:8090/health` вернул `{"ok":true,...}`;
  - созданы:
    - `~/.openclaw/krab_runtime_state/voice_gateway/.venv_krab_voice_gateway`
  - `~/.openclaw/krab_runtime_state/voice_gateway/gateway.log`
  - `~/.openclaw/krab_runtime_state/voice_gateway/gateway.pid`

### 5. Repo-level one-click entrypoints снова рабочие

- Проблема:
  - `start_krab.command` и `Stop Krab.command` внутри репо жёстко ссылались на
    `new ...`-скрипты в самом репо, которых там не было;
  - `Start Full Ecosystem.command` тоже вызывал отсутствующий `new start_krab.command`;
  - `Stop Full Ecosystem.command` вызывал отсутствующий `new Stop Krab.command`;
  - `Start Voice Gateway.command` был неисполняемым (`chmod` drift).
- Исправление:
  - repo-level wrapper-ы теперь сначала ищут repo-local `new ...`, затем sibling launcher уровнем выше;
  - `Start Full Ecosystem.command` делегирует в `start_krab.command`;
  - `Stop Full Ecosystem.command` делегирует в `Stop Krab.command`;
  - `Start Voice Gateway.command` снова executable.
- Проверка:
  - `bash -n` для всех обновлённых `.command` — OK;
  - `Start Voice Gateway.command` поднимает `:8090` и проходит health-check;
  - `start_krab.command` живым запуском дошёл до `kraab_running`;
  - `Stop Krab.command` корректно завершил userbot/proxy/voice;
  - `Start Full Ecosystem.command` + `Stop Full Ecosystem.command` прошли end-to-end цикл без missing-file ошибок.

## Что нужно перенести обратно на `pablito`

### Обязательно

- launcher hardening для `:18789`:
  - export `KRAB_RUNTIME_STATE_DIR`;
  - per-account log paths для Claude Proxy и watchdog;
  - `wait_gateway_ready()` вместо раздельных слабых проверок;
  - TCP fallback в `is_gateway_listening()`.

### По ситуации

- Voice Gateway per-account fallback.

Пояснение:
для single-account запуска под `pablito` штатный `scripts/start_gateway.command` уже может быть достаточным,
потому что `pablito` владеет своей voice-gateway директорией. Но если хотим, чтобы launcher одинаково хорошо
работал при межаккаунтном switchover, fallback стоит перенести и туда тоже.

## Что является именно USER3-специфичным

- symlink-контур на соседние каталоги `Krab Ear` и `Krab Voice Gateway`;
- необходимость обходить permission boundary между `USER3` и `pablito`;
- приватный Voice Gateway fallback как защита от shared-dir ownership drift.

## Рекомендации на следующий шаг

1. Перенести launcher hardening из `USER3` обратно в `/Users/pablito/Antigravity_AGENTS/new start_krab.command`.
2. При желании перенести те же wrapper-fix'ы и в shared/pablito repo-level `.command`, если там ещё есть старые ссылки на отсутствующие `new ...`.
3. Решить, нужен ли такой же fallback для `new Stop Krab.command`, если хотим мягко останавливать per-account Voice Gateway.
4. После возврата на `pablito` прогнать короткий smoke:
   - `:8090/health`
   - `:18789/health`
   - owner panel `:8080`
   - import/boot `KraabUserbot`
5. После smoke обновить roadmap/handoff уже с единым вердиктом по `pablito` и `USER3`.
