# OpenClaw Krab Roadmap

Дата актуализации: 2026-03-15
Ветка реализации: `codex/userbot-runtime-truth-hardening`
Текущая ориентировочная готовность большого плана: `~52%`

## Цель

Довести Краба до честного `owner-first` runtime на базе OpenClaw:
- `Telegram userbot` как основной owner-канал;
- `Telegram bot` как reserve-safe контур;
- owner web panel `:8080` как truthful operational layer;
- shared workspace/persistence без амнезии;
- release-gate и handoff bundle как реальные точки восстановления, а не формальность.

## Канонический source-of-truth

- [x] Runtime-истина живёт в `~/.openclaw/*`
- [x] Repo-level docs не притворяются runtime-persona файлами
- [x] Shared workspace truth читается через runtime/API
- [x] Owner/workspace state переживает restart и отражается единообразно
- [x] Все handoff/release артефакты синхронизированы с текущей веткой и текущим runtime truth

## Ключевые решения проекта

- [x] `legacy antigravity` не удаляем
- [x] `google-antigravity` и `google-gemini-cli` рассматриваются как разные квотные контуры
- [x] Reserve bot intentionally остаётся менее привилегированным, чем Python userbot
- [x] Owner inbox / approval / task flow ведём через единый operator-workflow state

## Этапы

### Этап 1. Shared workspace и память

- [x] Userbot читает общий OpenClaw workspace
- [x] `!remember / !recall` привязаны к общей memory
- [x] Web/runtime endpoints показывают `workspace_state`
- [x] Capability snapshot показывает `shared_workspace_attached`

### Этап 2. Owner / ACL / Operator workflow

- [x] Внедрён ACL `owner / full / partial / guest`
- [x] Owner-only admin-команды отделены от `full`
- [x] Owner UI умеет `Refresh / Grant / Revoke`
- [x] Inbox / escalation / approval flow поддерживает `trace_id` и `source_item_id`
- [x] Persisted operator workflow фиксирует owner inbound/outbound и linked followups
- [x] Runtime owner truth теперь предпочитает runtime ACL вместо stale fallback из `config.OWNER_USERNAME`
- [x] Owner panel live-smoke подтверждает truthful owner label `312322764, p0lrd`
- [x] Runtime banner после restart показывает truthful owner label, совпадающий с ACL/UI
- [x] Owner-only reasoning trace вынесен в отдельную команду `!reasoning` и не смешивается с основным ответом

### Этап 3. Userbot / reserve transport

- [x] Reserve Telegram roundtrip заведён отдельным live smoke
- [x] Reserve transport не дублирует owner-rich права userbot
- [x] Живой owner roundtrip уже виден в persisted inbox/runtime truth
- [x] Канонический локальный owner-E2E artifact уже лежит в `artifacts/live_smoke`
- [x] Telegram userbot truthful-streaming semantics выровнены до `buffered_edit_loop`
- [x] Reasoning visibility userbot truthfully помечена как `owner_optional_separate_trace`

### Этап 4. Voice / translator / restart persistence

- [x] Voice runtime profile управляется и из userbot, и из web panel
- [x] Translator runtime/session отражаются через owner runtime snapshot
- [x] Owner/workspace настройки переживают restart
- [x] Voice/translator state виден через `/api/ops/runtime_snapshot`
- [x] `/api/translator/readiness` теперь даёт product-ready breakdown:
  - `foundation_checks`
  - `account_runtime`
  - `active_session`
  - `product_surface`
- [x] `/api/translator/control-plane` агрегирует session/policy слой поверх Voice Gateway contract:
  - `gateway_contract`
  - `sessions`
  - `current_session`
  - `runtime_policy`
  - `quick_phrases`
  - `approval_state`
- [x] Translator orchestration write-layer заведён через owner-facing backend endpoints:
  - `POST /api/translator/session/start`
  - `POST /api/translator/session/policy`
  - `POST /api/translator/session/action`
  - `POST /api/translator/session/runtime-tune`
  - `POST /api/translator/session/quick-phrase`
- [x] Translator session inspector заведён как отдельный truthful слой:
  - `GET /api/translator/session-inspector`
  - `POST /api/translator/session/summary`
  - `POST /api/translator/session/escalate`
  - owner panel теперь читает why-report / timeline digest / escalation context через backend, а не напрямую из Gateway
- [x] Translator mobile readiness заведён как отдельный truthful слой для `iPhone companion`:
  - `GET /api/translator/mobile-readiness`
  - `POST /api/translator/mobile/register`
  - `POST /api/translator/mobile/bind`
  - `POST /api/translator/mobile/trial-prep`
  - `POST /api/translator/mobile/remove`
  - owner panel теперь читает mobile registry / binding snapshot через backend, а не из локального UI-state
- [x] Translator delivery matrix заведён как отдельный truthful product layer:
  - `GET /api/translator/delivery-matrix`
  - owner panel теперь читает ordinary/internet call tracks через backend, а не склеивает это на фронте из кусочков state
  - ordinary-call path и internet adapters теперь имеют явные blockers / next steps / guardrails
- [x] Translator live trial preflight заведён как отдельный truthful ops layer:
  - `GET /api/translator/live-trial-preflight`
  - `scripts/check_translator_live_trial_preflight.py`
  - `Check Translator Live Trial Preflight.command`
  - слой переиспользует те же helper paths и runtime truth, что и owner panel
- [x] Translator mobile onboarding packet заведён как отдельный truthful onboarding layer:
  - `GET /api/translator/mobile/onboarding`
  - `POST /api/translator/mobile/onboarding/export`
  - `scripts/build_translator_mobile_onboarding_packet.py`
  - `Build Translator Mobile Onboarding Packet.command`
  - слой переиспользует readiness/control/mobile/delivery/preflight truth, а не рисует локальный UI-state
- [x] Owner panel `:8080` теперь показывает отдельную карточку `Translator Readiness`
  - есть manual refresh
  - DOM и API сверены live smoke
- [x] Owner panel translator-карточка теперь показывает session policy / runtime tuning / quick phrases
  - при недоступном Gateway честно показывает `gateway_unavailable`, а не stale/mock данные
- [x] Owner panel translator-карточка теперь показывает `Session orchestration` и `Quick phrase test`
  - write-кнопки автоматически блокируются, если Voice Gateway down
  - defaults синхронизируются из truthful `operator_actions.draft_defaults`
- [x] Owner panel translator-карточка теперь показывает `Why report` и `Timeline digest`
  - summary/escalation buttons disabled, если активной session нет
  - UI честно отображает `gateway_unavailable` и не зависает на спиннере
- [x] Owner panel translator-карточка теперь показывает `iPhone companion`
  - registry/current snapshot синхронизируются из truthful `mobile-readiness`
  - при недоступном Gateway кнопки `register/bind` честно disabled
  - direct POST `/api/translator/mobile/register` truthfully отвечает `503 translator_gateway_unavailable`, если backend Voice Gateway недоступен
- [x] Owner panel translator-карточка теперь показывает `Подготовить companion trial`
  - orchestration-слой умеет за один owner-вызов пройти `register -> create mobile session -> bind`
  - без `device_id` новый endpoint не делает побочных side effects и честно отвечает `400 device_id_required_for_trial_prep`
  - UI не маскирует эту валидацию и показывает owner-facing ошибку напрямую
- [x] Owner panel translator-карточка теперь поддерживает полный temporary companion lifecycle
  - `POST /api/translator/mobile/remove` удаляет временный companion из registry и возвращает unified truthful snapshot
  - mobile draft-поля в UI стали `dirty-aware` и больше не теряют вручную введённый `device_id` при refresh
  - живой lifecycle `trial-prep -> trial_ready -> remove -> stop -> blocked/not_configured` подтверждён и через API, и через owner panel
- [x] Owner panel translator-карточка теперь показывает `Delivery matrix`
  - ordinary-call track и internet adapters имеют отдельные badges/status
  - панель показывает product blockers / next steps / guardrails, а не только health-check'и
  - при down Gateway ordinary/internet tracks truthfully падают в `blocked`, а не в fake-ready
- [x] Owner panel translator-карточка теперь показывает `Live trial preflight`
  - panel честно показывает helper paths для `Start Full Ecosystem.command`, Voice Gateway и Krab Ear
  - panel не пытается launch-ить сервисы из браузера, а показывает truthful следующий шаг
  - при down stack verdict падает в `stack_not_ready`, а не в generic unknown
- [x] Owner panel translator-карточка теперь показывает `Companion onboarding packet`
  - panel читает install tracks / trial profiles / onboarding checklist из backend packet
  - `subtitles-first` и `RU-ES duplex` доступны, `voice-first` truthfully disabled, пока `voice replies` ещё off
  - preset-профили реально подставляют mobile/session draft-поля и переживают `Обновить Translator`
  - кнопка export реально пишет свежий `translator_mobile_onboarding_latest.json`
- [x] USER3 runtime recovery и companion trial-ready подтверждены после controlled restart
  - legacy `agents.defaults.thinkingDefault=auto` теперь нормализуется в `adaptive`, поэтому OpenClaw 2026.3.11 больше не падает на reload и `:18789` снова поднимается штатно
  - `Check Current Account Runtime.command` и `/api/health` на `2026-03-14` подтверждают `:8080/:18789/:8090` = OK в `USER3`
  - owner panel после клика `Подготовить companion trial` показывает `iPhone companion = BOUND`, `Delivery matrix = TRIAL READY`, `Live trial preflight = READY FOR TRIAL`; при этом `current device binding status = pending`, пока iPhone ещё не вышел в живой session-stream
  - evidence: `artifacts/ops/translator_mobile_trial_ready_user3_latest.json`, `artifacts/ops/openclaw_runtime_thinking_alias_fix_user3_latest.json`, `output/playwright/translator-mobile-trial-ready-user3-20260314.png`
- [x] `Start Full Ecosystem.command` и `Stop Full Ecosystem.command` теперь устойчивы для `USER2`
  - launcher автоматически уходит в локальный Voice Gateway fallback, если внешний start-script не может писать в чужие `.gateway.pid/.log`
  - launcher автоматически уходит в direct runtime fallback для Krab Ear, если внешний start-script не может синхронизировать runtime binary
  - stop-path теперь корректно завершает и fallback-процессы, а не только штатные PID-файлы
- [x] `krab_ear_watchdog.py` теперь умеет восстанавливаться и через direct runtime fallback
  - recovery больше не зависит только от внешнего `start_agent.command`
  - watchdog ищет PID и по primary runtime, и по fallback runtime

### Этап 5. Routing / auth / quota truth

- [x] Routing diagnostics показывает truthful current route
- [x] `temporary_fallback_candidates` не схлопывает всё в одну рекомендацию
- [x] `google_antigravity_legacy_removed = false`
- [x] UI/runtime честно показывают текущий active route и fallback shortlist
- [x] Read-only auth recovery диагностика доступна и через owner panel, и через `.command`
- [x] `OpenAI Codex` подтверждён на `USER2` и виден как `OAuth OK`
- [ ] Материализовать устойчивый `Gemini CLI OAuth` store/profile или отложить это на `pablito`
- [ ] Legacy `google-antigravity` вести отдельным bypass-путём без смешения со штатным OAuth snapshot

### Этап 6. Release discipline / handoff

- [x] Есть `Release Gate.command`
- [x] Есть `pre_release_smoke.py`
- [x] Есть `r20_merge_gate.py`
- [x] Есть attach-ready handoff bundle exporter
- [x] Handoff/export тексты выровнены с текущим branch/runtime
- [x] Свежий full cycle `tests + gate + handoff bundle` уже собран

## Что уже сделано в этой ветке

- [x] Shared workspace/state поднят как first-class runtime truth
- [x] ACL policy parity доведена до owner/full разделения без ложных owner-прав у `full`
- [x] Owner panel русифицирована в рабочих owner-facing блоках без лишней перерисовки UI
- [x] Inbox / escalation layer подтверждён через owner UI
- [x] `voice_profile`, `translator_runtime`, `workspace_state` читаются через единый runtime snapshot
- [x] Truthful fallback shortlist сохранён без удаления `google-antigravity`
- [x] Reserve Telegram roundtrip автоматизирован отдельным live smoke
- [x] Persisted operator workflow уже фиксирует owner roundtrip с trace/reply trail
- [x] Owner panel теперь показывает `Auth Recovery этой учётки`, а repo хранит свежий `oauth_recovery_readiness_latest.json`
- [x] UI smoke подтверждает truthful recovery-тексты для `OpenAI Codex`, `Gemini CLI OAuth` и legacy bypass
- [x] Runtime owner truth выровнен между ACL, owner panel, runtime banner и workspace-memory (`312322764`, `p0lrd`)
- [x] Hidden reasoning trace отделён от основного ответа и доступен owner-only через `!reasoning`
- [x] Capability registry truthfully декларирует Telegram userbot как `buffered_edit_loop`, а не как полноценный provider chunk-stream
- [x] Owner panel теперь показывает truthful `Translator Readiness` по live endpoint `/api/translator/readiness`
- [x] Owner panel теперь показывает truthful translator control-plane по live endpoint `/api/translator/control-plane`
- [x] Owner panel теперь показывает truthful translator session orchestration слой
  - при `gateway_unavailable` кнопки start/policy/pause/resume/stop/tune/quick-phrase disabled
  - прямой POST `/api/translator/session/start` возвращает `503 translator_gateway_unavailable`
- [x] Owner panel теперь показывает truthful translator diagnostics inspector
  - `why-report: gateway_unavailable`
  - `timeline status: gateway_unavailable`
  - summary/escalation действия неактивны без active session
- [x] Owner panel теперь показывает truthful translator mobile companion readiness
  - `mobile status: gateway_unavailable`
  - `registry: 0 devices · push 0 · bound 0`
  - register/bind действия неактивны, пока Gateway недоступен
- [x] Owner panel теперь показывает truthful mobile trial-prep action
  - `POST /api/translator/mobile/trial-prep` подхватывается live runtime после controlled restart
  - browser smoke подтверждает новую кнопку `Подготовить companion trial`
  - безопасный live validation path без `device_id` подтверждён через `400 device_id_required_for_trial_prep`
- [x] Owner panel теперь показывает truthful translator delivery matrix
  - `ordinary calls: blocked`
  - `internet adapters: blocked`
  - UI явно объясняет почему обычный звонковый трек заблокирован и какой следующий шаг нужен
- [x] Owner panel теперь показывает truthful translator live trial preflight
  - `status: companion_pending` после live one-click recovery
  - helper paths для ecosystem/gateway/ear видны прямо в панели
  - следующий truthful blocker теперь `iPhone companion not_configured`, а не down stack
- [x] Owner panel теперь показывает truthful translator mobile onboarding packet
  - `status: ready_for_onboarding`
  - preset `subtitles-first` реально заполняет draft-поля и сохраняется после refresh
  - export из owner panel уже пишет `artifacts/ops/translator_mobile_onboarding_latest.json`

## Текущие live блокеры

- [ ] Current live route всё ещё идёт через `google/gemini-3.1-pro-preview`, а не через `openai-codex/gpt-5.4`
- [ ] `google-gemini-cli` на `USER2` сейчас не имеет устойчивого OAuth-store (`~/.gemini/oauth_creds.json` отсутствует)
- [ ] `google-antigravity` сохранён, но в штатном runtime snapshot сейчас виден только как legacy/bypass-контур
- [ ] На `iPhone 14 Pro Max` ещё нужно доставить свежую settings-сборку, чтобы on-device работали `source_lang / target_lang / Health-check`
- [ ] `iPhone 15 Pro Max` остаётся нестабильным dev-target из-за Apple/Xcode/CoreDevice tunnel reconnect
- [ ] Ordinary-call translator track теперь блокируется отсутствием зарегистрированного `iPhone companion`
- [ ] Финальный release gate на целевой учётке `pablito` ещё не перепрогнан после account-local relogin
- [ ] Для новой третьей macOS-учётки ещё нужен один bootstrap-цикл: tools, `~/.openclaw` baseline, skills и account-local auth/browser state

## Что считается подтверждением

- [x] Unit: shared workspace / capability registry / web runtime endpoints
- [x] Unit: ACL / command access parity
- [x] Unit: operator workflow / trace propagation / inbox flow
- [x] Unit: reserve roundtrip script
- [x] Live runtime: `:8080/api/health/lite`
- [x] Live runtime: `:8080/api/openclaw/model-routing/status`
- [x] Live runtime: `:8080/api/translator/readiness`
- [x] Live runtime: `:8080/api/translator/control-plane`
- [x] Live runtime: `:8080/api/translator/session/start` truthfully деградирует в `503 translator_gateway_unavailable`, если backend Voice Gateway недоступен
- [x] Live runtime: `:8080/api/translator/session-inspector`
- [x] Live runtime: `:8080/api/translator/session/summary` truthfully отвечает `400 translator_session_required`, если active session отсутствует
- [x] Live runtime: `:8080/api/translator/mobile-readiness`
- [x] Live runtime: `:8080/api/translator/mobile/register` truthfully деградирует в `503 translator_gateway_unavailable`, если backend Voice Gateway недоступен
- [x] Live runtime: `:8080/api/translator/mobile/trial-prep` безопасно валидирует `device_id` и не мутирует state без явного companion id
- [x] Live runtime: `:8080/api/translator/delivery-matrix`
- [x] Live runtime: `:8080/api/translator/live-trial-preflight`
- [x] Live ecosystem e2e: `scripts/live_ecosystem_e2e.py --require-openclaw --require-ear --require-voice-lifecycle`
- [x] Owner UI smoke: карточка `Translator Readiness` + refresh button + DOM/API parity
- [x] Owner UI smoke: session policy / runtime tuning / quick phrases в translator-карточке
- [x] Owner UI smoke: session orchestration form + disabled write-buttons при недоступном Gateway
- [x] Owner UI smoke: why-report / timeline digest / summary+escalation disabled-state в translator-карточке
- [x] Owner UI smoke: `iPhone companion` блок в translator-карточке + disabled register/bind при недоступном Gateway
- [x] Owner UI smoke: `Delivery matrix` блок в translator-карточке + truthful blocked-state для ordinary/internet tracks
- [x] Owner UI smoke: `Live trial preflight` блок в translator-карточке + truthful helper/checklist state
- [x] Owner UI smoke: `Userbot ACL` показывает truthful owner label `312322764, p0lrd` после controlled restart
- [x] Live runtime: `:8080/api/model/catalog` с `auth_recovery`
- [x] Live runtime: `:8080/api/ops/runtime_snapshot`
- [x] Live runtime: `:8080/api/inbox/status`
- [x] Live runtime: `:8080/api/userbot/acl/status`
- [x] Live runtime: `:8080/api/capabilities/registry`
- [x] Live runtime: `:8080/api/channels/capabilities`
- [x] Свежий `pre_release_smoke_latest.json`
- [x] Свежий `r20_merge_gate_latest.json`
- [x] Свежий `oauth_recovery_readiness_latest.json`
- [x] Свежий ops artifact `userbot_runtime_truth_user3_latest.json`
- [x] Свежий owner-panel screenshot `owner-userbot-truth-reasoning-smoke-20260315.png`
- [x] Свежий owner-panel screenshot `auth-recovery-readiness-owner-panel-smoke-20260314.png`
- [x] Свежий owner-panel screenshot `translator-session-orchestration-owner-panel-smoke-20260314.png`
- [x] Свежий owner-panel screenshot `translator-mobile-companion-owner-panel-smoke-20260314.png`
- [x] Свежий owner-panel screenshot `translator-mobile-trial-prep-owner-panel-smoke-20260314.png`
- [x] Свежий owner-panel screenshot `translator-delivery-matrix-owner-panel-smoke-20260314.png`
- [x] Свежий owner-panel screenshot `translator-live-trial-preflight-owner-panel-smoke-20260314.png`
- [x] Свежий owner-panel screenshot `translator-live-trial-preflight-owner-panel-smoke-post-fallback-20260314.png`
- [x] Свежий owner-panel screenshot `translator-session-inspector-owner-panel-smoke-20260314.png`
- [x] Свежий ops artifact `translator_live_trial_preflight_latest.json`
- [x] Свежий ops artifact `live_ecosystem_e2e_20260314_054155Z.json`
- [x] Свежий handoff bundle на текущем truth

## Что осталось до 100%

1. Добить всё, что не привязано к домашней директории `pablito`, включая shared-sync, truthful docs и on-device translator settings.
2. На `pablito` пройти account-local relogin через owner panel и только потом переподтвердить merge-ready тем же release-gate циклом.
3. После стабилизации translator/product layers вернуться к большим фазам master plan: `Internet Call Translation`, `Swarm v2`, `Trading Lab`, `Product Teams`, `Controlled Real Autonomy`.

## Merge gate

- [x] Кодовая база уже покрыта большим числом targeted unit/runtime тестов
- [x] Runtime truth доступна через owner web endpoints
- [ ] Merge в `main` пока преждевременен без финального account-local relogin на `pablito` и свежего release-gate после него
