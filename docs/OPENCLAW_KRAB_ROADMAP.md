# OpenClaw Krab Roadmap

Дата актуализации: 2026-03-12
Ветка реализации: `codex/handoff-bundle-polish`
Текущая ориентировочная готовность большого плана: `99.9%`

## Цель

Перевести Краба на модель `GPT-5.4 first`, сделать `Telegram userbot` основным owner-каналом, оставить Telegram bot резервным контуром, убрать ложные model IDs и привязать repo/UI к реальному runtime OpenClaw без дублирования уже существующего функционала.

## Cross-Project Decision

- [x] Зафиксирована каноническая позиция по связке `Krab` / `Krab Ear` / `Krab Voice Gateway`:
  - без жёсткого merge рантаймов;
  - через versioned API/contracts;
  - с `Krab` как control plane, `Krab Ear` как audio ingress и `Krab Voice Gateway` как voice/call plane.
- [x] Документ решения:
  [docs/CROSS_PROJECT_API_ARCHITECTURE_RU.md](/Users/pablito/Antigravity_AGENTS/Краб/docs/CROSS_PROJECT_API_ARCHITECTURE_RU.md)
- [x] Первый практический шаг контрактного слоя реализован:
  - `Krab Voice Gateway` теперь публикует `GET /version`, `GET /capabilities`, `GET /v1/capabilities`;
  - `Krab` публикует агрегированный `GET /api/ecosystem/capabilities`;
  - `Krab Ear` capability truth читается через native IPC `get_capabilities`, а не через догадки UI.

## Канонический source-of-truth

- [x] Зафиксировано, что runtime-истина живёт в `~/.openclaw/*`
- [x] Разделены repo-level docs и runtime persona-файлы
- [x] Web-панель полностью привязана к live runtime truth OpenClaw
- [ ] Все owner/workspace настройки переживают restart и читаются единообразно

## Этапы

### Этап 1. Документация и базовый ACL

- [x] Создать канонический roadmap/progress документ
- [x] Переписать repo-level `AGENTS.md`, `SKILLS.md`, `TOOLS.md`
- [x] Внедрить owner/full/partial ACL для userbot
- [x] Добавить owner-only runtime-управление ACL через `!acl` / `!access`
- [x] Покрыть ACL unit-тестами

### Этап 2. Userbot-primary и общий runtime state

- [ ] Перевести userbot на общий workspace/state без отдельной амнезии (частично)
- [ ] Зафиксировать Telegram bot как reserve transport
- [ ] Развести права owner / full / partial / guest по командам и tool-контру
- [x] Подключить live voice/audio ingress в userbot через local Perceptor + MLX Whisper

### Этап 3. GPT-5.4 first routing

- [x] Убрать `gpt-4.5-preview` как ложный primary
- [x] Реализовать compatibility stage для `GPT-5.4`
- [x] Добавить read-only diagnostics слой для runtime model routing
- [x] Настроить downgrade до последнего подтверждённого OpenClaw-compatible OpenAI/Codex ID
- [ ] Убрать legacy-зависимость от `google-antigravity` и перевести recovery-путь на `google-gemini-cli`
- [x] Собрать production fallback chain

### Этап 4. Thinking / slots / quota UX

- [ ] Ввести presets `off / low / medium / high / xhigh`
- [ ] Разделить слоты `Chat / Coding / Vision / Fast`
- [x] Показывать реальные capabilities, а не hardcoded catalog
- [x] Показывать auth/quota/runtime state из OpenClaw
- [x] Честно показывать queue concurrency truth для main-agent вместо ложного общего `parallel / sequential` toggle
- [x] Добавить owner-oriented web ACL control для userbot в `:8080`

### Этап 5. Browser / MCP readiness

- [x] Довести owner browser контур до полного readiness
- [x] Показать staged browser state в `:8080`
- [x] Агрегировать MCP health поверх существующего runtime-реестра

### Этап 6. Безопасность и release discipline

- [x] Закрыть открытые `groupPolicy=open` для Telegram reserve-контура
- [x] Ужесточить reserve bot и risky каналы для Telegram bot
- [x] Добавить release-checklist и merge-gate для крупных этапов

## Что сделано в этой ветке

- [x] Выбрана ветка `codex/gpt54-userbot-primary`
- [x] Подтверждён hidden runtime source-of-truth OpenClaw
- [x] Подтверждено, что `gpt-4.5-preview` в runtime падает с `404`
- [x] Подтверждено, что `GPT-5.4` доступен в текущем Codex-контуре пользователя
- [x] Подтверждено отсутствие нативного Telegram userbot в upstream OpenClaw
- [x] Подтверждено, что browser relay, browser RPC и owner UI сходятся на одной runtime truth картине
- [x] Внедрён ACL owner/full/partial
- [x] Обновлён web model catalog до runtime-aware режима
- [x] Добавлен autoswitch-профиль `production-safe` на основе runtime registry/auth/log truth
- [x] Добавлен честный canary-профиль `gpt54-canary` с `BLOCKED`, если `GPT-5.4` ещё не готов в OpenClaw runtime
- [x] Добавлен read-only compatibility probe для target-модели через текущий OpenClaw gateway
- [x] Userbot читает канонический OpenClaw workspace bundle для owner/full контура
- [x] `!remember / !recall` подключены к общей markdown-памяти `workspace-main-messaging/memory`
- [x] Владелец может выдавать и снимать `full/partial` доступ без ручного редактирования hidden ACL JSON
- [x] В web-панель `:8080` добавлен selector autoswitch-профилей
- [x] В web-панель `:8080` добавлен owner-oriented блок `Userbot ACL` с `Refresh / Grant / Revoke`
- [x] Browser smoke подтвердил, что `gpt54-canary` dry-run виден в UI и отдаёт `target_model_not_in_runtime_registry`
- [x] Live probe подтвердил: `GPT-5.4` пока `BLOCKED` в OpenClaw, потому что ещё отсутствует в runtime registry
- [x] Browser smoke подтвердил, что `Userbot ACL` в UI читает owner/full/partial и применяет `Grant / Revoke` на runtime ACL
- [x] Repair-слой runtime теперь умеет безопасно переводить `dmPolicy=allowlist` без wildcard-дыры
- [x] Repair-слой runtime теперь умеет выставлять `groupPolicy=allowlist` и выводить `groupAllowFrom` как sender allowlist, а не как group/chat IDs
- [x] Создан one-click файл `Apply Reserve Telegram Policy.command` для reserve-safe Telegram Bot
- [x] Live runtime Telegram bot переведён в reserve-safe policy: `allowlist` для DM и групп, внешние tool-guards включены
- [x] Добавлен безопасный `canary-registry sync` для `GPT-5.4` без изменения production routing до compat-probe
- [x] Live compatibility probe подтвердил: `openai-codex/gpt-5.4` реально отвечает через OpenClaw gateway
- [x] Runtime primary переведён на `openai-codex/gpt-5.4`, fallback chain перестроен без legacy `google-antigravity`
- [x] Legacy Gemini OAuth scripts в репозитории обезврежены; секреты удалены, recovery-path перенаправлен на официальный `google-gemini-cli`
- [x] В owner UI добавлен one-click запуск dedicated Browser Relay из карточки `Browser / MCP Readiness`
- [x] Browser readiness truth отвязан от ложного `401`: relay теперь проверяется с gateway token, а `authorized` не путается с `attached`
- [x] Browser acceptance подтверждён живым сценарием: `stop -> start from UI -> attached -> action probe ok`
- [x] UI сразу дорисовывает truth после запуска Browser Relay: `Stage / Tabs / Required MCP` и disabled-state кнопки синхронизированы
- [x] В `Интерфейс AI Ассистента` добавлен truthful meta-блок про параллелизм OpenClaw:
  - queue concurrency для `main/subagent`
  - явное разделение с named semantics `parallel / sequential` из других runtime-контуров
- [x] Browser smoke на изолированном web-инстансе подтвердил рендер нового блока параллелизма и live-клик `Синхронизировать каталог`
- [x] Heartbeat runtime-скрипт переведён на truthful `openclaw health --json` и `openclaw cron status --json` вместо шумного `gateway status`
- [x] Добавлен канонический release-checklist:
  - one-click [Release Gate.command](/Users/pablito/Antigravity_AGENTS/Краб/Release%20Gate.command)
  - runbook [docs/RELEASE_CHECKLIST_RU.md](/Users/pablito/Antigravity_AGENTS/Краб/docs/RELEASE_CHECKLIST_RU.md)
  - источники истины: `pre_release_smoke_latest.json` и `r20_merge_gate_latest.json`
- [x] `pre_release_smoke.py` теперь честно разделяет code failure и environment-blocked verdict на временной учётке (`exit 1` vs `exit 2`)
- [x] `signal_alert_route` использует project Python + CA bundle и truthful web fallback вместо ложных `CERTIFICATE_VERIFY_FAILED` / `gateway token missing`
- [x] `live_channel_smoke.py` снова даёт полезный verdict на временной учётке:
  - `success_rate = 100%`
  - loopback `token_missing` не считается runtime-regression, если `health_lite` и channels truth зелёные
- [x] `pre_release_smoke.py --full --strict-runtime` на USER2 больше не шумит лишними blocked-checks: остаются только owner-only `autoswitch_dry_run` и `e1e3_acceptance`
- [x] Исправлен launcher-регресс: `new start_krab.command` / `new Stop Krab.command` больше не подвисают бесконечно на `openclaw gateway stop`, если gateway уже мёртв или не слушает порт
- [x] Исправлен owner-chat fast-path: запросы вида `проведи полную диагностику` / `cron у тебя уже работает` теперь уходят в deterministic `runtime self-check`, а не в свободную генерацию
- [x] Controlled restart подтверждён после launcher-fix: runtime снова поднимает `:8080` и `:18789`, `telegram_userbot_state=running`
- [x] Reserve Telegram delivery подтверждён после controlled restart через live `openclaw message send --channel telegram --target 312322764`
- [x] Handoff bundle доведён до attach-ready формата:
  - свежий `NEW_CHAT_BOOTSTRAP_PROMPT.md` без устаревших `~82%`
  - автоматическая генерация `ATTACH_SUMMARY_RU.md`, `PABLITO_RETURN_CHECKLIST.md`, `HANDOFF_MANIFEST.json`
  - `.zip`-архив рядом с каждой новой handoff-папкой
  - в bundle подтягиваются `pre_release_smoke_latest.json`, `r20_merge_gate_latest.json` и свежие browser evidence-файлы
- [x] Live `:8080` parallelism truth переподтверждён уже от владельца `pablito` после controlled restart:
  - `Синхронизировать каталог` обновляет live DOM;
  - сохранены [artifacts/ops/live_parallelism_truth_latest.json](/Users/pablito/Antigravity_AGENTS/Краб/artifacts/ops/live_parallelism_truth_latest.json) и [artifacts/ops/live-parallelism-block-2026-03-12.png](/Users/pablito/Antigravity_AGENTS/Краб/artifacts/ops/live-parallelism-block-2026-03-12.png)
- [x] `google-gemini-cli` переподтверждён через безопасный sync-path из локального Gemini CLI store:
  - `google-gemini-cli:default` снова имеет положительный TTL;
  - owner UI показывает `OAuth OK`
- [x] Runtime primary снова переведён в `openai-codex/gpt-5.4` через `gpt54-canary`, а fallback-chain перестроен на `Gemini CLI -> Google -> Qwen -> Google Lite -> Local`
- [x] Добавлен one-click helper [Login OpenAI Codex OAuth.command](/Users/pablito/Antigravity_AGENTS/Краб/Login%20OpenAI%20Codex%20OAuth.command) для официального OpenClaw login flow и быстрой диагностики реально выданных scopes
- [x] В runtime возвращён live `Perceptor`:
  - web panel больше не живёт с `perceptor=None`;
  - `userbot_bridge` транскрибирует входящие `voice/audio` вместо placeholder `(Голосовое сообщение)`;
  - групповой trusted voice fallback работает даже без явного текстового триггера
- [x] Truthful transcriber status теперь различает:
  - `down`, если `Perceptor/STT` вообще не подключён;
  - `degraded`, если local STT готов, но `Voice Gateway` или `Krab Ear` просели;
  - `ready`, если весь voice-stack жив
- [x] Live audio smoke подтверждён:
  - isolated `Perceptor` успешно транскрибирует локально сгенерированный macOS audio sample;
  - связка `KraabUserbot._transcribe_audio_message` подтверждена интеграционным smoke без Telegram-mock placeholder
- [x] Voice runtime hardened после controlled restart на `2026-03-12`:
  - `new start_krab.command` больше не берёт `.venv` вслепую, а детерминированно выбирает runtime-env с нужными модулями;
  - launcher экспортирует `KRAB_PYTHON_BIN` и `KRAB_STT_PYTHON_BIN`, а live runtime подтвердил оба пути как `venv/bin/python`;
  - `Perceptor` isolated worker сам выбирает Python, где реально установлен `mlx_whisper`, вместо немого завязывания на `sys.executable`;
  - `KraabUserbot` сериализует обработку сообщений по `chat_id`, чтобы voice/TTS-ответы одного чата не накладывались друг на друга
- [x] Добавлен базовый `macOS control layer`:
  - owner/full контур получил команду `!mac ...`;
  - доступны `status`, `clipboard get/set`, `notification`, `front/list/open app`, `open`, `Finder reveal`;
  - live smoke на `2026-03-12` подтвердил frontmost app, список приложений, clipboard roundtrip и системное уведомление
- [x] Добавлен второй слой `macOS control layer`:
  - доступны `!mac reminders list/add`, `!mac notes list/add`, `!mac calendar list/events/add`;
  - live smoke на `2026-03-12` подтвердил реальные `list/create/delete` для `Reminders`, `Notes`, `Calendar`;
  - `list_upcoming_calendar_events` ограничен по количеству календарей, чтобы owner-команда не зависала на больших подписках Calendar
- [x] Добавлен `proactive watch + long-term memory` слой:
  - `src/core/proactive_watch.py` собирает owner-oriented runtime/macOS snapshot;
  - userbot запускает background watch loop и пишет digest в общую `workspace-main-messaging/memory`;
  - доступны owner-команды `!watch status|now` и `!memory recent [source]`
- [x] Добавлен `voice runtime profile` слой:
  - userbot больше не держит `!voice` как тупой toggle, а управляет профилем `enabled/speed/voice/delivery`;
  - доступны команды `!voice on|off|toggle`, `!voice speed`, `!voice voice`, `!voice delivery`, `!voice reset`;
  - owner web API теперь отдаёт и обновляет voice runtime через `/api/voice/runtime` и `/api/voice/runtime/update`;
  - owner UI на `:8080` показывает отдельную карточку `Voice Runtime` c read/write controls

## Блокеры и риски

- Совместимость `GPT-5.4` в Codex не гарантирует совместимость того же ID в `OpenClaw openai-codex`
- В рабочем дереве уже есть незакоммиченные изменения, часть из них не относится к этой задаче
- `src/core/provider_manager.py` уже существует как незакоммиченный файл и требует аккуратной миграции, а не перезаписи
- Для полного подтверждения migration всё ещё нужен внешний live E2E owner message через `Telegram userbot` после controlled restart
- Полный `owner -> reserve bot -> reply` уже автоматизирован и подтверждён live E2E отдельным скриптом, но reserve-контур по-прежнему intentionally остаётся менее привилегированным, чем Python userbot
- Residual warning `krab-output-sanitizer loaded without install/load-path provenance` закрыт через managed path-install provenance record в runtime-конфиге
- `openai-codex` сейчас упирается во внешний OAuth scope-blocker:
  - `openclaw models status` показывает профиль `ok expires in 10d`, но live gateway-log фиксирует `HTTP 401: Missing scopes: model.request`
  - расшифровка access token показывает только `openid/profile/email/offline_access`
  - официальный `openclaw models auth login --provider openai-codex --set-default` в этой среде открывает flow с теми же базовыми scopes
  - пока этот scope не появится upstream-способом, runtime как `GPT-5.4 first` остаётся конфигурационно выставленным, но фактически фейловерит в `google-gemini-cli`
- Evidence по этому blocker-у:
  [artifacts/ops/openai_codex_scope_check_latest.json](/Users/pablito/Antigravity_AGENTS/Краб/artifacts/ops/openai_codex_scope_check_latest.json)

## Проверка

- [x] Unit: ACL и runtime-aware status
- [x] Unit: runtime-aware model catalog
- [x] Unit: autoswitch `production-safe` / `gpt54-canary`
- [x] Unit: compatibility probe + web endpoint для него
- [x] Unit: workspace prompt bundle + shared memory bridge userbot/OpenClaw
- [x] Unit: ACL runtime CRUD + owner command `!acl`
- [x] Unit: web ACL endpoints `/api/userbot/acl/status` и `/api/userbot/acl/update`
- [x] Unit: reserve-safe repair для `dmPolicy/groupPolicy` Telegram Bot
- [x] Unit: safe sync canary-модели в runtime registry OpenClaw
- [x] Browser smoke: selector autoswitch-профилей на `:8080`
- [x] Browser smoke: `Userbot ACL` на изолированном web-инстансе подтверждает `Refresh / Grant / Revoke`
- [x] Runtime repair smoke: Telegram reserve-policy применён и отражён в `~/.openclaw/openclaw.json`
- [x] Live probe: `openai-codex/gpt-5.4` = `READY` через OpenClaw gateway
- [x] Live UI/browser: в `:8080` показан staged `Browser / MCP Readiness`
- [x] Unit: `parallelism_truth` в `/api/model/catalog` и чтение queue caps `main/subagent`
- [x] Browser smoke: новый блок параллелизма в `Интерфейс AI Ассистента` подтверждён на изолированном web-инстансе `:18081`
- [x] Live MCP sync: `~/.lmstudio/mcp.json` синхронизирован с managed registry (`--safe`)
- [x] Live photo-route: `photo_smoke` больше не путает `openai-codex/*` с локальной LM Studio моделью
- [x] Live acceptance: `channels_photo_chrome_acceptance.py` проходит после `stop -> start from UI`
- [x] Live smoke: `live_channel_smoke.py` проходит штатным запуском (`success_rate = 100%`)
- [x] Live restart: patched launcher поднимает runtime после controlled stop без зависания на `gateway stop`
- [x] Unit: owner diagnostic questions (`полная диагностика`, `cron`) идут в truthful fast-path без LLM
- [x] Live browser acceptance: тот же блок параллелизма переподтверждён на основном `:8080` после restart уже от владельца `pablito`
- [x] Live auth repair: `google-gemini-cli` снова показывает положительный TTL в CLI и owner UI
- [x] Live routing truth: `openai-codex/gpt-5.4` снова выставлен primary в CLI/API/UI, а scope-blocker отдельно зафиксирован evidence-файлом
- [x] Live voice/STT smoke: local `Perceptor` и userbot audio-ingress подтверждены после controlled restart
- [x] Live voice stack: `transcriber/status=ready`, когда одновременно живы `Perceptor`, `Krab Ear` и `Voice Gateway`
- [x] Live macOS automation smoke: `status`, `Reminders`, `Notes`, `Calendar` подтверждены после controlled restart и cleanup smoke-артефактов
- [x] Live proactive watch smoke: после controlled restart baseline дождался route warmup, сохранил `route_model_changed` в `data/proactive_watch/state.json` и записал digest в общую workspace-memory
- [x] Live voice runtime smoke: после controlled restart `/api/transcriber/status` и `/api/voice/runtime` показывают truthful voice-profile (`enabled=false`, `delivery=text+voice`, `speed=1.5`, `voice=ru-RU-DmitryNeural`), а write-endpoint `/api/voice/runtime/update` проходит на живом `:8080`
- [x] Live owner UI smoke: карточка `Voice Runtime` в `OpenClaw Control Center` загружает профиль через `Обновить Voice Runtime` и сохраняет его через `Сохранить Voice Runtime` без расхождения с runtime
- [x] Live runtime provenance smoke: `live_channel_smoke.py` больше не ловит warning `loaded without install/load-path provenance` после managed install-record repair
- [x] Live reserve Telegram E2E: `owner -> reserve bot -> reply` автоматизирован через `scripts/live_reserve_telegram_roundtrip.py` и one-click launcher
- [ ] Integration: общий workspace/state для userbot и reserve bot
- [ ] E2E: owner message через userbot после restart
- [x] E2E: emergency запрос через Telegram Bot в reserve-safe режиме
- [x] E2E: совместимость `GPT-5.4` в OpenClaw

## Merge gate

- [x] Ветка проходит targeted unit tests
- [x] Ветка проходит smoke
- [x] Есть краткий отчёт: что сделано, как проверено, что осталось
- [ ] Только после этого можно рассматривать merge в `main`
