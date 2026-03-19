# Session Handoff — Краб 19.03.2026

## Addendum 00:05 — low-quota checkpoint и truthful fallback evidence

- Последняя локальная правка в `src/userbot_bridge.py` сделала progress-notice
  честнее:
  - вместо старого текста `Стартовый маршрут` Telegram теперь получает строку
    вида `Текущий маршрут: ... · попытка 2 · fallback активен`;
  - тот же truthful route-line теперь используется и в slow-wait notice.
- Для этого добавлен helper `_build_openclaw_route_notice_line(...)` и
  unit-покрытие в `tests/unit/test_userbot_stream_timeouts.py`.
- Проверка после правки:
  - `python3 -m py_compile src/userbot_bridge.py`
  - `pytest -q tests/unit/test_userbot_stream_timeouts.py`
    → `11 passed`
  - ранее целевой набор regressions для userbot/OpenClaw уже проходил:
    `64 passed`

### Что показала живая runtime truth прямо перед checkpoint

- `curl http://127.0.0.1:8080/api/health/lite`
  показал:
  - `provider = google-gemini-cli`
  - `model = google-gemini-cli/gemini-3-flash-preview`
  - `status = pending`
  - `attempt = 2`
- Это означает, что live-route действительно уже ушёл на fallback, а не просто
  "залип" в UI.

### Что подтвердил tail `openclaw.log`

- Для реального owner-запроса цепочка деградировала так:
  1. `codex-cli/gpt-5.4` → `candidate_failed`, причина `rate_limit`
  2. `google-gemini-cli/gemini-3-flash-preview` → `embedded run timeout`
  3. `openai-codex/gpt-5.4` → `401 missing scopes: model.request`
  4. `qwen-portal/coder-model` → OAuth refresh token expired
- То есть current incident picture честная и воспроизводимая:
  fallback не сломан как механизм, но часть кандидатов operationally деградирует
  по разным причинам.

### Вывод checkpoint

- Текущий Telegram notice теперь лучше объясняет, что происходит под капотом,
  но true token-streaming ещё не внедрён.
- Для следующего чата уже можно опираться не на догадку "Краб молчит", а на
  зафиксированный факт: route/attempt и причины падения кандидатов уже видны в
  runtime truth и в handoff.

## Addendum 22:20 — синхронизация agent-facing инструкций

- Прочитаны и приведены к одной truthful-схеме repo-level файлы:
  - [CLAUDE.md](/Users/pablito/Antigravity_AGENTS/Краб/CLAUDE.md)
  - [GEMINI.md](/Users/pablito/Antigravity_AGENTS/Краб/GEMINI.md)
- Оба файла теперь явно фиксируют:
  - что истина по runtime живёт в `~/.openclaw/*`, а не в старых repo-note;
  - что project progress считается по
    [MASTER_PLAN_SOURCE_OF_TRUTH.md](/Users/pablito/Antigravity_AGENTS/Краб/docs/handoff/MASTER_PLAN_SOURCE_OF_TRUTH.md);
  - что `Telegram userbot`, owner panel `:8080` и native dashboard `:18789`
    имеют разную operational роль и не равны по полномочиям;
  - что текущий live primary — `codex-cli/gpt-5.4`, а `openai-codex/gpt-5.4`
    нельзя считать надёжным primary-маршрутом;
  - что Google API truth надо проверять по paid-key/runtime probe, а не по
    устным воспоминаниям.
- Важный эффект: следующий Claude/Gemini-сеанс теперь должен намного реже
  уезжать в устаревшую картину "LM Studio или Gemini fallback" и реже путать
  web/dash/userbot права между собой.

## Addendum 16:47 — paid Gemini truth и cache-fix owner panel

- Канонический project progress теперь привязан к master-plan, а не к локальному
  operational-срезу. Источник зафиксирован в
  [docs/handoff/MASTER_PLAN_SOURCE_OF_TRUTH.md](/Users/pablito/Antigravity_AGENTS/Краб/docs/handoff/MASTER_PLAN_SOURCE_OF_TRUTH.md).
- Платный Google API ключ подтверждён в live-окружении:
  - `.env`: `GEMINI_API_KEY = GEMINI_API_KEY_PAID = AIzaSyAifJ_0...vSNy3A`
  - `.env`: `GEMINI_API_KEY_FREE = AIzaSyA07LwN...LhPUKY`
  - `.env`: `GOOGLE_API_KEY = AIzaSyAifJ_0...vSNy3A`
  - direct probe `https://generativelanguage.googleapis.com/v1beta/models`
    через текущий `GEMINI_API_KEY` вернул `HTTP 200`.
- Runtime provider `google/` в
  `~/.openclaw/agents/main/agent/models.json` использует не literal-ключ, а
  placeholder `apiKey = GEMINI_API_KEY`, то есть реальное разрешение идёт
  через env и сейчас попадает именно в paid key.
- Исправлен источник путаницы с owner panel `:8080`:
  проблема была не только в backend-валидации, а в stale cached HTML уже
  открытой вкладки. После hard refresh/нового URL вкладка больше не держит старую
  версию редактора глобальной цепочки.
- На root и CSS owner panel добавлены anti-cache заголовки:
  - `Cache-Control: no-store, no-cache, must-revalidate, max-age=0`
  - `Pragma: no-cache`
  - `Expires: 0`
- Browser acceptance после restart и fresh URL подтверждает:
  - глобальный editor цепочки OpenClaw отдаёт только cloud-провайдеры;
  - local LM Studio модели остаются только в отдельном селекторе
    `Модель для этого запуска (облако + local)`, где они и должны быть;
  - сохранение глобальной цепочки больше не ловит
    `runtime_invalid_fallback_model`.
- `codex-cli` оформлен как отдельный provider truth/recovery контур:
  owner panel теперь показывает `CLI OK / CLI login missing / CLI missing` и
  имеет отдельную helper-кнопку `Login Codex CLI.command`.

## Addendum 03:38 — свежая operational truth

- Controlled restart выполнен успешно через launcher.
- Текущий live primary после restart: `codex-cli/gpt-5.4`.
- `curl http://127.0.0.1:8080/api/health/lite`
  показывает `last_runtime_route.provider = codex-cli`,
  `last_runtime_route.model = codex-cli/gpt-5.4`.
- `curl http://127.0.0.1:8080/api/openclaw/model-routing/status`
  показывает:
  - `current_primary = codex-cli/gpt-5.4`
  - `current_fallbacks = google-gemini-cli/gemini-3-flash-preview -> openai-codex/gpt-5.4 -> qwen-portal/coder-model`
  - `live_active_model = codex-cli/gpt-5.4`

## Addendum 03:52 — UX ожидания и owner panel truth

- В `userbot_bridge` добавлены ранние тех-уведомления до hard-timeout:
  userbot заранее сообщает, что запрос жив, контекст собран и модель всё ещё думает.
- Controlled restart после этой правки прошёл успешно.
- `curl http://127.0.0.1:8080/api/health/lite`
  после restart показывает `last_runtime_route.model = codex-cli/gpt-5.4`.
- Owner panel `http://127.0.0.1:8080` после `Sync Data`
  показывает `Рекомендовано (Routing) = codex-cli/gpt-5.4`
  и ту же глобальную цепочку, что и runtime.

### Что изменилось по сравнению с предыдущим handoff

- `openai-codex/gpt-5.4` больше нельзя описывать как "полностью мёртвый":
  одиночные live-probe сейчас проходят через OpenClaw API.
- Но `openai-codex/gpt-5.4` всё равно непригоден как production primary:
  на серии запросов в одном chat-scope он слишком быстро деградирует по latency.
- `codex-cli/gpt-5.4` переживает restart и держит `200 OK` стабильнее,
  поэтому был повышен до primary.

### Свежие live-замеры Codex-маршрутов

- `openai-codex/gpt-5.4`, одиночный live-probe:
  `200 OK`
- `openai-codex/gpt-5.4`, stateful серия:
  быстрый первый ответ, затем резкая деградация latency уже на следующих запросах
- `codex-cli/gpt-5.4`, post-restart stateful серия в одном chat-scope:
  `2.54s -> 7.46s -> 61.0s -> 60.7s`, все 4 ответа `200 OK`
- `codex-cli/gpt-5.4`, post-restart stateless серия с новым chat_id каждый раз:
  `1.25s -> 28.23s -> 62.46s -> 57.71s`, все 4 ответа `200 OK`

### Ключевой вывод addendum

Проблема больше не выглядит чисто session-history багом Краба:
даже stateless серия через `codex-cli` показывает плавающий QoS.
Operational verdict:

- `openai-codex/gpt-5.4` не использовать как primary;
- `codex-cli/gpt-5.4` использовать как рабочий Codex-primary, если приоритет —
  именно доступ к Codex;
- для скорости ответа держать живым fallback на `google-gemini-cli/gemini-3-flash-preview`.

### Расследование: гипотеза про выгоду OpenAI

Это не доказанный факт, а отдельная инженерная гипотеза.
На текущем evidence можно честно утверждать только следующее:

- consumer-friendly путь `openai-codex` плохо ведёт себя как агентный backend;
- у OpenAI может быть product/business мотивация не делать такой путь
  полноценной бесплатной заменой first-party tooling;
- прямого доказательства умысла у нас нет и, вероятно, не будет;
- для инженерного решения этого и не требуется: operationally путь уже признан
  ненадёжным как primary.

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
- Текущий честный primary runtime: `codex-cli/gpt-5.4`.
- `google-antigravity/*` намеренно не участвует в маршрутизации: для него сейчас
  нет usable-квоты, и в live-контур его не возвращали.

### Что проверено прямо в этой сессии

- `python3 -m py_compile src/userbot_bridge.py src/handlers/command_handlers.py src/config.py`
- `pytest tests/unit/test_userbot_stream_timeouts.py tests/unit/test_userbot_buffered_stream_flow.py tests/unit/test_userbot_message_batching.py tests/unit/test_command_handlers_status.py tests/unit/test_openclaw_client.py -q`
  → `59 passed`
- `pytest tests/unit/test_openclaw_runtime_repair.py::test_choose_target_key_prefers_paid_in_auto tests/unit/test_web_app_runtime_endpoints.py::test_provider_ui_metadata_exposes_codex_cli_helper tests/unit/test_web_app_runtime_endpoints.py::test_model_provider_action_launches_codex_cli_helper tests/unit/test_web_app_runtime_endpoints.py::test_runtime_provider_state_marks_codex_cli_as_ready_when_cli_logged_in tests/unit/test_web_app_runtime_endpoints.py::test_owner_panel_root_disables_browser_cache tests/unit/test_web_app_runtime_endpoints.py::test_owner_panel_css_disables_browser_cache -q`
  → `6 passed`
- live smoke через `openclaw_client.send_message_stream(...)`
  → ответ: `Краб на связи.`
- `curl http://127.0.0.1:8080/api/health/lite`
  → `last_runtime_route.model = codex-cli/gpt-5.4`
- owner panel `:8080`
  → блок `Рекомендовано (Routing)` показывает `codex-cli/gpt-5.4`
- direct Google API probe через текущий рабочий env-ключ
  → `HTTP 200`, использован ключ `AIzaSyAifJ_0...vSNy3A`
- fresh browser acceptance `http://127.0.0.1:8080/?fresh=...`
  → global chain editor сохраняет `codex-cli/gpt-5.4` + cloud fallbacks без
    `runtime_invalid_fallback_model`

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
- Текущий live primary выставлен на `codex-cli/gpt-5.4`,
  fallback-цепочка: `google-gemini-cli/gemini-3-flash-preview` → `openai-codex/gpt-5.4` → `qwen-portal/coder-model`

### ✅ Исправлено в репо (ещё не закоммичено в этой ветке)

- `src/userbot_bridge.py`
  - soft/hard логика buffered timeout для OpenClaw;
  - ранние тех-уведомления, что запрос жив и модель всё ещё думает;
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
| `codex-cli/gpt-5.4` | primary | ✅ warmup и restart проходят, route truthful в API/UI | Главная проблема сейчас не падение, а плавающая latency |
| `google-gemini-cli/gemini-3-flash-preview` | fallback#1 | ✅ живой и быстрый safety-net | Уже не primary, а резервный маршрут по подписке |
| `openai-codex/gpt-5.4` | fallback#2 | ⚠️ path живой, но нестабильный | Исторически ловит таймауты в реальном чате и быстро деградирует по latency |
| `qwen-portal/coder-model` | fallback#3 | ⚠️ rate_limit | Portal остаётся резервом, но быстро упирается в лимиты |
| `google-antigravity/*` | вне цепочки | ⛔ не используется | Намеренно исключён из live runtime: нет usable-квоты |

### Ключевой вывод по инциденту

Проблема была составная:
1. userbot ждал "стрим", которого фактически не было, потому что `send_message_stream()`
   буферизует `stream=False` completion и отдаёт кусок только в конце;
2. после `!clear` длинный recap из нескольких Telegram-сообщений превращался в
   очередь отдельных AI-задач;
3. runtime truth drift: один слой думал одно о primary/fallback, а сам gateway
   реально стартовал с другой конфигурацией.

Сейчас эта тройка закрыта: timeout-логика исправлена, batching добавлен, source-of-truth
по runtime выровнен.

### Ключевой вывод по GPT-5.4

`GPT-5.4` сейчас разделяется на два разных operational пути:

- `codex-cli/gpt-5.4` годится как текущий live primary, если нужен максимум от подписки `OpenAI Plus`;
- `openai-codex/gpt-5.4` не стоит использовать как primary, потому что именно этот OAuth-path ведёт себя нестабильно в реальном чате.

То есть главный вывод сейчас уже не про "жёсткий 401", а про **надёжность конкретного маршрута**.

---

## Pending задачи (из roadmap плана)

Файл плана: `/Users/pablito/.claude/plans/agile-mixing-teacup.md`

### Высокий приоритет (нужно в текущей ветке)
- [ ] **Закоммитить текущие repo-фиксы**: buffered timeout, truthful `!status`, message batching
- [ ] **Почистить owner inbox**: в `api/health/lite` висят 2 старых open owner_request item'а, которые больше не должны путать операционную картину
- [ ] **Решить судьбу `openai-codex/gpt-5.4`**: оставить как fallback-only или совсем убрать из live-цепочки после новых наблюдений
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
