"""
Канонический checkpoint для продолжения работы по ветке reserve/userbot/release-gate.

Нужен, чтобы следующий диалог стартовал от фактического состояния без реконструкции по памяти.

Важно:
- верхний блок `Обновление 2026-03-16` — текущая truth-layer для `USER3`;
- нижняя часть документа сохраняет исторический baseline от `2026-03-14`, если нужен контекст по прошлому этапу;
- текущие решения нужно принимать по верхнему блоку и свежим ops-артефактам.
"""

# Checkpoint Krab/OpenClaw

Дата: 2026-03-16
Ветка: `codex/translator-finish-gate-user3`
Ориентировочная готовность большого плана: **~52%**

## Обновление 2026-03-16: USER3 translator finish gate

- Текущая рабочая dev-учётка: `USER3`. Возврат на `pablito` пока не нужен; он остаётся финальным acceptance/release-контуром.
- Свежий truthful artifact: [artifacts/ops/translator_finish_gate_user3_latest.json](/Users/Shared/Antigravity_AGENTS/Краб/artifacts/ops/translator_finish_gate_user3_latest.json)
  - `status = automated_gate_ready_manual_step_pending`
  - `current_route_model = openai-codex/gpt-5.4` именно в момент automation-run этого gate
  - gateway regression = `17 passed`
  - iOS build/install = `ok`
  - `launch_attempt = locked`, то есть хвост сейчас не кодовый, а device/local
- Для этого блока добавлены:
  - [src/core/translator_finish_gate.py](/Users/Shared/Antigravity_AGENTS/Краб/src/core/translator_finish_gate.py)
  - [scripts/check_translator_finish_gate.py](/Users/Shared/Antigravity_AGENTS/Краб/scripts/check_translator_finish_gate.py)
  - [Check Translator Finish Gate.command](/Users/Shared/Antigravity_AGENTS/Краб/Check%20Translator%20Finish%20Gate.command)
- Параллельно закрыт memory/amnesia repair:
  - live `:8080/api/health/lite` сейчас показывает route `google-gemini-cli/gemini-3.1-pro-preview`, то есть это уже новый runtime-truth, а не тот route, который был в translator gate artifact
  - [src/core/openclaw_workspace.py](/Users/Shared/Antigravity_AGENTS/Краб/src/core/openclaw_workspace.py) теперь подмешивает хвост daily memory-файлов, а не их шумную "голову"
  - [src/openclaw_client.py](/Users/Shared/Antigravity_AGENTS/Краб/src/openclaw_client.py) теперь санирует старую chat-history/in-memory session от `think / Thinking Process`
  - добавлены repair entrypoints: [scripts/sanitize_history_cache.py](/Users/Shared/Antigravity_AGENTS/Краб/scripts/sanitize_history_cache.py) и [Repair Chat Memory Cache.command](/Users/Shared/Antigravity_AGENTS/Краб/Repair%20Chat%20Memory%20Cache.command)
  - добавлен owner-only session-clear контур: [scripts/clear_runtime_chat_session.py](/Users/Shared/Antigravity_AGENTS/Краб/scripts/clear_runtime_chat_session.py), [Clear Runtime Chat Session.command](/Users/Shared/Antigravity_AGENTS/Краб/Clear%20Runtime%20Chat%20Session.command), `POST /api/runtime/chat-session/clear`
  - persisted `chat_history:312322764` уже очищен: `bad_count = 0`
- Что реально осталось:
  - разблокировать `iPhone 14 Pro Max` и сделать короткий ручной `ru -> es` retest;
  - подтвердить, что `Recognition request was canceled` больше не всплывает после `stop/start`;
  - если во время retest снова проявится reported UX-хвост со scaling в приложении iPhone companion, зафиксировать это отдельно как регресс ручного acceptance;
  - для финального closure амнезии flush-нуть уже загруженную live session у `pablito`: safest path — один `!clear` в owner-чате или controlled restart на основной учётке;
  - новый endpoint `/api/runtime/chat-session/clear` уже в коде, но на текущем живом `:8080` потребует следующего controlled restart.
- После закрытия этого блока оптимально снова включить `Plan Mode` и уже на свежей truth-base расписать следующие фазы: `routing/auth/quota truth`, безопасный `FinOps`, и следующие roadmap-слои.

## Что уже подтверждено

- Telegram userbot работает на общем workspace `~/.openclaw/workspace-main-messaging`.
- Runtime truth по workspace/state уже видна и в web API, и в operator workflow:
  - `shared_workspace_attached = true`
  - `shared_memory_ready = true`
  - `workspace_dir = /Users/USER2/.openclaw/workspace-main-messaging`
- Owner UI на `:8080` уже умеет:
  - показывать truthful `Runtime / ACL / Inbox / Routing / Voice / Translator`;
  - эскалировать owner request в task/approval;
  - сохранять `trace_id`, `source_item_id`, `source_trace_id`.
- Отдельный owner-facing блок `Translator Readiness` уже выведен в панели и читает тот же truthful backend endpoint, а не локальный mock-state.
- ACL policy parity подтверждена:
  - `owner_only_commands = ["access", "acl", "restart", "set"]`
  - `full` не получает owner-only admin-команды.
- Настройки owner/workspace переживают restart и читаются через единый runtime snapshot:
  - `voice_profile`
  - `translator_runtime`
  - `workspace_state`
- Truthful routing shortlist уже не выкидывает `google-antigravity`.
  - `google-antigravity` и `google-gemini-cli` считаются разными квотными контурами.
  - Политика проекта: **не удалять legacy antigravity**, а держать рядом с Gemini CLI.
- Reserve transport сохранён как отдельный и менее привилегированный контур.
- Operator workflow / inbox слой уже first-class:
  - есть linked followups;
  - approval history;
  - recent replied requests;
  - trace index.

## Что было подтверждено живым runtime на 2026-03-14

- `GET /api/health/lite` возвращает:
  - `status = up`
  - `telegram_session_state = ready`
  - `telegram_userbot_state = running`
  - `scheduler_enabled = true`
- `GET /api/userbot/acl/status` возвращает truthful ACL matrix и owner-only команды.
- `GET /api/openclaw/model-routing/status` возвращает:
  - `current_primary = google/gemini-3.1-pro-preview`
  - `temporary_fallback_candidates = ["google/gemini-3.1-pro-preview"]`
  - `google_antigravity_legacy_removed = false`
- `GET /api/model/catalog` теперь дополнительно отдаёт read-only `auth_recovery` snapshot:
  - `recovery_stage = attention`
  - owner panel показывает русскую сводку `Auth Recovery этой учётки`
  - `OpenAI Codex` на `USER2` уже отражается как `OAuth OK`
  - `Gemini CLI OAuth` честно показывается как `OAuth не подтверждён`
  - legacy `google-antigravity` честно показывается как `provider plugin не загружен; bypass отдельно`
- `GET /api/ops/runtime_snapshot` подтверждает restart-proof state:
  - `voice.enabled = true`
  - `voice.delivery = voice-only`
  - `voice.speed = 1.33`
  - `voice.voice = ru-RU-SvetlanaNeural`
  - `translator.language_pair = en-ru`
  - `translator.translation_mode = auto_to_ru`
  - `translator.voice_strategy = subtitles-first`
  - `translator.internet_calls_enabled = true`
  - `translator.summary_enabled = false`
  - `translator.session_status = paused`
  - `translator.active_session_label = Restart Proof Session`
- `GET /api/translator/readiness` теперь дополнительно возвращает product-ready breakdown:
  - `foundation_checks`
  - `account_runtime`
  - `active_session`
  - `product_surface`
- `GET /api/translator/control-plane` теперь дополнительно возвращает session/policy truth:
  - `gateway_contract`
  - `sessions`
  - `current_session`
  - `runtime_policy`
  - `operator_actions`
  - `quick_phrases`
  - `approval_state`
- Translator write-layer теперь доступен через owner-facing backend endpoints:
  - `POST /api/translator/session/start`
  - `POST /api/translator/session/policy`
  - `POST /api/translator/session/action`
  - `POST /api/translator/session/runtime-tune`
  - `POST /api/translator/session/quick-phrase`
- `GET /api/translator/session-inspector` теперь возвращает:
  - `why_report`
  - `timeline`
  - `actions`
  - `escalation`
- Дополнительно появились write-endpoints для session diagnostics:
  - `POST /api/translator/session/summary`
  - `POST /api/translator/session/escalate`
- `GET /api/translator/mobile-readiness` теперь дополнительно возвращает truthful iPhone companion snapshot:
  - `summary`
  - `actions`
  - `devices`
  - `selected_device_snapshot`
- Дополнительно появились write-endpoints для companion lifecycle:
  - `POST /api/translator/mobile/register`
  - `POST /api/translator/mobile/bind`
  - `POST /api/translator/mobile/trial-prep`
  - `POST /api/translator/mobile/remove`
- `GET /api/translator/delivery-matrix` теперь дополнительно возвращает product truth по call tracks:
  - `ordinary_calls`
  - `internet_calls`
  - `guardrails`
  - `evidence`
- `GET /api/translator/live-trial-preflight` теперь дополнительно возвращает ops truth по live trial:
  - `helpers`
  - `services`
  - `translator`
  - `actions.checklist`
- `GET /api/translator/mobile/onboarding` теперь дополнительно возвращает truthful onboarding packet для нового `iPhone companion`:
  - `summary`
  - `install_tracks`
  - `trial_profiles`
  - `onboarding_contract`
  - `packet_preview`
  - `helpers`
- Дополнительно появился write-endpoint для выгрузки onboarding packet:
  - `POST /api/translator/mobile/onboarding/export`
- Живой owner UI smoke новой карточки translator readiness подтверждён:
  - manual click по `Обновить Translator` сработал
  - DOM показывает `USER2 / yung_nagato / split_runtime_per_account`
  - DOM показывает `google/gemini-3.1-pro-preview (openclaw_cloud)`
  - DOM показывает foundation checks для `Perceptor`, `Krab Voice Gateway`, `Krab Ear`, `Voice replies`, `Voice ingress`
  - screenshot: [output/playwright/translator-readiness-owner-panel-smoke-20260314.png](/Users/pablito/Antigravity_AGENTS/Краб/output/playwright/translator-readiness-owner-panel-smoke-20260314.png)
- Тот же owner UI smoke теперь подтверждает и новый control-plane слой в этой же карточке:
  - `Session policy` честно показывает `status: gateway_unavailable`, если `Krab Voice Gateway` сейчас down
  - `Runtime tuning` честно не врёт про несуществующий active session
  - `Quick phrases` честно показывает `unavailable`, когда Gateway library недоступна
  - screenshot: [output/playwright/translator-control-plane-owner-panel-smoke-20260314.png](/Users/pablito/Antigravity_AGENTS/Краб/output/playwright/translator-control-plane-owner-panel-smoke-20260314.png)
- Тот же live smoke теперь подтверждает и новый orchestration-слой в translator-карточке:
  - появились блоки `Session orchestration` и `Quick phrase test`
  - defaults синхронизируются из `operator_actions.draft_defaults`
  - при `gateway_unavailable` все write-кнопки (`start/policy/pause/resume/stop/tune/quick phrase`) честно disabled
  - direct POST `POST /api/translator/session/start` сейчас truthfully отвечает `503 translator_gateway_unavailable`
  - screenshot: [output/playwright/translator-session-orchestration-owner-panel-smoke-20260314.png](/Users/pablito/Antigravity_AGENTS/Краб/output/playwright/translator-session-orchestration-owner-panel-smoke-20260314.png)
- Тот же live smoke теперь подтверждает и diagnostics-слой в translator-карточке:
  - `Why report` честно показывает `gateway_unavailable`
  - `Timeline digest` честно показывает `status: gateway_unavailable`
  - кнопки `Пересобрать summary` и `Эскалировать в Inbox` disabled, если active session отсутствует
  - direct POST `POST /api/translator/session/summary` сейчас truthfully отвечает `400 translator_session_required`
  - screenshot: [output/playwright/translator-session-inspector-owner-panel-smoke-20260314.png](/Users/pablito/Antigravity_AGENTS/Краб/output/playwright/translator-session-inspector-owner-panel-smoke-20260314.png)
- Тот же live smoke теперь подтверждает и mobile companion-слой в translator-карточке:
  - после live recovery блок `iPhone companion` честно показывает `NOT_CONFIGURED`
  - registry показывает `0 devices · push 0 · bound 0`
  - current snapshot показывает `status: not_configured`
  - кнопка `Зарегистрировать companion` доступна, а `Привязать к session` остаётся disabled до появления device/session
  - screenshot: [output/playwright/translator-mobile-companion-owner-panel-smoke-20260314.png](/Users/pablito/Antigravity_AGENTS/Краб/output/playwright/translator-mobile-companion-owner-panel-smoke-20260314.png)
- Тот же live smoke теперь подтверждает и новый orchestration-слой `Подготовить companion trial`:
  - `POST /api/translator/mobile/trial-prep` уже подхватывается live runtime после controlled restart
  - endpoint без `device_id` честно отвечает `400 device_id_required_for_trial_prep` и не создаёт session "впустую"
  - browser click по новой кнопке в owner panel показывает owner-facing ошибку `Device ID обязателен`, а не скрытую мутацию state
  - screenshot: [output/playwright/translator-mobile-trial-prep-owner-panel-smoke-20260314.png](/Users/pablito/Antigravity_AGENTS/Краб/output/playwright/translator-mobile-trial-prep-owner-panel-smoke-20260314.png)
- Тот же live smoke теперь подтверждает и полный mobile companion lifecycle:
  - `POST /api/translator/mobile/remove` удаляет временный companion без мусора в registry
  - `POST /api/translator/session/action` c `action=stop` после remove очищает временную session и возвращает `control-plane` к `sessions.count = 0`
  - live lifecycle-artifact фиксирует переход `trial_ready -> blocked/not_configured` после cleanup: [artifacts/ops/translator_mobile_lifecycle_latest.json](/Users/pablito/Antigravity_AGENTS/Краб/artifacts/ops/translator_mobile_lifecycle_latest.json)
  - owner panel больше не теряет вручную введённый `device_id` при `Обновить Translator`: mobile draft-поля стали `dirty-aware`
  - browser smoke подтвердил полный UI-цикл `refresh preserves draft -> trial prep -> remove -> stop -> blocked`: [output/playwright/translator-mobile-lifecycle-owner-panel-smoke-20260314.png](/Users/pablito/Antigravity_AGENTS/Краб/output/playwright/translator-mobile-lifecycle-owner-panel-smoke-20260314.png)
  - краткий browser digest: [/.playwright-cli/translator-mobile-lifecycle-owner-panel-smoke-20260314.txt](/Users/pablito/Antigravity_AGENTS/Краб/.playwright-cli/translator-mobile-lifecycle-owner-panel-smoke-20260314.txt)
- Тот же live smoke теперь подтверждает и delivery-matrix слой в translator-карточке:
  - root badge честно показывает `BLOCKED`
  - `ordinary calls v1` честно показывает `BLOCKED` с blocker про незарегистрированный `iPhone companion`
  - `internet adapters` честно показывает `PLANNED` и остаётся вторым слоем после ordinary-call v1
  - guardrails прямо в панели фиксируют `companion/call-assist architecture`, а не fake-PSTN assumptions
  - screenshot: [output/playwright/translator-delivery-matrix-owner-panel-smoke-20260314.png](/Users/pablito/Antigravity_AGENTS/Краб/output/playwright/translator-delivery-matrix-owner-panel-smoke-20260314.png)
- Тот же live smoke теперь подтверждает и live-trial-preflight слой в translator-карточке:
  - после one-click recovery badge честно показывает `COMPANION_PENDING`
  - panel показывает helper paths для `Start Full Ecosystem.command`, Voice Gateway start и Krab Ear start
  - checklist и next step теперь truthfully советуют зарегистрировать `iPhone companion`
  - screenshot: [output/playwright/translator-live-trial-preflight-owner-panel-smoke-post-fallback-20260314.png](/Users/pablito/Antigravity_AGENTS/Краб/output/playwright/translator-live-trial-preflight-owner-panel-smoke-post-fallback-20260314.png)
- На `USER3` после runtime-repair и controlled restart тот же owner flow дошёл до live-ready состояния:
  - legacy `thinkingDefault=auto` в `~/.openclaw/openclaw.json` был починен до `adaptive`, после чего `:18789` снова стал healthy
  - `Check Current Account Runtime.command` подтвердил `:8080/:18789/:8090` = OK именно для `USER3`
  - клик `Подготовить companion trial` в owner panel создал session `vs_0b93dc247b1d` и привязал `iphone-dev-1`
  - блок `iPhone companion` теперь показывает `BOUND`, `Delivery matrix` = `TRIAL READY`, `Live trial preflight` = `READY FOR TRIAL`; при этом `current device` остаётся в `pending`, пока реальное iOS-приложение не подключит audio/session stream
  - screenshot: [output/playwright/translator-mobile-trial-ready-user3-20260314.png](/Users/Shared/Antigravity_AGENTS/Краб/output/playwright/translator-mobile-trial-ready-user3-20260314.png)
  - artifacts: [artifacts/ops/translator_mobile_trial_ready_user3_latest.json](/Users/Shared/Antigravity_AGENTS/Краб/artifacts/ops/translator_mobile_trial_ready_user3_latest.json), [artifacts/ops/openclaw_runtime_thinking_alias_fix_user3_latest.json](/Users/Shared/Antigravity_AGENTS/Краб/artifacts/ops/openclaw_runtime_thinking_alias_fix_user3_latest.json)
- Тот же live smoke теперь подтверждает и `Companion onboarding packet` слой в translator-карточке:
  - badge честно показывает `ONBOARDING READY`, а не placeholder
  - `subtitles-first` и `RU-ES duplex` доступны, `voice-first` truthfully disabled, пока `voice replies` не подтверждены
  - кнопка `Подставить subtitles-first` реально подставляет draft-поля `mobile/session`
  - повторный `Обновить Translator` не стирает подставленный профиль благодаря dirty-aware orchestration draft-полям
  - кнопка `Собрать onboarding packet` реально пишет свежий ops artifact:
    [artifacts/ops/translator_mobile_onboarding_latest.json](/Users/pablito/Antigravity_AGENTS/Краб/artifacts/ops/translator_mobile_onboarding_latest.json)
  - screenshot: [output/playwright/translator-mobile-onboarding-owner-panel-smoke-20260314.png](/Users/pablito/Antigravity_AGENTS/Краб/output/playwright/translator-mobile-onboarding-owner-panel-smoke-20260314.png)
  - краткий browser digest: [/.playwright-cli/translator-mobile-onboarding-owner-panel-smoke-20260314.txt](/Users/pablito/Antigravity_AGENTS/Краб/.playwright-cli/translator-mobile-onboarding-owner-panel-smoke-20260314.txt)
- Controlled ecosystem cycle `stop -> start -> check -> e2e` теперь подтверждён живыми артефактами:
  - `Start Full Ecosystem.command` на `USER2` автоматически уходит в Voice Gateway fallback, если внешний start-script ловит `permission denied` на `.gateway.pid/.log`
  - тот же launcher автоматически уходит в direct runtime fallback для `Krab Ear`, если внешний start-script не может синхронизировать runtime binary
  - `Stop Full Ecosystem.command` теперь корректно завершает и fallback-процессы
  - `scripts/live_ecosystem_e2e.py --require-openclaw --require-ear --require-voice-lifecycle` сейчас проходит зелёно
  - свежий report: [artifacts/ops/live_ecosystem_e2e_20260314_054155Z.json](/Users/pablito/Antigravity_AGENTS/Краб/artifacts/ops/live_ecosystem_e2e_20260314_054155Z.json)

## Owner / Telegram truth

- Persisted inbox уже фиксирует реальный owner inbound/outbound, а не только UI-состояние.
- В `recent_replied_requests` уже есть живой owner roundtrip:
  - `trace_id = telegram:88f60345b29c`
  - `message_id = 10472`
  - `reply_message_ids = ["10473"]`
  - `text_excerpt = "manual owner e2e. owner-manual-e2e-20260313-231109"`
- В общей workspace-memory уже записан сигнал:
  - `source = owner-userbot-e2e-manual`
  - `owner_manual_userbot_roundtrip=ok`
- Пользователь отдельно руками подтвердил, что в ответ пришла голосовая.

Важно:
- это уже хороший live evidence реального `owner -> userbot -> reply`;
- и теперь в текущем репозитории уже есть отдельный локальный evidence-файл для этого сценария.

## Reserve / transport truth

- В репозитории уже есть свежие live reserve artifacts:
  - [artifacts/live_smoke/reserve_telegram_roundtrip_20260313_013441.json](/Users/pablito/Antigravity_AGENTS/Краб/artifacts/live_smoke/reserve_telegram_roundtrip_20260313_013441.json)
  - [artifacts/live_smoke/reserve_telegram_roundtrip_20260313_013000.json](/Users/pablito/Antigravity_AGENTS/Краб/artifacts/live_smoke/reserve_telegram_roundtrip_20260313_013000.json)
- Это подтверждает, что reserve Telegram roundtrip уже заведён как отдельный живой smoke-контур.

## Свежий release-gate срез

- На `2026-03-14` сохранены как актуальный baseline:
  - [artifacts/ops/pre_release_smoke_latest.json](/Users/pablito/Antigravity_AGENTS/Краб/artifacts/ops/pre_release_smoke_latest.json)
  - [artifacts/ops/r20_merge_gate_latest.json](/Users/pablito/Antigravity_AGENTS/Краб/artifacts/ops/r20_merge_gate_latest.json)
- Итог последнего полного gate-цикла:
  - `pre_release_smoke --full --strict-runtime`: `ok=true`, `blocked=false`
  - `r20_merge_gate`: `ok=true`, `required_failed=0`, `advisory_failed=0`
- Важно:
  - после свежих auth-recovery доработок и account-local relogin на `pablito` этот gate нужно прогнать ещё раз;
  - текущие gate-артефакты считать baseline, а не финальным release verdict.
- Дополнительно пересобраны свежие local evidence:
  - [artifacts/live_smoke/live_channel_smoke_20260314_current.json](/Users/pablito/Antigravity_AGENTS/Краб/artifacts/live_smoke/live_channel_smoke_20260314_current.json)
  - [artifacts/live_smoke/owner_manual_userbot_roundtrip_20260314_runtime.json](/Users/pablito/Antigravity_AGENTS/Краб/artifacts/live_smoke/owner_manual_userbot_roundtrip_20260314_runtime.json)
- Fresh handoff bundle уже собран:
  - [artifacts/handoff_20260313_233251](/Users/pablito/Antigravity_AGENTS/Краб/artifacts/handoff_20260313_233251)
  - [artifacts/handoff_20260313_233251.zip](/Users/pablito/Antigravity_AGENTS/Краб/artifacts/handoff_20260313_233251.zip)
- Handoff/export слой теперь нужно считать и переносным пакетом для новой третьей macOS-учётки:
  - bundle должен включать `THIRD_ACCOUNT_BOOTSTRAP_RU.md`, `KRAB_SKILLS_REGISTRY_RU.md` и, если доступен, исходный файл `PLAN-Краб+переводчик 12.03.2026.md`
  - для новой учётки первым generic helper считать `Check New Account Readiness.command`

## Auth recovery diagnostics уже сделаны

- В репозитории появился безопасный read-only контур диагностики:
  - [scripts/check_oauth_recovery_readiness.py](/Users/pablito/Antigravity_AGENTS/Краб/scripts/check_oauth_recovery_readiness.py)
  - [Check OAuth Recovery Readiness.command](/Users/pablito/Antigravity_AGENTS/Краб/Check%20OAuth%20Recovery%20Readiness.command)
- Он не мутирует `~/.openclaw/*`, не включает plugins и не пытается логинить без TTY.
- Свежий snapshot уже сохранён в:
  - [artifacts/ops/oauth_recovery_readiness_latest.json](/Users/pablito/Antigravity_AGENTS/Краб/artifacts/ops/oauth_recovery_readiness_latest.json)
- Живой owner UI smoke для этого блока сохранён в:
  - [output/playwright/auth-recovery-readiness-owner-panel-smoke-20260314.png](/Users/pablito/Antigravity_AGENTS/Краб/output/playwright/auth-recovery-readiness-owner-panel-smoke-20260314.png)
- Дополнительно появился read-only helper для translator trial prep:
  - [scripts/check_translator_live_trial_preflight.py](/Users/pablito/Antigravity_AGENTS/Краб/scripts/check_translator_live_trial_preflight.py)
  - [Check Translator Live Trial Preflight.command](/Users/pablito/Antigravity_AGENTS/Краб/Check%20Translator%20Live%20Trial%20Preflight.command)
  - свежий JSON-артефакт: [artifacts/ops/translator_live_trial_preflight_latest.json](/Users/pablito/Antigravity_AGENTS/Краб/artifacts/ops/translator_live_trial_preflight_latest.json)

## Текущий truthful auth-state на USER2

- `openai-codex` на `USER2` уже подтверждён:
  - OpenClaw видит `OAuth OK`;
  - профиль живёт в `~/.openclaw/agents/main/agent/auth-profiles.json`.
- `google-gemini-cli` на `USER2` пока не материализован в устойчивый OAuth-store:
  - `~/.gemini/oauth_creds.json` отсутствует;
  - наличие отдельного `GOOGLE_API_KEY`-контура не считается подтверждением `Gemini CLI OAuth`.
- `google-antigravity` не удалён, но в штатном OpenClaw snapshot сейчас виден как отдельный legacy/bypass-контур:
  - стандартный provider plugin не загружен;
  - bypass-путь проекта должен проверяться отдельно и не смешивается с обычным OAuth recovery.

## Что остаётся реальными хвостами после обновления 2026-03-16

### 1. Translator finish gate на USER3

- Автоматическая часть milestone уже зелёная, но без нового ручного `ru -> es` retest блок нельзя считать закрытым.
- Последний CLI-launch уткнулся в `Locked`, поэтому ближайший шаг — разблокировать `iPhone 14 Pro Max` и повторить короткий on-device сценарий.
- Пользователь дополнительно сообщал о плавающем scaling-regression: приложение переводчика иногда отображалось примерно на половине экрана и мешало ручным проверкам.
  Это нужно трактовать как UX-риск acceptance, если проблема повторится на следующем retest.

### 2. Возврат на pablito

- На `pablito` ещё не воспроизведены:
  - runtime truth (`/api/health/lite`, `/api/translator/readiness`, routing/auth);
  - owner panel;
  - translator acceptance после текущих USER3-фиксов.
- Release/merge verdict в `main` по-прежнему нельзя считать финальным, пока ключевые сценарии не воспроизведены на основной учётке.

### 3. Следующий этап после переводчика

- После зелёного ручного retest оптимально включить `Plan Mode`.
- Планировать уже от свежей truth-base:
  - `routing/auth/quota truth`;
  - безопасный `FinOps` слой из research;
  - следующие roadmap-фазы без смешения с translator-tail.

## Что проверять первым в новом окне

1. `git status --short --branch`
2. `curl http://127.0.0.1:8080/api/health/lite`
3. `curl http://127.0.0.1:8080/api/translator/readiness`
4. `curl http://127.0.0.1:8080/api/openclaw/model-routing/status`
5. `curl http://127.0.0.1:8080/api/model/catalog`
6. `curl http://127.0.0.1:8080/api/ops/runtime_snapshot`
7. `artifacts/ops/translator_finish_gate_user3_latest.json`
8. если есть свежий bundle: `artifacts/handoff_<timestamp>/ATTACH_SUMMARY_RU.md`

## Рекомендуемый следующий этап

1. На `USER3` закрыть translator finish gate:
   - разблокировать `iPhone 14 Pro Max`;
   - повторить короткий `ru -> es` retest;
   - если retest зелёный, обновить ops/handoff truth.
2. Сразу после этого зафиксировать финальный flush live memory-session у `pablito`:
   - safest path: `!clear` в owner-чате;
   - альтернативно: controlled restart уже на основной учётке.
3. После translator + amnesia closure снова включить `Plan Mode` и зафиксировать следующий implementation-block уже без этих хвостов.
4. На `pablito` идти отдельным этапом:
   - воспроизвести runtime truth и owner panel;
   - затем перепрогнать `Release Gate.command`.
5. Для возврата на основную учётку использовать:
   - [docs/PABLITO_RETURN_CHECKLIST_RU.md](/Users/pablito/Antigravity_AGENTS/Краб/docs/PABLITO_RETURN_CHECKLIST_RU.md)
   - [Reclaim Runtime For Pablito.command](/Users/pablito/Antigravity_AGENTS/Краб/Reclaim%20Runtime%20For%20Pablito.command)

## Рекомендуемые настройки для следующего окна

- Глубина рассуждений: `high`
- `speed`: выключен
- Новый чат оптимален после ещё одного крупного блока, если снова начнёт раздуваться handoff-контекст

## Короткий handoff-текст для нового окна

```text
Продолжаем Krab/OpenClaw в ветке codex/translator-finish-gate-user3.

Состояние на 2026-03-16:
- готовность проекта ~52%
- текущая dev-учётка = USER3, `pablito` пока оставляем как финальный acceptance/release контур
- runtime на :8080 жив, текущий live route сейчас = `google-gemini-cli/gemini-3.1-pro-preview`
- свежий truthful artifact: `artifacts/ops/translator_finish_gate_user3_latest.json`
- автоматическая часть translator gate уже зелёная:
  - gateway regression = 17 passed
  - iOS build/install = ok
  - launch attempt = locked, то есть хвост сейчас device/local, а не кодовый
- route `openai-codex/gpt-5.4` внутри этого артефакта относится к конкретному automation-run, а не к текущему live runtime
- memory/amnesia bug уже частично закрыт:
  - persisted `chat_history:312322764` очищен от `think / Thinking Process`
  - repair tool: `Repair Chat Memory Cache.command`
- незакрытый остаток: короткий ручной `ru -> es` retest на iPhone 14 Pro Max после unlock
- нужно отдельно проверить, что `Recognition request was canceled` больше не всплывает после stop/start
- для полного closure амнезии нужен финальный flush уже загруженной live session у `pablito` (`!clear` или controlled restart)
- пользователь отдельно сообщал о плавающем scaling-regression в iPhone companion; если повторится, зафиксировать как отдельный acceptance-риск
- после closure translator + amnesia хвостов оптимально сразу включить Plan Mode и планировать следующий блок уже без этих хвостов
- финальная проверка на `pablito` всё ещё обязательна перед выводом в основной контур

Сначала прочитай:
1) docs/NEXT_CHAT_CHECKPOINT_RU.md
2) docs/OPENCLAW_KRAB_ROADMAP.md
3) docs/NEW_CHAT_BOOTSTRAP_PROMPT.md
4) artifacts/ops/translator_finish_gate_user3_latest.json
5) если есть свежий bundle: START_NEXT_CHAT.md и ATTACH_SUMMARY_RU.md

Первый шаг:
1) проверить git status
2) проверить /api/health/lite, /api/translator/readiness, /api/openclaw/model-routing/status
3) проверить `artifacts/ops/translator_finish_gate_user3_latest.json`
4) закрыть ручной translator retest и flush-нуть live memory-session
5) только потом обновлять handoff/docs
6) после этого включить Plan Mode для следующей фазы
```
