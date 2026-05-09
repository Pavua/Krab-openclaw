# Wave 44-U Observability — Krab Agent Runs Visibility

## Цель

Сделать видимыми Krab CLI bypass agent runs (codex-cli/*, google-gemini-cli/*).
Раньше эти runs не были видны нигде — они шли мимо OpenClaw gateway через прямой
subprocess и не регистрировались в OpenClaw Sessions dashboard.

## Two-layer fix

### Layer A — Telemetry to OpenClaw external session register (best-effort)

После каждого `cli_subprocess_bypass` run модуль шлёт `POST` на
`http://127.0.0.1:18789/api/sessions/external` с краткой сводкой run-а.
Если endpoint не существует — silently skip (1.5s timeout).

Override URL через env: `KRAB_OPENCLAW_SESSIONS_URL`.

### Layer B — Krab Own Observability Hub (own panel `:8080/observability`)

Полный собственный dashboard в Owner panel:

- URL: <http://127.0.0.1:8080/observability>
- Live + completed runs (24h окно по умолчанию)
- Фильтр по chat_id, model, status (ok/error/timeout)
- Click row → modal с полным prompt + response excerpts + stderr

## Файлы

| Path | Назначение |
|---|---|
| `~/.openclaw/krab_runtime_state/runs_history.jsonl` | JSONL append лог runs |
| `src/integrations/_observability_log.py` | record / read / get_run helpers |
| `src/modules/web_routers/observability_router.py` | API: `/api/observability/runs`, `/api/observability/run/{id}` |
| `src/web/v4/observability.html` | Frontend dashboard |
| `tests/unit/test_observability_wave44u.py` | Тесты (~12 cases) |

## Telemetry record schema

```json
{
  "ts_started": 1700000000.0,
  "ts_completed": 1700000005.5,
  "request_id": "abc123def456",
  "user_id": null,
  "chat_id": null,
  "model": "codex-cli/gpt-5.5",
  "kind": "krab-bypass",
  "prompt_len": 1024,
  "prompt_excerpt": "first 200 chars of prompt…",
  "response_len": 512,
  "response_excerpt": "first 500 chars of response…",
  "duration_sec": 5.5,
  "status": "ok",
  "exit_code": 0,
  "stderr_excerpt": "",
  "tools_called": [],
  "binary": "codex"
}
```

## Retention / rotation

- Файл ротируется при достижении 100 MB → `runs_history.jsonl.1`
- Один backup (старая версия перезаписывается).

## API

### GET /api/observability/runs

Параметры:
- `since` — окно: `1h`, `24h`, `5m` или секунды (default `24h`)
- `limit` — max записей (1..2000, default 200)
- `status` — фильтр: `ok`/`error`/`timeout`
- `chat_id` — фильтр по chat_id
- `model` — substring match по model

Ответ:
```json
{"ok": true, "count": 42, "runs": [...]}
```

### GET /api/observability/run/{request_id}

Полные данные одного run. 404 если не найден.

## Best-effort guarantees

- Запись в jsonl никогда не выбрасывает исключений.
- POST в OpenClaw silently swallow всех ошибок (1.5s timeout).
- Telemetry call не блокирует agent flow (всё в `finally` блоке после
  reply отдан caller-у).

## Wiring

`cli_subprocess_bypass.py` импортирует `record_agent_run` и вызывает в
`finally` блоке обоих путей: `_complete_codex_with_account_rotation` и
`complete_via_cli` (non-codex).
