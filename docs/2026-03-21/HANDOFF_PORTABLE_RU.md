# Handoff Portable — Краб — 21.03.2026

## Что это

Этот файл нужен как переносимый handoff для нового чата или другого агента.
Он не заменяет runtime truth, а собирает в одном месте:

- что уже подтверждено живыми проверками;
- какие изменения внесены в код;
- какие гипотезы уже проверяли и почему они больше не являются приоритетными;
- что осталось сделать дальше без потери контекста.

## Source Of Truth

- Runtime OpenClaw: `/Users/pablito/.openclaw/openclaw.json`
- Runtime моделей: `/Users/pablito/.openclaw/agents/main/agent/models.json`
- Runtime auth: `/Users/pablito/.openclaw/agents/main/agent/auth-profiles.json`
- Боевая persona и память: `/Users/pablito/.openclaw/workspace-main-messaging`
- Репозиторий кода: `/Users/pablito/Antigravity_AGENTS/Краб`
- Рабочая ветка: `fix/routing-qwen-thinking`

## Важный принцип по прогрессу

- Live operational progress проекта: около `91%`
- Baseline master-plan: около `31%`
- Эти числа не равны друг другу
- Baseline нельзя использовать как live progress

## Что подтверждено живьём на 21 марта 2026

- Web panel `http://127.0.0.1:8080` поднята
- OpenClaw gateway `:18789` поднят
- Voice Gateway `:8090` поднят
- Krab Ear поднят
- `GET /api/health/lite` отвечает truthfully
- Последний warmup route truthful:
  - `provider = google-gemini-cli`
  - `model = google-gemini-cli/gemini-3.1-pro-preview`
  - `active_tier = paid`
- Codex MCP на USER2 подтверждён как usable
- Browser truth и owner/debug Chrome path разведены

## Что изменено в коде в этой фазе

### Browser bridge

Файл: `/Users/pablito/Antigravity_AGENTS/Краб/src/integrations/browser_bridge.py`

- добавлен `action_probe(...)`
- `CDP_URL` переведён на `http://127.0.0.1:9222`
- добавлено чтение websocket endpoint из `DevToolsActivePort`
- добавлен fallback через raw websocket CDP
- добавлена диагностика `error_repr` и `error_type`
- добавлен учёт `KRAB_OPERATOR_HOME`
- исправлен баг `await page.title()`
- добавлен raw-first путь, если известен `DevToolsActivePort`

### Web panel / readiness

Файл: `/Users/pablito/Antigravity_AGENTS/Краб/src/modules/web_app.py`

- добавлен owner Chrome action probe в `/api/openclaw/browser-mcp-readiness`
- browser endpoints получили timeout-ограничения
- readiness и smoke-пробы частично распараллелены
- добавлен truthful state для ordinary Chrome policy block:
  - `chrome_policy_blocked`

### MCP registry

Файл: `/Users/pablito/Antigravity_AGENTS/Краб/src/core/mcp_registry.py`

- `chrome-profile` manual setup теперь честно предупреждает:
  ordinary Chrome default profile может быть заблокирован новой Chrome policy

### Helper для ordinary Chrome

Файл: `/Users/pablito/Antigravity_AGENTS/Краб/new Open Owner Chrome Remote Debugging.command`

- helper пытается честно перезапустить ordinary Chrome
- helper умеет диагностировать cross-account блокировку
- helper теперь должен честно сигнализировать Chrome policy block, если в логе есть:
  `DevTools remote debugging requires a non-default data directory`

### Документация

Файл: `/Users/pablito/Antigravity_AGENTS/Краб/docs/LM_STUDIO_MCP_SETUP_RU.md`

- добавлено подтверждённое ограничение для Chrome `146.0.7680.154`

### Тесты

Файлы:

- `/Users/pablito/Antigravity_AGENTS/Краб/tests/unit/test_integrations_clients.py`
- `/Users/pablito/Antigravity_AGENTS/Краб/tests/unit/test_web_app_runtime_endpoints.py`

Добавлено покрытие на:

- ws fallback через `DevToolsActivePort`
- raw CDP fallback
- readiness state `action_probe_ok`
- readiness state `chrome_policy_blocked`

## Как проверено

### Код

- `python3 -m py_compile` для изменённых файлов
- `pytest -q tests/unit/test_integrations_clients.py -k 'browser_bridge'`
- `pytest -q tests/unit/test_web_app_runtime_endpoints.py -k 'browser_mcp_readiness or browser_access_paths or open_owner_chrome_endpoint'`

### Runtime

- `curl http://127.0.0.1:8080/api/health/lite`
- `curl http://127.0.0.1:8080/api/browser/status`
- `curl 'http://127.0.0.1:8080/api/openclaw/browser-mcp-readiness?url=https%3A%2F%2Fexample.com'`
- helper-log:
  `/tmp/krab-owner-chrome-remote-debugging.log`

## Главный confirmed blocker текущей фазы

### Ordinary Chrome default-profile attach

Подтверждено:

- `127.0.0.1:9222` слушает
- `http://127.0.0.1:9222/json/version` даёт `404`
- websocket endpoint из `DevToolsActivePort` существует
- но websocket handshake на browser endpoint уходит в `TimeoutError`
- helper-log содержит строку:
  `DevTools remote debugging requires a non-default data directory`

Вывод:

- проблема не в permission prompt
- проблема не в cross-account prompt
- проблема не в том, что "нужно ещё раз approve"
- текущий Chrome `146.0.7680.154` блокирует remote debugging для default profile

### Truthful operational interpretation

- ordinary Chrome path сейчас нельзя считать готовым
- owner panel должна говорить об этом честно
- правильный fallback:
  - использовать OpenClaw Debug browser
  - или исследовать attach через отдельный non-default `--user-data-dir`

## Текущее состояние browser readiness

По live API:

- `/api/browser/status`
  - `attached = false`
  - `tab_count = 0`
- `/api/openclaw/browser-mcp-readiness`
  - overall: `attention`
  - browser path: `relay_scope_limited`
  - owner chrome path: `manual_setup_required` или после полного truthful-fix должен стать `chrome_policy_blocked`

Почему relay не зелёный:

- HTTP relay доступен
- но `gateway probe` ограничен `missing scope: operator.read`
- runtime смотрит в dedicated debug contour, а не в ordinary Chrome владельца

## Что уже обсуждали и что не надо повторять

- Не надо снова считать, что baseline `31%` отражает live progress
- Не надо снова считать, что простое `approve access to Chrome` решает attach
- Не надо снова считать, что `chrome://inspect` сам поднимет рабочий DevTools MCP путь
- Не надо смешивать:
  - ordinary Chrome владельца
  - dedicated OpenClaw Debug browser
  - openclaw relay / gateway probe
- Не надо снова диагностировать USER2 cross-account issue как главный блокер:
  он был реальным раньше, но текущий confirmed blocker уже другой

## Что осталось сделать следующему агенту

### Минимальный путь

1. Завершить truthful UI state `chrome_policy_blocked` в живом runtime
2. Обновить handoff docs и собрать свежий bundle уже из `pablito`
3. Решить стратегию ordinary Chrome:
   - принять как known issue current Chrome
   - или сделать отдельный supported path через non-default `--user-data-dir`

### Если нужен рабочий browser action уже сейчас

- использовать OpenClaw Debug browser как operational fallback

## Что приложить в следующий чат

- этот файл
- `/Users/pablito/Antigravity_AGENTS/Краб/docs/2026-03-21/NEXT_CHAT_PROMPT_RU.md`
- `/Users/pablito/Antigravity_AGENTS/Краб/docs/2026-03-21/KNOWN_AND_DISCUSSED_RU.md`
- свежий handoff bundle, если будет собран

## Короткий статус

- Проект: `91%`
- Текущий browser/MCP блок: `99%`
- Реальный остаток: не “понять, что происходит”, а либо зафиксировать policy block как финальную truth, либо построить новый supported path через non-default Chrome data dir
