# Pablito Launcher Backport 2026-03-23 RU

Этот файл фиксирует, что именно нужно перенести обратно в
`/Users/pablito/Antigravity_AGENTS/new start_krab.command` из USER3-контура.

## Цель

Сделать launcher устойчивым в multi-account режиме без конфликтов по:

- `/tmp` логам;
- ложным `gateway not started` warning;
- shared/pablito Voice Gateway path.

## Что переносить

### 1. Per-account runtime-state export и логи

Нужно сохранить в launcher:

- `export KRAB_RUNTIME_STATE_DIR="$RUNTIME_STATE_DIR"`
- `CLAUDE_PROXY_LOG_FILE="$RUNTIME_STATE_DIR/claude_proxy.log"`
- `WATCHDOG_LOG_FILE="$RUNTIME_STATE_DIR/krab_session_watchdog.log"`

И заменить:

- `claude proxy` лог из `/tmp/claude_proxy.log` → per-account runtime-state;
- `telegram_session_watchdog.py` лог из `/tmp/krab_session_watchdog.log` →
  per-account runtime-state.

### 2. Voice Gateway per-account fallback

Нужно перенести:

- `VOICE_GATEWAY_RUNTIME_DIR`
- `VOICE_GATEWAY_VENV_DIR`
- `VOICE_GATEWAY_STAMP_FILE`
- `VOICE_GATEWAY_LOG_FILE`
- `VOICE_GATEWAY_PID_FILE`
- helper `start_voice_gateway_direct()`

Смысл:

- если штатный `scripts/start_gateway.command` не может писать в shared/pablito
  path, launcher поднимает отдельный per-account Voice Gateway из
  `~/.openclaw/krab_runtime_state/voice_gateway`.

### 3. Truthful readiness для OpenClaw gateway

Нужно перенести:

- helper `wait_gateway_ready()`;
- TCP fallback в `is_gateway_listening()`.

Смысл:

- на части launchd/OpenClaw состояний `lsof` не всегда вовремя показывает
  listener;
- прямой TCP connect к `127.0.0.1:18789` убирает ложный restart/kickstart.

### 4. Обновлённые ветки успеха/ошибки после старта gateway

Нужно заменить проверки:

- `wait_gateway_listening 20 && wait_gateway_healthy 60`

на:

- `wait_gateway_ready 90`

И оставить честную позднюю проверку:

- если `probe_gateway_health` уже проходит после ожидания, печатать delayed-success,
  а не ложный fail.

## Что уже проверено в USER3

- launcher больше не пишет permission errors на `/tmp/*`;
- launcher больше не делает лишний `launchctl kickstart`, когда gateway уже жив;
- `Voice Gateway` реально поднимается через per-account fallback;
- `Krab` снова доходит до `kraab_running`.

## Что уже синхронизировано обратно в `pablito`

- Во внешние launcher-файлы
  `/Users/pablito/Antigravity_AGENTS/new start_krab.command` и
  `/Users/pablito/Antigravity_AGENTS/new Stop Krab.command`
  уже перенесён helper `resolve_voice_gateway_dir()`.
- Это означает, что при возврате на `pablito` launcher больше не должен по
  умолчанию резолвить Voice Gateway в чужой вспомогательный symlink-path:
  теперь у `pablito` приоритет остаётся за локальной копией, а у `USER2/USER3`
  — за shared-копией.
- Синтаксис обоих внешних launcher-файлов после этой синхронизации проверен
  через `bash -n`.

## Что не переносить бездумно

- account-local runtime-state файлы;
- USER3-specific лог-пути как literal path;
- любые изменения в чужом `~/.openclaw/*`, если цель — только hardening launcher-а.
