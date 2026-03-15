# Старт следующего чата (Krab / OpenClaw)

Дата: `2026-03-14`

Этот пакет подготовлен в учетке `USER3` и нужен, чтобы без потерь продолжить разработку
в новом диалоге и/или другой macOS-учётке с оплаченной квотой.

## Краткий статус

- Runtime запущен в `USER3`.
- Порты живы: `:8080` (owner panel), `:18789` (OpenClaw), `:8090` (Voice Gateway).
- Voice Gateway поднят через fallback (нет прав на `.gateway.pid` и `gateway.log` в shared repo).
- Krab Ear поднят через fallback runtime binary, watchdog активен.
- Owner: `@yung_nagato`.
- Translator readiness: `READY`, Voice replies: `ON`.
- iPhone companion зарегистрирован: `device_id = iphone-dev-1`.
- Legacy `agents.defaults.thinkingDefault=auto` в `USER3` починен до `adaptive`, поэтому `:18789` снова healthy после controlled restart.
- Companion на `iPhone 15 Pro Max` уже прошёл реальный live trial.
- Подтверждён рабочий session/audio loop: `vs_f35900861c74`, `stt.partial`, `translation.partial`, `call.closed`.
- Delivery matrix = `TRIAL READY`, а on-device live proof уже снят и зафиксирован.
- Push token по-прежнему отсутствует и это ожидаемо для free signing / первого trial.

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


## Обновление 2026-03-15 05:52: mobile ru/es translation uplift

- В `Krab Voice Gateway` улучшен mobile translation helper: теперь partial-перевод для `ru/es` идёт не через голый echo/prefix, а через phrase-based rules.
- Это уже даёт осмысленный перевод без внешнего cloud API.
- Live proof на gateway:
  - session `vs_314de69629f5`
  - `Привет, проверка связи, завтра отправить договор` -> `Hola, prueba de conexión, mañana enviar contrato`
- iPhone-клиент для этого шага переустанавливать не нужно: uplift серверный.
- Оставшийся follow-up: доставить на устройство свежую сборку с явным `gatewayHealthText`, когда Apple install service снова позволит on-device reinstall.
