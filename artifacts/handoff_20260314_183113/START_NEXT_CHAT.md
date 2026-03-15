# Старт следующего чата (Krab / OpenClaw)

Дата: `2026-03-14`

Этот пакет подготовлен в учетке `USER3` и нужен, чтобы без потерь продолжить разработку
в новом диалоге и/или другой macOS-учётке с оплаченной квотой.

## Краткий статус

- Runtime запущен в `USER3`.
- Порты живы: `:8080` (owner panel), `:18789` (OpenClaw), `:8090` (Voice Gateway).
- Voice Gateway поднят через fallback (нет прав на `.gateway.pid` и `gateway.log` в shared repo).
- Krab Ear поднят через fallback runtime binary, watchdog активен.
- Owner: `312322764, p0lrd` (truth теперь берётся из runtime ACL, а не из legacy fallback).
- Translator readiness: `READY`, Voice replies: `ON`.
- iPhone companion зарегистрирован: `device_id = iphone-dev-1`.
- Legacy `agents.defaults.thinkingDefault=auto` в `USER3` починен до `adaptive`, поэтому `:18789` снова healthy после controlled restart.
- Companion на `iPhone 15 Pro Max` уже прошёл реальный live trial.
- Подтверждён рабочий session/audio loop: `vs_f35900861c74`, `stt.partial`, `translation.partial`, `call.closed`.
- Delivery matrix = `TRIAL READY`, а on-device live proof уже снят и зафиксирован.
- Push token по-прежнему отсутствует и это ожидаемо для free signing / первого trial.
- Primary Telegram userbot truthfully помечен как `buffered_edit_loop`, а не как полноценный provider chunk-stream.
- Hidden reasoning trace больше не должен протекать в основной ответ: он вынесен в отдельный owner-only debug-контур.

## Что исправлено в коде

1) Экспорт onboarding packet больше не падает при `Permission denied` на общий `*_latest.json`.
   Теперь пишется `translator_mobile_onboarding_latest_{user}.json` и UI показывает фактический путь.
2) UI показывает путь к реально сохранённому onboarding packet + текст ошибки,
   если общий `latest` не обновился.
3) Документация обновлена про `ops latest` в multi-account.
4) Runtime-controls больше не пишут legacy `thinkingDefault=auto`: owner UI и backend нормализуют его в `adaptive`, совместимый с OpenClaw 2026.3.11.

Ветка: `codex/companion-runtime-adaptive-fix`  
Базовая сохранённая ветка: `codex/onboarding-export-fallback` (HEAD = origin, не потеряна)

## Артефакты и доказательства

- Скрин owner panel (export + updated UI):
  - `artifacts/krab-owner-panel-onboarding-export-2026-03-14.png`
- Скрин owner panel (companion ATTENTION):
  - `artifacts/krab-owner-panel-companion-attention-2026-03-14.png`
- Скрин owner panel (companion trial ready / session bound):
  - `artifacts/krab-owner-panel-companion-trial-ready-2026-03-14.png`
- Экспорт onboarding packet (USER3 fallback latest):
  - `artifacts/ops/translator_mobile_onboarding_latest_user3.json`
- Артефакт trial-ready snapshot (USER3):
  - `artifacts/ops/translator_mobile_trial_ready_user3_latest.json`
- Артефакт runtime alias-fix (USER3):
  - `artifacts/ops/openclaw_runtime_thinking_alias_fix_user3_latest.json`
- Артефакт truthful owner/runtime snapshot (USER3):
  - `artifacts/ops/userbot_runtime_truth_user3_latest.json`
- Скрин owner panel (ACL / reasoning / streaming truth):
  - `output/playwright/owner-userbot-truth-reasoning-smoke-20260315.png`

## Следующий фокус

1) Зафиксировать и не потерять текущий on-device/live-audio milestone при возврате в `pablito`.
2) Заменить synthetic `mobile перевод (...)` на реальный translation pipeline.
3) Дополировать `Live`-экран iPhone companion под production-качество: safe area, масштаб, финальная компоновка.
4) После этого повторить live trial уже на финальном переводческом тракте.

## Что приложить в новый диалог

Просто приложи папку этого handoff:

`/Users/Shared/Antigravity_AGENTS/Краб/artifacts/handoff_20260314_183113`

Если новый аккаунт Codex не видит skills, нужно перенести:

- `cp -a /Users/USER3/.codex/skills ~/.codex/`
- убедиться, что `context7` активен в `~/.lmstudio/mcp.json`

Дополнительно по желанию:
- `artifacts/ops/translator_mobile_onboarding_latest_user3.json`
- последние скриншоты (они уже в этом handoff)

## Важные напоминания

- Не выключать runtime на `pablito`, если он снова нужен; перед стартом здесь его нужно остановить.
- Для free signing PushKit токен не обязателен; даже без него `trial_ready`/`bound` уже достижимы, а device proof идёт следующим шагом.
- OAuth не удаляем и не обрезаем — все профили должны остаться.

## Обновление 2026-03-14 19:37: Xcode bootstrap automation

- Создан новый рабочий поток для Xcode free signing без ручного создания проекта в UI.
- Локальный per-account Xcode project теперь генерируется автоматически в `~/Projects/KrabVoiceiOS-user3`.
- Добавлены one-click launcher'ы:
  - `Prepare iPhone Companion Xcode Project.command`
  - `Check iPhone Companion Simulator Build.command`
- Генератор использует `xcodegen`, подключает shared skeleton из `../Krab Voice Gateway/ios/KrabVoiceiOS`,
  пишет локальный `project.yml`, создаёт `KrabVoice.xcodeproj` и проходит simulator build.
- Simulator smoke подтверждён: `iPhone 17 Pro Max` build = OK.
- Xcode project открыт автоматически; окно проекта видно в Xcode (`KrabVoice — KrabVoice.xcodeproj`).
- Ops-артефакт:
  - `artifacts/ops/iphone_companion_xcode_project_user3_latest.json`
- Скриншот Xcode bootstrap:
  - `output/xcode/iphone-companion-xcode-bootstrap-user3-20260314.png`

Текущая ветка: `codex/iphone-companion-xcodegen-bootstrap`
Предыдущая ветка с runtime/trial repair: `codex/companion-runtime-adaptive-fix`

## Обновление 2026-03-14 20:08: on-device milestone подтверждён

- Реальный `iPhone 15 Pro Max` успешно прошёл `Free Signing`, install и first launch.
- Developer trust на устройстве подтверждён, приложение `KrabVoice` запускается на iPhone.
- В приложении выставлен `Gateway URL = http://192.168.0.171:8090`.
- Voice Gateway сейчас доступен и по localhost, и по LAN:
  - `http://127.0.0.1:8090/health` -> `ok`
  - `http://192.168.0.171:8090/health` -> `ok`
- С телефона создана реальная live session:
  - `session_id = vs_4ad7901164f1`
  - `event = call.state`
- Device/session привязка доведена вручную на gateway:
  - `device_id = 9add3a18-eb7a-4ca6-8f47-f2e0eecf0cb0`
  - `session_id = vs_4ad7901164f1`
- Session snapshot truth:
  - `active_session = true`
  - `timeline_count = 0`
  - `why.code = no_audio_stream`

## Текущий честный блокер

- Это уже не проблема Xcode, trust, сети или signing.
- Текущий блокер: в session пока не приходит живой аудиопоток с iPhone companion.
- Поэтому субтитры пустые, а WebSocket закрывается после стартового обмена.

## Что делать следующим агентом

1. Обновить/сохранить docs и acceptance artifacts после fast-follow патча.
2. Починить iPhone UI в `../Krab Voice Gateway/ios/KrabVoiceiOS/ContentView.swift`:
   - `ScrollView` для `Live`-экрана;
   - secondary buttons над tab bar;
   - dismiss клавиатуры.
3. Затем переходить к audio uplink:
   - разрешение микрофона;
   - удержание WS;
   - доставка аудиокадров в gateway.

## Свежие артефакты

- `artifacts/ops/iphone_companion_on_device_status_user3_latest.json`
- `output/xcode/iphone-build-succeeded-user3-20260314.png`
- `output/xcode/iphone-launch-attempt-user3-20260314.png`


## Обновление 2026-03-14 21:28: fast-follow UI patch

- Для следующего шага уже подготовлен UI fast-follow патч в отдельной ветке репозитория `Krab Voice Gateway`:
  - ветка: `codex/iphone-companion-ui-fastfollow`
  - commit: `55dd0c0`
- Патч делает `Live`-экран пригоднее для `iPhone 15 Pro Max`:
  - `ScrollView`;
  - secondary buttons над tab bar;
  - dismiss клавиатуры;
  - консистентный lowercase `device_id`.
- Simulator build на локальном per-account проекте подтверждён: `BUILD SUCCEEDED`.

## Обновление 2026-03-14 21:58: live audio uplink proof

- Блок `iPhone companion + live trial` фактически доведён до рабочего on-device proof.
- На реальном `iPhone 15 Pro Max` подтверждены:
  - `session_id = vs_f35900861c74`;
  - `Сессия: running`;
  - `event = stt.partial`;
  - живой uplink микрофона (`mobile_chunk#... speech=True`);
  - живые partial updates в `Оригинал` и `Перевод`;
  - штатная ручная остановка с `event = call.closed`.
- Текущий `Перевод` пока synthetic/diagnostic (`RU: mobile перевод (...)`), но сам live pipeline уже работает end-to-end.
- Главный текущий результат: Xcode/signing/network/audio loop больше не блокеры.
- Актуальный ops-артефакт:
  - `artifacts/ops/iphone_companion_on_device_status_user3_latest.json`



## Обновление 2026-03-15 05:24: fallback acceptance на iPhone 14 Pro Max

- `iPhone 15 Pro Max` остаётся нестабильным как dev-target: Xcode/CoreDevice периодически падает на tunnel reconnect.
- Чтобы не блокировать проект, acceptance успешно перенесён на `iPhone 14 Pro Max` (`00008120-001C58983C00C01E`).
- На `14 Pro Max` подтверждён рабочий live сценарий:
  - устройство регистрируется;
  - session создаётся;
  - `event = stt.partial`;
  - русский текст виден прямо в UI.
- Последняя подтверждённая session на `14 Pro Max`:
  - `vs_a19cf4481acd`
- Текущий follow-up фикс в iOS-клиенте:
  - resume через `session snapshot`;
  - отдельный visible status для `Health-check` (`gatewayHealthText`).
- Фикс уже в `Krab Voice Gateway`:
  - ветка `codex/iphone-companion-ui-fastfollow`
  - commit `4fe1c87`
- Simulator build для этого фикса: `BUILD SUCCEEDED`.
- On-device install именно этой свежей сборки на `14 Pro Max` через `devicectl` упёрся в Apple service `com.apple.remote.installcoordination_proxy`, но рабочая сборка на устройстве уже показывает русский live transcript.

## Обновление 2026-03-15 20:00: fresh settings-fix build delivered to iPhone 14 Pro Max

- Follow-up фикс интерактивности настроек (`@Published + UserDefaults`, `safeAreaInset` для сервисных кнопок, `.menu` picker style) уже не только собран, но и повторно установлен на рабочий `iPhone 14 Pro Max`.
- Источник установленной `.app`:
  - `/Users/USER3/Library/Developer/Xcode/DerivedData/KrabVoice-dmarxbzrqkaskrhgnlpjivptmwnu/Build/Products/Debug-iphoneos/KrabVoice.app`
- Wireless install через `devicectl` снова успешен:
  - bundle id `com.antigravity.krabvoice.user3.macbook.pro.pablito.local`
  - version `0.2.0`
- CLI launch этой новой сборки не завершился только потому, что устройство оказалось заблокировано в момент запуска (`Locked`).
- Значит текущий оставшийся шаг для нового чата уже узкий и понятный:
  - вручную открыть эту свежую сборку на `14 Pro Max`;
  - подтвердить on-device интерактивность `translation_mode / source_lang / target_lang / Health-check`;
  - затем сразу снять живой `ru -> es` proof.


## Обновление 2026-03-15 05:52: mobile ru/es translation uplift

- В `Krab Voice Gateway` улучшен mobile translation helper: теперь partial-перевод для `ru/es` идёт не через голый echo/prefix, а через phrase-based rules.
- Это уже даёт осмысленный перевод без внешнего cloud API.
- Live proof на gateway:
  - session `vs_314de69629f5`
  - `Привет, проверка связи, завтра отправить договор` -> `Hola, prueba de conexión, mañana enviar contrato`
- iPhone-клиент для этого шага переустанавливать не нужно: uplift серверный.
- Оставшийся follow-up: вручную открыть уже доставленную свежую сборку на `14 Pro Max` и добить on-device verification `source_lang / target_lang / Health-check / ru -> es`.


## Обновление 2026-03-15 19:12: owner truth / hidden reasoning / truthful streaming

- Runtime owner truth в `USER3` больше не зависит от legacy `config.OWNER_USERNAME`.
  Теперь owner берётся из runtime ACL:
  - `312322764`
  - `p0lrd`
- Owner panel `Userbot ACL` live-smoke подтверждён:
  - в UI видно `Owner: 312322764, p0lrd`
  - `Refresh ACL` больше не возвращает `—` для owner после controlled restart
- API truth подтверждён:
  - `GET /api/userbot/acl/status`
  - `GET /api/capabilities/registry`
  - `GET /api/channels/capabilities`
- Primary Telegram userbot теперь честно декларирует streaming semantics как:
  - `buffered_edit_loop`
  - а не ложный `confirmed`
- Hidden reasoning trace вынесен из основного ответа:
  - reasoning извлекается из `<think>` или plain-text `Thinking Process`
  - сохраняется отдельно как owner-only trace
  - читается отдельной командой `!reasoning`
  - очищается через `!reasoning clear`
- Runtime banner после restart теперь показывает truthful owner label:
  - `Owner: 312322764, p0lrd`

## Обновление 2026-03-15 20:28: USER3 owner Telegram Web browser truth

- ACL/runtime truth в `USER3` уже исправлены, но browser-based owner Telegram E2E на этой учётке пока блокируется не правами, а отсутствием корректного owner browser profile.
- Живой browser smoke показал:
  - repo profile `/Users/Shared/Antigravity_AGENTS/Краб/browser_data` логинен как `Yung Nagato`, то есть это не owner profile;
  - отчёт: `/Users/Shared/Antigravity_AGENTS/Краб/artifacts/live_smoke/owner_userbot_roundtrip_20260315_201703.json`
- Отдельная проверка стандартного Chrome profile `USER3` показала, что Telegram Web там вообще не залогинен:
  - screenshot: `/Users/Shared/Antigravity_AGENTS/Краб/output/playwright/owner-telegram-profile-default-probe.png`
  - состояние: `QR login required`
- Следствие:
  - жалоба userbot на `недостаточно прав` не подтверждается как текущий ACL blocker;
  - текущий blocker browser-owner E2E в `USER3` — это именно отсутствие owner Telegram Web session.
- Правильный fallback для этой учётки:
  - `scripts/live_owner_manual_userbot_roundtrip.py`
- Truth-артефакт:
  - `/Users/Shared/Antigravity_AGENTS/Краб/artifacts/ops/userbot_owner_browser_profile_user3_latest.json`
- Runtime owner-context в `USER3` дополнительно зафиксирован в:
  - `/Users/USER3/.openclaw/workspace-main-messaging/USER.md`
  - `/Users/USER3/.openclaw/workspace-main-messaging/memory/2026-03-15.md`

### Проверки

- Selective unit suite:
  - `./venv/bin/python -m pytest tests/unit/test_access_control.py tests/unit/test_userbot_privacy_guards.py tests/unit/test_userbot_voice_flow.py tests/unit/test_capability_registry.py tests/unit/test_capability_registry_web_endpoints.py -q`
  - результат: `45 passed`
- Live owner panel smoke:
  - `http://127.0.0.1:8080/`
  - DOM показывает `Owner: 312322764, p0lrd`
- Truth snapshot:
  - `artifacts/ops/userbot_runtime_truth_user3_latest.json`

### Следующий разумный фокус после этого handoff

1. Доставить свежую iOS-сборку settings-fix на рабочий device (`14 Pro Max`) и закрыть on-device `source_lang / target_lang / Health-check`.
2. Подтвердить живой `ru -> es` partial translation прямо на устройстве.
3. Затем вернуться к `iPhone 15 Pro Max` как к отдельному Apple/CoreDevice blocker, а не как к blocker всего проекта.


## Дополнение: Deep Research / FinOps для нового планирования

- В handoff добавлены две прикладные выжимки по оптимизации расходов и routing:
  - `OPENCLAW_FINOPS_APPLICABILITY_RU.md`
  - `OPENCLAW_FINOPS_SECOND_OPINION_RU.md`
- В новом диалоге их лучше разбирать уже в planning-mode и переводить в конкретные route / cost / local-first решения для Krab / OpenClaw.
- Важный общий вывод из обоих отчётов: экономия должна идти через truthful routing, deterministic/runtime-first paths, compaction/context hygiene и event-driven loops, а не через попытку сделать consumer web-провайдеры production truth.
