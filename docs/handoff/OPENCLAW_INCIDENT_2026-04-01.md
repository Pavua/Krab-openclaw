# Инцидент OpenClaw / Krab после обновления 01.04.2026

## Контекст

После обновления OpenClaw от 1 апреля 2026 проявились три разных класса симптомов:

1. `codex-cli/gpt-5.4` выглядел как primary в runtime, но часть запросов уходила в fallback на Gemini.
2. Browser-контур визуально открывал и закрывал вкладки, а в системе одновременно жили несколько Chrome/CDP-профилей.
3. В Telegram ранее был зафиксирован crash-loop на `USER_BANNED_IN_CHANNEL`.

Этот документ фиксирует именно подтверждённые факты, что уже исправлено и что ещё осталось добить.

## Подтверждённые факты

### 1. `codex-cli` registry drift после обновления

Подтверждено 1 апреля 2026 локальными runtime-файлами и логами:

- `~/.openclaw/openclaw.json` держал primary `codex-cli/gpt-5.4`.
- при этом в `~/.openclaw/agents/main/agent/models.json` и `~/.openclaw/openclaw.json`
  provider `codex-cli` либо отсутствовал, либо был создан без обязательного provider-shape.
- из-за этого gateway логировал:
  - `startup model warmup failed for codex-cli/gpt-5.4: Error: Unknown model: codex-cli/gpt-5.4`
  - затем после частичной ручной синхронизации:
    `models.providers.codex-cli.baseUrl: Invalid input: expected string, received undefined`

Вывод:
- обновление/repair привело к рассинхрону между declared primary и provider catalog;
- проблема была не в квоте OpenAI Plus и не в самом Codex CLI login-state.

### 2. `gpt-5.4` мог реально отвечать, но retry-логика Краба слишком рано сдавалась

Подтверждено логами и кодом:

- `codex-cli/gpt-5.4` в live probe отвечал корректно через `/v1/chat/completions`.
- в `src/openclaw_client.py` cloud quality recovery делал только один переход на альтернативный cloud-кандидат;
- после этого chain мог оборваться на следующем `provider_error` / `provider_timeout`,
  хотя в runtime-цепочке оставались ещё кандидаты.
- отдельный подтверждённый semantic case:
  `Cloud Code Assist API error (400): Unable to submit request because The model does not support setting thinking_budget to 0.`

Вывод:
- часть уходов на Gemini была вызвана не исчерпанием квоты, а сочетанием timeout/semantic-error и слишком короткой retry-цепочки.

### 3. Browser truth сейчас раздвоена

Подтверждено live-окружением и диагностикой:

- workspace helper-path продолжает считать intended debug-портом `9223`;
- repo/browser bridge при этом умеет молча fallback-нуться на legacy `9222`;
- OpenClaw browser runtime отдельно поднимает свой debug browser на `18800`.

Практический эффект:
- пользователь видит несколько Chrome-процессов одновременно;
- action probe на `https://example.com` создаёт ощущение «вкладка открылась и сразу закрылась»;
- `9223` vs `9222` сейчас всё ещё не доведён до единого source-of-truth.

Addendum 01.04.2026 поздний fix-pass:

- owner-readiness в web-панели больше не должен сам создавать временную вкладку для ordinary Chrome;
- ordinary owner path теперь изолирован от runtime `mcporter.json` truth и не должен тихо скатываться в `9223`;
- `overall.readiness` в `/api/openclaw/browser-mcp-readiness` теперь учитывает и `owner_chrome.readiness`,
  чтобы успешный debug browser не маскировал сломанный ordinary Chrome.

### 4. Crash-loop `USER_BANNED_IN_CHANNEL`

Диагностика показала:

- основной helper-path `_safe_reply_or_send_new` уже переведён на best-effort доставку;
- конкретный старый unhandled stack был вызван тем, что error-path пытался снова писать в тот же недоступный чат;
- рядом всё ещё есть непокрытые прямые `send_message` ветки, которым нужны дополнительные unit-тесты.

Вывод:
- главный crash-loop в helper-path уже выглядит закрытым;
- delivery-guard coverage стоит расширить, но это не текущий primary blocker для `gpt-5.4`.

## Что исправлено в этой recovery-сессии

### 1. Исправлен sync provider registry для `codex-cli`

Изменён файл:
- `scripts/openclaw_model_registry_sync.py`

Что сделано:
- если в runtime registry добавляется alias-провайдер вроде `codex-cli`,
  скрипт теперь досеивает provider-level shape (`baseUrl`, `api`) из близкого sibling-провайдера
  (`openai-codex` / `openai`);
- это убирает broken provider entry вида `models[]` без `baseUrl`.

### 2. Исправлена cloud retry-цепочка в OpenClawClient

Изменён файл:
- `src/openclaw_client.py`

Что сделано:
- quality recovery теперь не обрывается после первого cloud fallback;
- клиент исключает уже попробованные cloud-модели и идёт дальше по runtime fallback chain;
- budget попыток теперь считается динамически от фактической длины runtime-цепочки,
  а не жёстко как `4`.

### 3. Добавлены unit-тесты

Изменены файлы:
- `tests/unit/test_openclaw_model_registry_sync.py`
- `tests/unit/test_openclaw_client.py`

Покрыто:
- provider shape seeding для `codex-cli`;
- multi-step cloud quality retry после `provider_timeout` и `thinking_budget to 0`.

### 4. Browser / owner-readiness fix-pass

Изменены файлы:
- `src/integrations/browser_bridge.py`
- `src/modules/web_app.py`
- `tests/unit/test_integrations_clients.py`
- `tests/unit/test_web_app_runtime_endpoints.py`

Что сделано:
- `BrowserBridge` научен читать runtime CDP truth из `mcporter.json` и `remote_debugging_port.txt`;
- если runtime уже объявил свой HTTP CDP endpoint, legacy `9222` больше не подмешивается молча как source-of-truth;
- для owner Chrome web-панель создаёт отдельный изолированный `BrowserBridge`,
  жёстко привязанный к ordinary contour (`9222`);
- owner-readiness переведён с активного `action_probe()` на неинвазивный `passive_probe()`;
- сам `passive_probe()` теперь не создаёт новую вкладку, если в браузере нет открытых страниц;
- `overall.readiness` теперь честно падает в `attention/blocked`, если именно owner Chrome не готов;
- `capability_registry` и его `system_control.browser_control` больше не читают runtime singleton bridge,
  а нормализуют тот же owner Chrome probe, что и Browser / MCP Readiness;
- legacy `/api/browser/*` теперь явно маркируются как `runtime_debug_browser`,
  чтобы их не путали с ordinary owner Chrome API.

## Что проверено live

1 апреля 2026 после правок:

- `python3 scripts/openclaw_model_registry_sync.py --model codex-cli/gpt-5.4 --reasoning on`
  успешно досеял `baseUrl` и `api` в:
  - `~/.openclaw/agents/main/agent/models.json`
  - `~/.openclaw/openclaw.json`
- целевые unit-тесты прошли:
  - `tests/unit/test_openclaw_model_registry_sync.py`
  - `tests/unit/test_openclaw_client.py`
- после последовательного restart gateway в `/tmp/openclaw/openclaw-2026-04-01.log`
  больше не воспроизвёлся `Config invalid` для `codex-cli.baseUrl`;
- прямой live probe в `http://127.0.0.1:18789/v1/chat/completions`
  дважды вернул `200 OK` и ответы:
  - `OK-CODEX`
  - `OK-CODEX-2`

Поздний browser fix-pass 01.04.2026:

- таргетный набор unit-тестов
  `tests/unit/test_integrations_clients.py` +
  `tests/unit/test_web_app_runtime_endpoints.py`
  дал `148 passed`;
- оставшиеся `2 failed` подтверждены отдельно на чистом baseline `ca96027`
  и не относятся к browser-fix:
  - `test_model_compat_probe_passes_model_and_reasoning`
  - `test_runtime_chat_session_clear_calls_openclaw_client`
- временный web-контур новой ветки на `http://127.0.0.1:18081/`
  отдал `200 OK` на `/api/openclaw/browser-mcp-readiness`;
- после загрузки панели и ручного `Синхронизировать данные`
  owner Chrome tab-list не изменился:
  - `9222`: те же 3 page-target (`about:blank`, `chrome://newtab/`, `chrome://newtab-footer/`)
  - `9223`: `0` page-target

Вывод:
- новый owner-readiness path не воспроизвёл лишнее создание вкладок;
- визуальный эффект «вкладка открылась и закрылась» на этом fix-pass больше не подтверждён;
- соседний owner-facing capability layer тоже переведён на owner-contour truth.

Live state check того же вечера:

- живой runtime на `http://127.0.0.1:8080` и gateway на `:18789` остаются подняты;
- но `/api/runtime/handoff?probe_cloud_runtime=0` показал, что runtime всё ещё работает из ветки
  `codex/openclaw-apr1-recovery` на коммите `ca96027`;
- поэтому timeout на живом `/api/openclaw/browser-mcp-readiness` в этом контуре
  пока трактуется как отсутствие rollout browser-fix ветки в боевой runtime,
  а не как опровержение новых правок.

## Что осталось сделать

### Высокий приоритет

- починить browser source-of-truth:
  - не писать stale `9223` truth до фактической готовности workspace Chrome;
  - решить судьбу legacy `/api/browser/*`: либо честно пометить их как runtime/debug-only,
    либо вынести на отдельный owner-bridge contour;
  - при желании усилить контракт `BrowserBridge`, чтобы `explicit_cdp_http_urls` автоматически отключал websocket/file fallback без необходимости передавать пустой список путей вручную.
- отдельно выполнить rollout ветки `codex/owner-browser-passive-truth` в живой `pablito` runtime
  и повторить live browser/owner verification уже на новом коде.

### Средний приоритет

- расширить unit-тесты на все send-only delivery ветки вокруг
  `USER_BANNED_IN_CHANNEL` / `CHAT_WRITE_FORBIDDEN`;
- понять, почему owner/session UI накапливает раздутые session counters и не нужен ли controlled session reset для долгих чатов.

### Низкий приоритет

- отдельно проверить, воспроизводится ли ещё terminal EOF в `new start_krab.command`;
- на момент этой recovery-сессии `bash -n "new start_krab.command"` проходит успешно,
  то есть статической syntax-ошибки в файле не подтверждено.

## Короткий итог

На 1 апреля 2026 основной production-blocker по `codex-cli/gpt-5.4` локализован и частично исправлен:

- live registry drift для `codex-cli` устранён;
- gateway снова отвечает через `codex-cli/gpt-5.4`;
- retry-логика Краба больше не должна слишком рано сдавать cloud-цепочку после одного плохого fallback.

Неглавный, но ещё живой инцидент:

- browser/CDP контур остаётся раздвоенным (`9223` / `9222` / `18800`) и требует отдельного fix-pass.
