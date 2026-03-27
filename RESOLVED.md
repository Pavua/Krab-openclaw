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

### Reserve Telegram Bot переведён в reserve-safe без потери доставки
- Причина: live reserve roundtrip уже отвечал, но оставался красным по preflight, потому что runtime policy была небезопасной: `dmPolicy=open`, а `allowFrom` фактически допускал wildcard-сценарий. Это был policy debt, а не transport outage.
- Что сделано: создан rollback-бэкап runtime-конфига `/Users/pablito/.openclaw/openclaw.json.reservebot_backup_20260327_165059`, затем через [scripts/openclaw_runtime_repair.py](/Users/pablito/Antigravity_AGENTS/Краб/scripts/openclaw_runtime_repair.py) применён reserve-safe repair для канала `telegram`, после чего gateway перезапущен. Итоговый runtime truth: `dmPolicy=allowlist`, `groupPolicy=allowlist`, `allowFrom=[312322764, 6435872621]`, `groupAllowFrom=[312322764]`.
- Проверка:
  - `./venv/bin/python scripts/live_reserve_telegram_roundtrip.py --timeout-sec 40 --output /tmp/krab_reserve_roundtrip_after_policy.json` -> `ok=true`, `reserve_safe=true`, ответ от reserve bot получен
  - `python3 scripts/live_channel_smoke.py --max-age-minutes 120 --output /tmp/krab_live_channel_smoke_after_reserve_policy.json` -> `ok=true`

### Живой owner Telegram roundtrip подтверждает voice delivery и отсутствие свежей scratchpad leakage
- Что сделано: в owner chat `312322764` отправлен контролируемый smoke-trigger с маркером `SMOKE-OK-20260327-165416`, после чего собран фактический ответ из Telegram history и отдельно проверено наличие voice-вложения.
- Проверка:
  - текстовый ответ пришёл с тем же message id `11410` и содержит маркер `SMOKE-OK-20260327-165416`, без сигнатур вида `Ready.`, `Wait, I'll check...`, shell-команд и прочего внутреннего scratchpad-мусора;
  - отдельное voice-сообщение пришло как message id `11411`;
  - `telegram_transcribe_voice(chat_id=312322764, message_id=11411)` подтвердил смысл voice-ответа;
  - evidence-отчёт сохранён в [output/reports/OWNER_TELEGRAM_VOICE_HYGIENE_SMOKE_2026-03-27.md](/Users/pablito/Antigravity_AGENTS/Краб/output/reports/OWNER_TELEGRAM_VOICE_HYGIENE_SMOKE_2026-03-27.md).

### Private burst batching подтверждён unit-тестом и живым MCP-burst smoke
- Причина: быстрые private-пачки сообщений должны схлопываться в один query, иначе userbot плодит несколько независимых AI-маршрутов и ответы расползаются по чату.
- Что сделано: в [src/userbot_bridge.py](/Users/pablito/Antigravity_AGENTS/Краб/src/userbot_bridge.py) batching переведён с одноразового history-snapshot на короткий `settle-poll`, чтобы follower-сообщения успевали появиться в Telegram history перед финальной склейкой; в [tests/unit/test_userbot_message_batching.py](/Users/pablito/Antigravity_AGENTS/Краб/tests/unit/test_userbot_message_batching.py) добавлен regression на delayed history visibility.
- Проверка:
  - `source venv/bin/activate && pytest -q tests/unit/test_userbot_message_batching.py -q` -> `3 passed`
  - live MCP-burst `BURSTMCP-20260327-172132`: сообщения `11423/11424/11425` ушли в одну секунду, а message `11425` стал единым anchor со склеенными `part 1/3`, `part 2/3` и `part 3/3`, что зафиксировано в [output/reports/PRIVATE_BURST_BATCHING_SMOKE_2026-03-27.md](/Users/pablito/Antigravity_AGENTS/Краб/output/reports/PRIVATE_BURST_BATCHING_SMOKE_2026-03-27.md).

### Добавлен one-click сбор evidence по Telegram transport
- Что сделано: добавлен CLI-скрипт [scripts/telegram_transport_evidence.py](/Users/pablito/Antigravity_AGENTS/Краб/scripts/telegram_transport_evidence.py) и launcher [Collect Telegram Transport Evidence.command](/Users/pablito/Antigravity_AGENTS/Краб/Collect%20Telegram%20Transport%20Evidence.command).
- Что собирает:
  - последние `owner_mention` из persisted inbox (`inbox_state.json`);
  - сигнатуры `private_text_burst_coalesced` из `krab.log` / `openclaw.log`.
- Проверка:
  - `pytest -q tests/unit/test_telegram_transport_evidence.py`
  - `Collect Telegram Transport Evidence.command`
  - живой артефакт: [telegram_transport_evidence_20260327-171617.json](/Users/pablito/Antigravity_AGENTS/Краб/output/reports/telegram_transport_evidence_20260327-171617.json)

### Private explicit trigger и mention-gated/group flow подтверждены живым second-account E2E
- Причина: после подключения второго Telegram MCP аккаунта `p0lrd` нужно было честно проверить не только self/owner path, но и реальный inbound от другого аккаунта, включая `owner_request` и `owner_mention`.
- Что сделано:
  - в [src/userbot_bridge.py](/Users/pablito/Antigravity_AGENTS/Краб/src/userbot_bridge.py) userbot-путь усилен против застревания новых запросов в `open`: stale per-chat background-task теперь отменяется, новый owner-запрос ставится в честную background-очередь вместо отката в inline-path, а initial Telegram ack больше не валит handoff при сбое `reply()`;
  - в [tests/unit/test_userbot_buffered_stream_flow.py](/Users/pablito/Antigravity_AGENTS/Краб/tests/unit/test_userbot_buffered_stream_flow.py) добавлены регрессии на stale background-task, очередь второго фонового запроса и выживание при падении initial ack;
  - проведён live E2E через второй Telegram MCP аккаунт `p0lrd`, evidence сохранён в [output/reports/TELEGRAM_OWNER_E2E_SECOND_ACCOUNT_2026-03-27.md](/Users/pablito/Antigravity_AGENTS/Краб/output/reports/TELEGRAM_OWNER_E2E_SECOND_ACCOUNT_2026-03-27.md).
- Проверка:
  - `pytest -q tests/unit/test_userbot_buffered_stream_flow.py tests/unit/test_userbot_message_batching.py`
  - private inbound explicit trigger:
    - persisted inbox item `incoming:312322764:11432`
    - text delivery `11434`
    - voice delivery `11435`
  - group mention / owner mention в `YMB FAMILY FOREVER` (`-1001804661353`):
    - persisted inbox items `incoming:-1001804661353:764818` и `incoming:-1001804661353:764820`
    - queue handoff `764821`
    - финальный text `764824`
    - voice `764825`

### Raw fallback `No response from OpenClaw.` больше не проходит как пользовательский group-answer
- Причина: часть group/background сценариев могла завершиться сырым transport-fallback текстом `No response from OpenClaw.`; такой текст доходил в Telegram как есть и ещё озвучивался TTS, что выглядело как неаккуратная деградация, хотя сам transport/inbox lifecycle был рабочим.
- Что сделано:
  - в [src/userbot_bridge.py](/Users/pablito/Antigravity_AGENTS/Краб/src/userbot_bridge.py) добавлена нормализация сырых fallback-строк в user-facing Telegram surface;
  - voice/TTS теперь не озвучивает transport/model error-surface;
  - автоподклейка `/tmp/voice_reply.*` больше не срабатывает поверх error-surface;
  - в [tests/unit/test_userbot_buffered_stream_flow.py](/Users/pablito/Antigravity_AGENTS/Краб/tests/unit/test_userbot_buffered_stream_flow.py) добавлены точечные регрессии на нормализацию fallback и запрет voice для error-ответов.
- Проверка:
  - `pytest -q tests/unit/test_userbot_buffered_stream_flow.py tests/unit/test_userbot_message_batching.py -q` -> `14 passed`
  - live group E2E через второй Telegram MCP аккаунт `p0lrd`:
    - trigger `764827` с маркером `GROUPP0-FIX2-20260327-1834`
    - ack `764828`
    - финальный text `764829`: `GROUPP0-FIX2-20260327-1834 🦀 Краб на связи, всё работает стабильно и чётко.`
    - финальный voice `764830`
    - persisted inbox item `incoming:-1001804661353:764827` завершился статусом `done` и событием `reply_sent`
  - отдельный evidence: [output/reports/TELEGRAM_GROUP_FALLBACK_RECOVERY_2026-03-27.md](/Users/pablito/Antigravity_AGENTS/Краб/output/reports/TELEGRAM_GROUP_FALLBACK_RECOVERY_2026-03-27.md)

### `new start_krab.command` снова переживает зависший старый `src.main` без ручного Stop
- Причина: launcher пытался завершить старый `src.main` только через `SIGTERM` и ждал около 6 секунд. Если процесс зависал или держался в старой сессии, one-click старт обрывался на сообщении `Старый процесс Krab не завершился мягко`, не доходя до `🚀 Starting Krab...`.
- Что сделано:
  - в [new start_krab.command](/Users/pablito/Antigravity_AGENTS/new%20start_krab.command) `stop_old_krab_processes()` усилен по схеме `TERM -> wait -> KILL -> wait`, с явным логом, что launcher применяет forced-stop именно как last resort для one-click UX;
  - проверка прогнана на живом зависшем `src.main`: launcher сам добил старый процесс, поднял gateway, дошёл до `Starting Krab...` и стартовал новый runtime.
- Проверка:
  - live launcher trace показал последовательность `🧹 Found old Krab processes -> 🪓 Применяю принудительную остановку -> 🚀 Starting Krab...`
  - `curl http://127.0.0.1:8080/api/health/lite` после старта вернул `{\"ok\":true,\"status\":\"up\"...}`
  - `krab_status` снова показывает `status=up`, `telegram_session_state=ready`

### Stale owner inbox cleanup больше не завязан на два жёстко вшитых message_id
- Причина: старая утилита `cleanup_old_inbox_items.py` была одноразовой миграцией под `10897/10848`, из-за чего любой следующий cleanup снова требовал ручной правки кода. В runtime при этом реально висели именно эти два старых `owner_request`, которые засоряли `api/health/lite`.
- Что сделано:
  - в [scripts/cleanup_old_inbox_items.py](/Users/pablito/Antigravity_AGENTS/Краб/scripts/cleanup_old_inbox_items.py) утилита переведена в reusable CLI: по умолчанию она архивирует только stale `owner_request/open` старше `3` суток, а любые более рискованные cleanup-сценарии требуют явных флагов `--kind`, `--message-id`, `--item-id`;
  - добавлены unit-тесты в [tests/unit/test_cleanup_old_inbox_items.py](/Users/pablito/Antigravity_AGENTS/Краб/tests/unit/test_cleanup_old_inbox_items.py) на cutoff, default selection и message-id narrowing;
  - живой runtime cleanup выполнен на `inbox_state.json`: записи `incoming:312322764:10848` и `incoming:312322764:10897` переведены в `cancelled` с actor `system-cleanup`.
- Проверка:
  - `python3 -m py_compile scripts/cleanup_old_inbox_items.py tests/unit/test_cleanup_old_inbox_items.py`
  - `pytest -q tests/unit/test_cleanup_old_inbox_items.py tests/unit/test_inbox_service.py -q` -> `23 passed`
  - `./venv/bin/python scripts/cleanup_old_inbox_items.py --dry-run` показал ровно два кандидата: `10848` и `10897`
  - `./venv/bin/python scripts/cleanup_old_inbox_items.py` успешно закрыл оба stale item-а
  - `curl http://127.0.0.1:8080/api/health/lite` спустя короткий TTL-кеш показал `open_items=4`, `pending_owner_requests=2`
  - evidence: [output/reports/INBOX_STALE_OWNER_REQUEST_CLEANUP_2026-03-27.md](/Users/pablito/Antigravity_AGENTS/Краб/output/reports/INBOX_STALE_OWNER_REQUEST_CLEANUP_2026-03-27.md)

### Inbox lifecycle теперь автоматически закрывает stale relay и recovery-traces вместо накопления open-хвоста
- Причина: в runtime оставались два системных долга:
  - `relay_request` создавался в `_escalate_relay_to_owner()`, но никогда не закрывался автоматически, даже если владелец уже вернулся в тот же чат;
  - `proactive_watch` открывал `proactive_action` не только на `gateway_down`, но и на `gateway_recovered/scheduler_backlog_cleared`, то есть recovery-события тоже оставались `open`.
- Что сделано:
  - в [src/userbot_bridge.py](/Users/pablito/Antigravity_AGENTS/Краб/src/userbot_bridge.py) добавлен `_acknowledge_open_relay_requests_for_chat()`: при следующем directed owner message в том же чате старые `open relay_request` автоматически переводятся в `done`;
  - в [src/core/proactive_watch.py](/Users/pablito/Antigravity_AGENTS/Краб/src/core/proactive_watch.py) `proactive_action` теперь открывается только для активных проблем (`gateway_down`, `scheduler_backlog_created`), а recovery-события закрывают соответствующий trace через `set_status_by_dedupe(...)`;
  - добавлены регрессии в [tests/unit/test_userbot_inbox_flow.py](/Users/pablito/Antigravity_AGENTS/Краб/tests/unit/test_userbot_inbox_flow.py) и [tests/unit/test_proactive_watch.py](/Users/pablito/Antigravity_AGENTS/Краб/tests/unit/test_proactive_watch.py).
- Проверка:
  - `pytest -q tests/unit/test_userbot_inbox_flow.py tests/unit/test_proactive_watch.py tests/unit/test_proactive_inbox_trace.py tests/unit/test_scheduler.py -q` -> `26 passed`
  - live cleanup existing runtime-tail:
    - `relay:312322764:11402` -> `done`, note `owner_followed_up_after_relay`
    - legacy `proactive:watch_trigger:route_model_changed:2026-03-12T05:05:00+00:00` -> `done`, note `legacy_non_actionable_proactive_trace`
  - `GET /api/inbox/status` и `GET /api/health/lite` после TTL-кеша показывают уже `open_items=2`, `attention_items=0`, `pending_owner_requests=2`
  - evidence: [output/reports/INBOX_LIFECYCLE_TRUTH_SYNC_2026-03-27.md](/Users/pablito/Antigravity_AGENTS/Краб/output/reports/INBOX_LIFECYCLE_TRUTH_SYNC_2026-03-27.md)

### Inbox summary теперь различает новые owner-запросы и уже взятые в background processing
- Причина: после честного lifecycle-fix `health-lite` всё ещё сваливал в один счётчик и настоящие `open`, и `acked` items. В результате `pending_owner_requests=7` выглядел как семь забытых запросов, хотя часть из них уже обрабатывалась в фоне.
- Что сделано:
  - в [src/core/inbox_service.py](/Users/pablito/Antigravity_AGENTS/Краб/src/core/inbox_service.py) summary расширен truthful-полями:
    - `fresh_open_items`
    - `acked_items`
    - `new_owner_requests`
    - `processing_owner_requests`
    - `new_owner_mentions`
    - `processing_owner_mentions`
  - добавлен regression-тест в [tests/unit/test_inbox_service.py](/Users/pablito/Antigravity_AGENTS/Краб/tests/unit/test_inbox_service.py), который подтверждает split `open vs acked`.
- Проверка:
  - `pytest -q tests/unit/test_inbox_service.py -q` -> `22 passed`

### Второй Telegram MCP переживает `database is locked` через serialized access и controlled restart клиента
- Причина: второй Telegram MCP (`krab_test_mcp`) после restart-переходов иногда падал на `database is locked`, потому что Pyrogram session живёт в sqlite-файле и параллельные tool-call'ы/подвисший session handle могли конфликтовать.
- Что сделано:
  - в [mcp-servers/telegram/telegram_bridge.py](/Users/pablito/Antigravity_AGENTS/Краб/mcp-servers/telegram/telegram_bridge.py) добавлена сериализация всех Telegram API операций через `asyncio.Lock()`;
  - при transient session-lock bridge теперь один раз делает controlled `stop -> recreate client -> start -> retry`;
  - добавлены unit-тесты в [tests/unit/test_telegram_bridge.py](/Users/pablito/Antigravity_AGENTS/Краб/tests/unit/test_telegram_bridge.py) на idempotent `start()` и recovery после `database is locked`.
- Проверка:
  - `python3 -m py_compile mcp-servers/telegram/telegram_bridge.py tests/unit/test_telegram_bridge.py`
  - `pytest -q tests/unit/test_telegram_bridge.py -q` -> `2 passed`
- Важная оговорка:
  - уже поднятый MCP host в текущем чате hot-reload не умеет, поэтому этот hardening начнёт работать для tool-host после следующего restart/new chat, но код и тесты уже готовы.
