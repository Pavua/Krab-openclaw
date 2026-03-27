# RESOLVED.md

Закрытые задачи и регрессии Краба.

С 2026-03-26 новые закрытые пункты фиксируем здесь, а не в `IMPROVEMENTS.md`.

---

## 2026-03-26

### Голосовые ответы больше не выключаются после рестарта
- Причина: `VOICE_MODE_DEFAULT`, `VOICE_REPLY_SPEED`, `VOICE_REPLY_VOICE`, `VOICE_REPLY_DELIVERY` не жили в typed-config, поэтому после рестарта `userbot_bridge` молча откатывался к fallback `False`.
- Что сделано: добавлены typed-поля и поддержка `update_setting()` в [src/config.py](/Users/pablito/Antigravity_AGENTS/Краб/src/config.py), усилена санитизация transport-output в [src/openclaw_client.py](/Users/pablito/Antigravity_AGENTS/Краб/src/openclaw_client.py) и [src/userbot_bridge.py](/Users/pablito/Antigravity_AGENTS/Краб/src/userbot_bridge.py), добавлены точечные тесты.
- Проверка:
  - `pytest -q tests/unit/test_config_voice_settings.py tests/unit/test_userbot_privacy_guards.py tests/unit/test_openclaw_client.py -q`
  - `GET http://127.0.0.1:8080/api/voice/runtime` вернул `enabled=true`
  - owner panel после refresh подтянула `Voice Runtime -> Replies: ON`

### Audio preflight в активном OpenClaw runtime подтверждён как исправленный
- Канонический verifier: [verify_openclaw_audio_preflight.py](/Users/pablito/.openclaw/workspace-main-messaging/verify_openclaw_audio_preflight.py)
- Результат проверки 2026-03-26:
  - `source: /Users/pablito/.openclaw-git/src/media-understanding/audio-preflight.ts -> fixed`
  - `installed-dist: /opt/homebrew/lib/node_modules/openclaw/dist/pi-embedded-BaSvmUpW.js -> fixed`
  - functional probes `runner auto-detect` и `preflight` прошли успешно
- Следствие: устаревшие записи в backlog о том, что Homebrew/npm bundle всё ещё buggy, больше не соответствуют фактам.

### Owner Panel: кнопка `Run Smoke Trigger` снова рабочая
- Причина: frontend-кнопка стучалась в `POST /api/diagnostics/smoke`, которого не было в backend-контракте.
- Что сделано: добавлен endpoint-агрегатор в [src/modules/web_app.py](/Users/pablito/Antigravity_AGENTS/Краб/src/modules/web_app.py), который честно объединяет browser smoke и photo smoke; добавлен regression-тест в [tests/unit/test_web_app_runtime_endpoints.py](/Users/pablito/Antigravity_AGENTS/Краб/tests/unit/test_web_app_runtime_endpoints.py).
- Проверка:
  - `pytest -q tests/unit/test_web_app_runtime_endpoints.py -q`
  - `POST http://127.0.0.1:8080/api/diagnostics/smoke` вернул `ok=true`
  - живой клик в owner panel показал toast `Triggering Smoke Tests...`
  - артефакт: [run-smoke-trigger-toast-20260326-2000.png](/Users/pablito/Antigravity_AGENTS/Краб/output/playwright/run-smoke-trigger-toast-20260326-2000.png)

### Owner Panel: первичная гидратация больше не блокируется целиком тяжёлым Browser/MCP probe
- Причина: `refreshAll()` грузил панели строго последовательно, а `loadOpenclawStatus()` ждал тяжёлый `/api/openclaw/browser-mcp-readiness` в общем `Promise.all`, из-за чего быстрые runtime-блоки висели на `—`.
- Что сделано: в [src/web/index.html](/Users/pablito/Antigravity_AGENTS/Краб/src/web/index.html) `refreshAll()` переведён на `Promise.allSettled`, Browser/MCP probe вынесен из критического пути OpenClaw-карточки, добавлен явный loading-state; добавлен regression-тест в [tests/unit/test_web_panel_bootstrap_order.py](/Users/pablito/Antigravity_AGENTS/Краб/tests/unit/test_web_panel_bootstrap_order.py).
- Проверка:
  - `pytest -q tests/unit/test_web_panel_bootstrap_order.py -q`
  - живой reload owner panel: на первом кадре `Voice Runtime` уже показывает `ON / text+voice`, а Browser/MCP честно в состоянии `LOADING`, затем карточка догидрируется без ручного refresh
  - артефакты: [owner-panel-first-paint-20260326-2003.png](/Users/pablito/Antigravity_AGENTS/Краб/output/playwright/owner-panel-first-paint-20260326-2003.png), [owner-panel-settled-after-reload-20260326-2004.png](/Users/pablito/Antigravity_AGENTS/Краб/output/playwright/owner-panel-settled-after-reload-20260326-2004.png)

### Owner Panel: translator и runtime-карточки сами восстанавливаются после controlled restart
- Причина: открытая вкладка переживала краткий даунтайм `:8080`, ловила `ERR_CONNECTION_REFUSED`, но без автоматических recovery-pass оставалась в полугидрированном состоянии до ручного `Синхронизировать данные`.
- Что сделано: в [src/modules/web_app.py](/Users/pablito/Antigravity_AGENTS/Краб/src/modules/web_app.py) добавлен единый `/api/translator/bootstrap`, а в [src/web/index.html](/Users/pablito/Antigravity_AGENTS/Краб/src/web/index.html) translator переведён на этот fast-path и добавлены автоматические recovery-pass после старта страницы и при возврате видимости; статическая регрессия расширена в [tests/unit/test_web_panel_bootstrap_order.py](/Users/pablito/Antigravity_AGENTS/Краб/tests/unit/test_web_panel_bootstrap_order.py), backend-покрытие добавлено в [tests/unit/test_web_app_runtime_endpoints.py](/Users/pablito/Antigravity_AGENTS/Краб/tests/unit/test_web_app_runtime_endpoints.py).
- Проверка:
  - `pytest -q tests/unit/test_web_app_runtime_endpoints.py tests/unit/test_web_panel_bootstrap_order.py -q`
  - живой controlled restart с открытой owner panel: после transient `ERR_CONNECTION_REFUSED` вкладка без ручного `Sync` снова показывает `Translator Readiness = READY`, `Route & Model = auto`, `Channel State = LOCAL`
  - `python3 scripts/live_channel_smoke.py --max-age-minutes 120 --output /tmp/krab_live_channel_smoke_now.json` -> `ok=true`
  - `python3 scripts/channels_photo_chrome_acceptance.py --output /tmp/krab_channels_photo_acceptance_now.json` -> `ok=true`
  - артефакт: [owner-panel-post-restart-auto-recovery-20260326-2026.png](/Users/pablito/Antigravity_AGENTS/Краб/output/playwright/owner-panel-post-restart-auto-recovery-20260326-2026.png)

## 2026-03-27

### Periodic auto-handoff export больше не ловит искусственный timeout на тяжёлом cloud probe
- Причина: `userbot_bridge` ходил в `/api/runtime/handoff` с жёстким `timeout=10`, а сам endpoint внутри мог ждать `get_cloud_runtime_check()` до `18s`, поэтому periodic maintenance иногда логировал `auto_handoff_export_failed timed out` без реальной поломки runtime.
- Что сделано: в [src/modules/web_app.py](/Users/pablito/Antigravity_AGENTS/Краб/src/modules/web_app.py) `GET /api/runtime/handoff` получил явный флаг `probe_cloud_runtime`, а в [src/userbot_bridge.py](/Users/pablito/Antigravity_AGENTS/Краб/src/userbot_bridge.py) periodic auto-export переведён на быстрый snapshot `?probe_cloud_runtime=0`; добавлены регрессии в [tests/unit/test_web_app_runtime_endpoints.py](/Users/pablito/Antigravity_AGENTS/Краб/tests/unit/test_web_app_runtime_endpoints.py) и [tests/unit/test_userbot_auto_handoff_export.py](/Users/pablito/Antigravity_AGENTS/Краб/tests/unit/test_userbot_auto_handoff_export.py).
- Проверка:
  - `pytest -q tests/unit/test_userbot_auto_handoff_export.py tests/unit/test_web_app_runtime_endpoints.py -q`
  - `GET /api/runtime/handoff?probe_cloud_runtime=0` -> примерно `1.0s`, `cloud_runtime = {available: false, skipped: true, reason: "probe_disabled"}`
  - `GET /api/runtime/handoff` -> примерно `3.3s`, тяжёлый cloud runtime probe остаётся доступен для полного handoff
