# E2E MCP Smoke Harness

Живой e2e smoke test для Краба — отправляет команды и диалоговые реплики в Telegram
через MCP (p0lrd, SSE `http://127.0.0.1:8011/sse`) и валидирует ответы Краба.
Заменяет устаревший `scripts/e2e_smoke_test.py` (стучал в несуществующий `/rpc`).

## Быстрый запуск

```bash
cd /Users/pablito/Antigravity_AGENTS/Краб
venv/bin/python scripts/e2e_mcp_smoke.py --verbose
```

## Требования

- Краб запущен (`new start_krab.command`), `/api/v1/health` отвечает `ok:true`.
- MCP p0lrd LaunchAgent (`com.krab.mcp-p0lrd`) поднят на `127.0.0.1:8011`.
- Пакет `mcp` в venv: `venv/bin/pip install mcp`.
- Owner chat = `312322764`, How2AI group = `-1001587432709` (см. константы в скрипте).

## Exit codes

| Code | Смысл |
|------|-------|
| 0    | Все тесты PASS |
| 1    | ≥ 1 FAIL |
| 2    | Краб/MCP unhealthy — скип |

## Тест-кейсы

| Name | Что проверяет |
|------|----------------|
| `version_cmd` | `!version` отдаёт версию |
| `uptime_cmd` | `!uptime` отдаёт время работы |
| `proactivity_status` | `!proactivity` — текущий уровень |
| `silence_status` | `!silence status` — состояние режима тишины |
| `model_cmd` | `!model` — имя активной модели |
| `dialog_no_gospodin` | W31: нет дефолтного «Мой Господин» в диалоге |
| `phantom_action_guard` | Краб не рапортует о несделанных действиях |
| `how2ai_blocklist_silence` | **W26.1**: в чате How2AI (`-1001587432709`) Краб молчит (blocklist) |

## Опции

```bash
--verbose       # debug логи
--timeout N     # таймаут ожидания ответа (default 30с)
--test NAME     # один тест
--no-save       # не перезаписывать docs/E2E_RESULTS_LATEST.md
```

## Артефакты

- Markdown отчёт: `docs/E2E_RESULTS_LATEST.md` (перезаписывается при каждом запуске).

## Как добавить тест

1. Добавить `TestCase(...)` в список `TEST_CASES` в `scripts/e2e_mcp_smoke.py`.
2. Матчеры: `must_contain` (OR, любой), `must_not_contain` (NONE), `min_length`, `max_length`.
3. Для "silent" тестов: `expect_no_reply=True` + `wait_seconds=<seconds>` (ждём тишину).
4. Для другого чата: `chat_id=<id>`.

## Транспорт

Harness использует официальный Python SDK `mcp`:

```python
from mcp import ClientSession
from mcp.client.sse import sse_client
```

Аргументы MCP tools обёрнуты в `{"params": {...}}` — это контракт p0lrd сервера
(pydantic-модели ожидают поле `params`). Ответ Krab'а детектируется через
`telegram_get_chat_history` + фильтр по `from_user != "Yung Nagato"` (имя MCP
аккаунта-отправителя).
