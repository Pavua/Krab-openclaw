# OpenClaw / Krab — unrestricted runtime и sessions cleanup (2026-04-02)

## Что было целью

- вернуть `codex-cli/gpt-5.4` в реально unrestricted-режим, а не в частично безопасный профиль;
- убрать ложные выводы permission-аудита про `sandbox` / `TCC`;
- убрать ложные auth-fallback с сообщением про `AIza...`, когда модель просто обсуждала ключи в обычном тексте;
- починить нативный dashboard `:18789/sessions`, который показывал stale session rows с огромными токенами и пустым detail.

## Что подтверждено и сделано

### 1. Unrestricted Codex backend возвращён

Живая причина ограничений была смешанной:

- в `~/.openclaw/agents/main/sessions/*` sandbox у main-сессий уже был `off`;
- но `cliBackends.codex-cli.args` в runtime запускал `codex exec` без явного break-glass флага;
- из-за этого сам Codex CLI оставался в дефолтном safe execution mode, даже когда OpenClaw sandbox уже снят.

Исправление:

- repo-level helper `scripts/openclaw_account_bootstrap.py` теперь гарантирует:
  - `exec --dangerously-bypass-approvals-and-sandbox --json --color never --skip-git-repo-check`
  - и тот же break-glass режим для `resume`
- bootstrap уже прогнан на живом `~/.openclaw/openclaw.json`

Факт после ремонта:

- warmup после рестарта снова проходит через `codex-cli/gpt-5.4` со `status=200`

### 2. Permission audit стал truthful

Старая проблема была не в реальном deny, а в неверном SQL-вызове `sqlite3`:

- `scripts/check_macos_permissions.py` передавал `?` как bind-параметр в CLI-режиме sqlite;
- из-за этого audit писал ложный `tcc_db_unavailable`;
- кроме того unsigned local `.command` ошибочно считались quarantine только потому, что `spctl` отвечал `rejected`.

Исправление:

- TCC query теперь собирается как inline SQL c корректным escaping;
- `spctl rejected` остаётся диагностикой, но больше не считается quarantine-блокером без `com.apple.quarantine`

Факт после ремонта:

- `./check_permissions.command` показывает:
  - `Practical readiness: True`
  - `TCC DB accessible: True`
  - `Quarantine findings: 0`

### 3. Ложный auth-fallback убран

Старая проблема:

- `src/openclaw_client.py` искал паттерны вроде `401`, `unauthorized`, `invalid api key` в любом тексте ответа;
- если модель в нормальном ответе просто анализировала логи/ключи, runtime ошибочно объявлял `openclaw_auth_unauthorized`;
- дальше маршрут срывался на fallback и пользователю прилетало ложное `Проверь Gemini ключ формата AIza...`

Исправление:

- auth/quota/provider semantic-паттерны теперь применяются только когда ответ реально выглядит как transport/provider error blob, а не как обычный длинный текст модели.

### 4. Нативный dashboard sessions очищен и застрахован от повторения

Подтверждённый корень:

- `~/.openclaw/agents/main/sessions/sessions.json` держал stale entries;
- часть записей ссылалась на уже отсутствующие `.jsonl`;
- список на `:18789/sessions` рисовал token counters прямо из store;
- detail читал transcript и на отсутствующем файле возвращал пусто.

Что сделано в live-данных:

- выполнен backup:
  - `~/.openclaw/agents/main/sessions/sessions.json.bak_cleanup_20260402_145623Z`
- из store удалены 25 stale-записей с отсутствующим `sessionFile`

Что сделано в установленном OpenClaw:

- патчнут файл:
  - `/opt/homebrew/lib/node_modules/openclaw/dist/session-utils-Jgzk2Bo-.js`
- backup файла:
  - `/opt/homebrew/lib/node_modules/openclaw/dist/session-utils-Jgzk2Bo-.js.bak_20260402_150556Z`

Смысл патча:

- list/detail/title/preview теперь ищут существующий transcript через единый helper;
- если основной `.jsonl` уже уехал в `.deleted` / `.reset`, detail может взять архив;
- `sessions.list` фильтрует записи, у которых нет ни живого transcript, ни архивного fallback.

Факт после рестарта:

- `openclaw gateway call sessions.list` больше не содержит stale keys из проблемного скрина;
- gateway health после рестарта зелёный.

## Проверки

- `pytest -q tests/unit/test_openclaw_account_bootstrap.py tests/unit/test_check_macos_permissions.py tests/unit/test_openclaw_client.py`
  - результат: `74 passed`
- `python3 scripts/openclaw_account_bootstrap.py --openclaw-bin "$(command -v openclaw)"`
  - результат: `ok=true`
- `./check_permissions.command`
  - результат: readiness зелёный
- controlled restart через:
  - `/Users/pablito/Antigravity_AGENTS/new Stop Krab.command`
  - `/Users/pablito/Antigravity_AGENTS/new start_krab.command`
- `openclaw gateway health`
  - результат: `OK`

## Остатки

- MCP attach к ordinary Chrome по-прежнему нестабилен: DevTools tooling не видит `DevToolsActivePort`, даже когда `9222` жив;
- сам runtime/browser owner path это не блокирует, но для агентной DOM-верификации всё ещё неприятный хвост;
- `scripts/check_macos_permissions.py` исторически не был в git этой ветки, поэтому перед commit/merge его нужно включать в тот же changeset вместе с тестами.

## Практический статус

- unrestricted runtime recovery: `100%`
- false-positive auth fallback: `100%`
- sessions dashboard stale-store recovery: `95%`
- browser-based MCP attach verification: `70%`
