# Session Handoff — Краб 19.03.2026

## Ветка
`fix/routing-qwen-thinking` (не смёрджена в main)

## Актуальный live verdict на 19.03.2026 02:38

- `userbot_bridge` больше не рубит buffered OpenClaw-ответ по ложному "first chunk timeout":
  теперь первый таймаут мягкий, userbot продолжает ждать живую fallback-цепочку и
  только потом срабатывает отдельный hard-timeout.
- `!status` больше не врёт stale-конфигом: команда показывает фактический
  runtime-route, provider и active model.
- Для private-чатов добавлена склейка быстрой пачки сообщений одного отправителя
  в один LLM-запрос. Это закрывает сценарий после `!clear`, когда длинный recap
  улетает несколькими Telegram-сообщениями и раньше превращался в очередь
  независимых AI-задач.
- Главный drift source-of-truth устранён: раньше `agents.defaults.model.primary`
  уже был изменён, но `agents.list[0].model` продолжал указывать на
  `openai-codex/gpt-5.4`, поэтому UI и userbot думали одно, а gateway реально
  стартовал с другого.
- Текущий честный primary runtime: `google-gemini-cli/gemini-3-flash-preview`.
- `google-antigravity/*` намеренно не участвует в маршрутизации: для него сейчас
  нет usable-квоты, и в live-контур его не возвращали.

### Что проверено прямо в этой сессии

- `python3 -m py_compile src/userbot_bridge.py src/handlers/command_handlers.py src/config.py`
- `pytest tests/unit/test_userbot_stream_timeouts.py tests/unit/test_userbot_buffered_stream_flow.py tests/unit/test_userbot_message_batching.py tests/unit/test_command_handlers_status.py tests/unit/test_openclaw_client.py -q`
  → `59 passed`
- live smoke через `openclaw_client.send_message_stream(...)`
  → ответ: `Краб на связи.`
- `curl http://127.0.0.1:8080/api/health/lite`
  → `last_runtime_route.model = google-gemini-cli/gemini-3-flash-preview`
- owner panel `:8080`
  → блок `Рекомендовано (Routing)` показывает `google-gemini-cli/gemini-3-flash-preview`

## Что было сделано в этой сессии

### ✅ Исправлено и закоммичено

| Коммит | Что |
|--------|-----|
| `4d781a9` | `httpx.TimeoutException` теперь попадает в fallback-loop (был баг: исключение вылетало СНАРУЖИ `for attempt in range(4)`, минуя все fallbacks) |
| `d329dd3` | Таймаут 300s → 180s; `Start Voice Gateway.command` — убран хардкод пути `/Users/pablito/...` |
| `32e8535` | Таймаут httpx 180s → 600s (`connect=30s, read=600s`) чтобы не обрывать внутренний fallback-цикл OpenClaw |

### ✅ Исправлено вне репо / в live runtime

- `new start_krab.command` (строки ~471-478): добавлен блок `if [ -n "${GEMINI_API_KEY_PAID:-}" ]; then export GOOGLE_API_KEY="$GEMINI_API_KEY_PAID"; fi` — теперь OpenClaw provider `google/` получает платный ключ
- `~/.openclaw/openclaw.json` и `~/.openclaw/agents/main/agent/agent.json` выровнены:
  `main agent`, `subagents` и declarative primary теперь смотрят на один и тот же
  runtime-model, без скрытого старта с `gpt-5.4`
- Текущий live primary выставлен на `google-gemini-cli/gemini-3-flash-preview`,
  fallback-цепочка: `google/gemini-3.1-pro-preview` → `qwen-portal/coder-model` → `openai-codex/gpt-5.4`

### ✅ Исправлено в репо (ещё не закоммичено в этой ветке)

- `src/userbot_bridge.py`
  - soft/hard логика buffered timeout для OpenClaw;
  - явное notice-сообщение при долгом ожидании первой части;
  - private message batching для пачки сообщений после `!clear`;
  - skip для follower-handlers уже поглощённых batched-сообщений.
- `src/handlers/command_handlers.py`
  - truthful `!status` по фактическому runtime route.
- Новые unit-тесты:
  - `tests/unit/test_userbot_buffered_stream_flow.py`
  - `tests/unit/test_userbot_message_batching.py`
  - `tests/unit/test_command_handlers_status.py`

---

## Текущее состояние провайдеров (на 19.03.2026)

| Провайдер | Конфиг | Статус | Проблема |
|-----------|--------|--------|----------|
| `google-gemini-cli/gemini-3-flash-preview` | primary, thinking=off | ✅ проходит warmup и live smoke | На текущем наборе провайдеров это первый реально рабочий и честно подтверждённый primary |
| `google/gemini-3.1-pro-preview` | fallback#1, thinking=off | ⚠️ rate_limit | REST-провайдер отвечает, но уходит в rate-limit до ответа на основном маршруте |
| `qwen-portal/coder-model` | fallback#2, thinking=off | ⚠️ rate_limit | Portal остаётся резервом, но быстро упирается в лимиты |
| `openai-codex/gpt-5.4` | fallback#3 | ❌ 401 | **HTTP 401 Missing scopes: `model.request`** — проблема OAuth scope/плана, не квоты |
| `google-antigravity/*` | вне цепочки | ⛔ не используется | Намеренно исключён из live runtime: нет usable-квоты |

### Ключевой вывод по инциденту

Проблема была составная:
1. userbot ждал "стрим", которого фактически не было, потому что `send_message_stream()`
   буферизует `stream=False` completion и отдаёт кусок только в конце;
2. после `!clear` длинный recap из нескольких Telegram-сообщений превращался в
   очередь отдельных AI-задач;
3. runtime truth drift: один слой думал, что primary уже Gemini, а сам gateway
   реально стартовал с `gpt-5.4`.

Сейчас эта тройка закрыта: timeout-логика исправлена, batching добавлен, source-of-truth
по runtime выровнен.

### Ключевой вывод по GPT-5.4

Это по-прежнему не вопрос квот. Логи OpenClaw: `HTTP 401: Missing scopes: model.request`.
Copilot OAuth-токен не имеет нужного API scope. Пока это не исправлено, `gpt-5.4`
не должен быть primary ни в одном live-профиле.

---

## Pending задачи (из roadmap плана)

Файл плана: `/Users/pablito/.claude/plans/agile-mixing-teacup.md`

### Высокий приоритет (нужно в текущей ветке)
- [ ] **Закоммитить текущие repo-фиксы**: buffered timeout, truthful `!status`, message batching
- [ ] **Почистить owner inbox**: в `api/health/lite` висят 2 старых open owner_request item'а, которые больше не должны путать операционную картину
- [ ] **Починить GPT-5.4 OAuth**: попробовать `openclaw auth openai-codex` — перелогиниться и проверить scope `model.request`
- [ ] **Смёрджить ветку** `fix/routing-qwen-thinking` в main

### Средний приоритет (следующая сессия)
- [ ] **Проверить реальный Telegram round-trip после batching**: один тестовый recap из 2-3 сообщений подряд, убедиться что reply один
- [ ] **`handle_browser`**: функция в `command_handlers.py` — зарегистрировать в COMMAND_MAP и `userbot_bridge.py`. `browser_bridge.py` уже создан.
- [ ] **Тестирование Voice Gateway**: `Start Voice Gateway.command` создан, нужно проверить end-to-end
- [ ] **LM Studio интеграция**: SSD подключён, модели загружены — нужно проверить что routing работает

---

## Архитектурные заметки для нового чата

### Как работает fallback-цепочка

```
Telegram сообщение
  → userbot_bridge.py
      → private burst coalescing (только для plain-text private сообщений)
      → openclaw_client.py send_message_stream()
          → buffered completion через POST /v1/chat/completions
          → soft first-chunk timeout в userbot
          → hard buffered timeout только если реально нет ответа слишком долго
          → OpenClaw Gateway :18789 со своей внутренней fallback-цепочкой
```

**Важно**: OpenClaw имеет СВОЙ внутренний fallback и Краб имеет СВОЙ внешний. Не путать!

### Два разных Google-провайдера в OpenClaw

| Провайдер | Аутентификация |
|-----------|----------------|
| `google/` | `GOOGLE_API_KEY` из env (REST API) |
| `google-gemini-cli/` | OAuth через CLI `~/.gemini/` (не зависит от env) |

Исправление в `new start_krab.command`: `export GOOGLE_API_KEY="$GEMINI_API_KEY_PAID"` — теперь `google/` получает платный ключ.

### Настройки thinking

- Сохраняются в `~/.openclaw/openclaw.json` под `agents.defaults.models[model_id].params.thinking`
- После изменения через панель 8080 ТРЕБУЕТСЯ РЕСТАРТ OpenClaw gateway (не Краба!) — изменения в openclaw.json читаются только при старте gateway
- `thinking=high` увеличивает время ответа в 2-3x и потребляет RPM квоту агрессивно

---

## Состояние портов и процессов

| Сервис | Порт | Как проверить |
|--------|------|---------------|
| Краб web panel | 8080 | `curl http://127.0.0.1:8080/api/health/lite` |
| OpenClaw gateway | 18789 | `curl http://127.0.0.1:18789/health` |
| Voice Gateway | 8090 | `curl http://127.0.0.1:8090/health` |
| Chrome CDP | 9222 | `curl http://localhost:9222/json/version` |

## Runtime state файлы

- Lock/PID/stop-flags: `~/.openclaw/krab_runtime_state/`
- Watchdog log: `/tmp/krab_session_watchdog.log`
- OpenClaw log: `/Users/pablito/Antigravity_AGENTS/Краб/openclaw.log`
- App state (translator, reminders): `data/`

## Как перезапустить

```bash
cd /Users/pablito/Antigravity_AGENTS
bash "new Stop Krab.command"
# подождать 3-5 секунд
open "new start_krab.command"
# проверить:
curl -s http://127.0.0.1:8080/api/health/lite | python3 -m json.tool
```

## Как перезапустить только OpenClaw gateway (после изменений openclaw.json)

```bash
openclaw gateway stop
sleep 2
openclaw gateway start
```
