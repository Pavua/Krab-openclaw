# OpenClaw Krab Roadmap

Дата актуализации: 2026-03-12
Ветка реализации: `codex/live-8080-parallelism-acceptance`
Текущая ориентировочная готовность большого плана: `99%`

## Цель

Перевести Краба на модель `GPT-5.4 first`, сделать `Telegram userbot` основным owner-каналом, оставить Telegram bot резервным контуром, убрать ложные model IDs и привязать repo/UI к реальному runtime OpenClaw без дублирования уже существующего функционала.

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
- [x] Исправлен launcher-регресс: `new start_krab.command` / `new Stop Krab.command` больше не подвисают бесконечно на `openclaw gateway stop`, если gateway уже мёртв или не слушает порт
- [x] Исправлен owner-chat fast-path: запросы вида `проведи полную диагностику` / `cron у тебя уже работает` теперь уходят в deterministic `runtime self-check`, а не в свободную генерацию
- [x] Controlled restart подтверждён после launcher-fix: runtime снова поднимает `:8080` и `:18789`, `telegram_userbot_state=running`
- [x] Reserve Telegram delivery подтверждён после controlled restart через live `openclaw message send --channel telegram --target 312322764`

## Блокеры и риски

- Совместимость `GPT-5.4` в Codex не гарантирует совместимость того же ID в `OpenClaw openai-codex`
- В рабочем дереве уже есть незакоммиченные изменения, часть из них не относится к этой задаче
- `src/core/provider_manager.py` уже существует как незакоммиченный файл и требует аккуратной миграции, а не перезаписи
- Для полного подтверждения migration всё ещё нужен внешний live E2E owner message через `Telegram userbot` после controlled restart
- Для полного подтверждения reserve-контура всё ещё нужен полный round-trip owner -> reserve bot -> reply; пока подтверждена только живая post-restart delivery из runtime в Telegram
- В smoke остаётся residual warning `krab-output-sanitizer loaded without install/load-path provenance`; это не runtime-blocker, но хвост доверенной provenance
- `google-gemini-cli` сейчас хрупкий как fallback: `openclaw models status` показывает `expires in 0m`, а gateway-log фиксировал refresh failure 2026-03-11
- Live `:8080` для нового блока параллелизма пока не переподтверждён после restart из-под `USER2`:
  старый `src.main` принадлежит `pablito`, пережил попытку controlled restart и не дал заменить процесс без доступа владельца
- Для закрытия этого хвоста добавлен helper:
  [Verify Live Parallelism On Pablito.command](/Users/pablito/Antigravity_AGENTS/Краб/Verify%20Live%20Parallelism%20On%20Pablito.command)

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
- [ ] Integration: общий workspace/state для userbot и reserve bot
- [ ] E2E: owner message через userbot после restart
- [ ] E2E: emergency запрос через Telegram Bot в reserve-safe режиме (delivery post-restart подтверждена, полный inbound round-trip ещё не автоматизирован)
- [x] E2E: совместимость `GPT-5.4` в OpenClaw
- [ ] Live browser acceptance: тот же блок параллелизма должен быть переподтверждён на основном `:8080` после restart уже от владельца `pablito`

## Merge gate

- [x] Ветка проходит targeted unit tests
- [x] Ветка проходит smoke
- [x] Есть краткий отчёт: что сделано, как проверено, что осталось
- [ ] Только после этого можно рассматривать merge в `main`
