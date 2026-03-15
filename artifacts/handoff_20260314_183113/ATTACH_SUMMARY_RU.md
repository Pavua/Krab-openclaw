# Attach Summary (RU)

## Цель пакета

Передать весь контекст, артефакты и инструкции так, чтобы новый диалог
мог продолжить разработку без потерь.

## Готовность

- Проект: ~84%.
- Текущий блок (iPhone companion + live trial): 100%.

## Основные изменения в коде

- Endpoint `/api/translator/mobile/onboarding/export` теперь:
  - не падает при `Permission denied` на общий `*_latest.json`;
  - пишет `translator_mobile_onboarding_latest_{user}.json`;
  - возвращает `latest_path_effective` и ошибку записи.
- UI отображает фактический путь и ошибку, если `latest` не обновился.
- Обновлён документ multi-account про `ops latest`.
- Добавлен гайд по `Xcode Free Signing`.
- Добавлен `.command` helper для открытия iOS skeleton.
- Runtime-controls и owner UI больше не пишут legacy `thinkingDefault=auto`; теперь он нормализуется в `adaptive`, совместимый с OpenClaw 2026.3.11.
- Runtime owner truth теперь берётся из runtime ACL, а не из legacy `config.OWNER_USERNAME`.
- Hidden reasoning trace вынесен из основного ответа в отдельный owner-only debug-контур `!reasoning`.
- Telegram userbot truthfully декларирует streaming как `buffered_edit_loop`, а не как полноценный provider chunk-stream.

Ветка: `codex/companion-runtime-adaptive-fix`  
Базовая сохранённая ветка: `codex/onboarding-export-fallback` (synced с origin)  
Репозиторий: `Pavua/Krab-openclaw`

## Что проверено

- Unit tests:
  - `tests/unit/test_web_app_runtime_endpoints.py::test_build_openclaw_runtime_controls_maps_legacy_auto_to_adaptive`
  - `tests/unit/test_web_app_runtime_endpoints.py::test_model_apply_set_runtime_chain_converts_legacy_auto_to_adaptive`
  - `tests/unit/test_web_app_runtime_endpoints.py::test_build_openclaw_runtime_controls_reads_context_and_thinking`
- Runtime check:
  - `Check Current Account Runtime.command` подтвердил `:8080/:18789/:8090` = OK для `USER3`.
- Owner panel:
  - Export onboarding packet работает, путь отражается в UI.
  - Клик `Подготовить companion trial` создал session `vs_0b93dc247b1d` и привязал `iphone-dev-1`.
  - Итоговый UI статус: `BOUND` / `TRIAL READY` / `READY FOR TRIAL`, при этом `current device binding status = pending` до живого подключения iPhone.

## Live окружение (USER3)

- `:8080` owner panel — OK
- `:18789` OpenClaw — OK
- `:8090` Voice Gateway — OK (fallback start)
- `Krab Ear` — OK (fallback runtime + watchdog)
- Trial-ready evidence: `artifacts/ops/translator_mobile_trial_ready_user3_latest.json`
- ACL/runtime truth evidence: `artifacts/ops/userbot_runtime_truth_user3_latest.json`

## Что дальше

1. Закрепить live milestone в ветках и handoff для возврата в `pablito` без потерь.
2. Заменить synthetic `mobile перевод (...)` на реальный translation pipeline.
3. Дополировать iPhone UI под production-качество и затем повторить live trial уже на финальном переводческом тракте.
4. Доставить свежую settings-сборку на стабильный iPhone (`14 Pro Max`) и закрыть on-device `source_lang / target_lang / Health-check`.
5. После этого отдельно вернуться к `iPhone 15 Pro Max` как к Apple/CoreDevice blocker.

## Дополнение: Xcode automation

- Добавлен автоматический генератор локального Xcode project для iPhone companion:
  - `scripts/generate_iphone_companion_xcode_project.py`
  - `scripts/ios_companion_project_lib.py`
- Добавлены launcher'ы:
  - `Prepare iPhone Companion Xcode Project.command`
  - `Check iPhone Companion Simulator Build.command`
- Генерация идёт в локальную per-account папку `~/Projects/KrabVoiceiOS-user3`, а не в shared repo.
- Simulator build подтверждён на `iPhone 17 Pro Max`.
- Свежий artifact: `artifacts/ops/iphone_companion_xcode_project_user3_latest.json`
- Текущая ветка: `codex/iphone-companion-xcodegen-bootstrap`

## Дополнение: on-device proof

- Реальный iPhone companion на `iPhone 15 Pro Max`:
  - собран через `Xcode Free Signing`;
  - установлен на устройство;
  - доверен в `VPN & Device Management`;
  - запускается на устройстве.
- LAN-подключение к Voice Gateway подтверждено:
  - `http://192.168.0.171:8090`
- Финальный live proof подтверждён на реальном устройстве:
  - `session_id = vs_f35900861c74`;
  - `Сессия: running`;
  - `event = stt.partial`;
  - живой микрофонный uplink (`mobile_chunk#... speech=True`);
  - частичные обновления `Перевод`;
  - штатная остановка с `event = call.closed`.
- Это значит, что end-to-end live pipeline уже работает на устройстве, а текущий текст `RU: mobile перевод (...)` пока остаётся synthetic/diagnostic уровнем.

## Обновлённая готовность

- Проект: ~84%.
- Текущий блок (iPhone companion + live trial): 100%.


## Дополнение: fast-follow UI patch

- В отдельном рабочем клоне `Krab Voice Gateway` создана ветка `codex/iphone-companion-ui-fastfollow`.
- Патч правит `ios/KrabVoiceiOS/ContentView.swift`:
  - `ScrollView` для `Live`-экрана;
  - secondary buttons больше не должны прятаться под tab bar;
  - dismiss клавиатуры;
  - консистентный lowercase `device_id`.
- Сборка патча подтверждена:
  - `xcodegen generate`
  - `xcodebuild -destination "platform=iOS Simulator,name=iPhone 17 Pro Max" build`
  - результат: `BUILD SUCCEEDED`.

## Дополнение: live audio uplink patch

- В той же ветке `codex/iphone-companion-ui-fastfollow` добавлен mobile audio uplink.
- Ключевой commit в `Krab Voice Gateway`:
  - `f1e39a4 feat: add iphone companion audio uplink`
- Что добавлено:
  - endpoint `POST /v1/mobile/sessions/{session_id}/audio-chunk`;
  - захват микрофона через `AVAudioEngine`;
  - отправка audio chunks из iPhone companion в gateway;
  - публикация `stt.partial` и `translation.partial` для mobile trial.
- Проверки:
  - backend tests = `14 passed`;
  - simulator build = `BUILD SUCCEEDED`;
  - on-device live trial = успешен.


## Дополнение: fallback acceptance на iPhone 14 Pro Max

- Из-за Apple/Xcode `CoreDevice tunnel` проблемы на `iPhone 15 Pro Max` acceptance был безопасно перенесён на `iPhone 14 Pro Max`.
- На `iPhone 14 Pro Max` подтверждены:
  - live session creation;
  - `event = stt.partial`;
  - отображение русского текста в `Оригинал` прямо на экране устройства.
- Последняя подтверждённая session:
  - `vs_a19cf4481acd`
- `Health-check` на старой on-device сборке работал HTTP-уровнем, но почти не давал видимого UI feedback.
- Для этого уже сделан follow-up фикс:
  - новый `gatewayHealthText` в статусной карточке;
  - resume через `session snapshot` после `Health-check`/`Ре-регистрации`.
- Фикс сохранён в `Krab Voice Gateway`:
  - ветка `codex/iphone-companion-ui-fastfollow`
  - commit `4fe1c87`
- Simulator build нового фикса зелёный: `BUILD SUCCEEDED`.
- Свежая settings-сборка затем повторно переустановлена на рабочий `iPhone 14 Pro Max` через wireless `devicectl`.
- Источник установленной `.app`:
  - `/Users/USER3/Library/Developer/Xcode/DerivedData/KrabVoice-dmarxbzrqkaskrhgnlpjivptmwnu/Build/Products/Debug-iphoneos/KrabVoice.app`
- CLI launch этой новой сборки упёрся только в `Locked`, то есть install уже не блокер.
- Значит следующий практический шаг стал уже узким:
  - вручную открыть свежую сборку на `14 Pro Max`;
  - проверить интерактивность `translation_mode / source_lang / target_lang / Health-check`;
  - затем снять on-device `ru -> es` proof.


## Дополнение: mobile ru/es translation uplift

- В gateway добавлен phrase-based translation uplift для mobile live partials (`ru -> es`, `es -> ru`).
- Это заменяет прежний префиксный fallback на более осмысленный текст без внешних API ключей.
- Live proof через HTTP/gateway:
  - `Привет, проверка связи, завтра отправить договор`
  - `Hola, prueba de conexión, mañana enviar contrato`
- Клиентский UI уже умеет показывать эти partial-события; для проверки достаточно выставить `target_lang = es` в работающей on-device сборке.

## Дополнение: owner truth / hidden reasoning / truthful streaming

- Owner truth в `USER3` выровнен:
  - runtime ACL owner = `312322764`, `p0lrd`
  - owner panel `Userbot ACL` теперь показывает те же субъекты
  - runtime banner после restart тоже показывает truthful owner label
- В `USER3` дополнительно восстановлен runtime owner-context:
  - `/Users/USER3/.openclaw/workspace-main-messaging/USER.md`
  - `/Users/USER3/.openclaw/workspace-main-messaging/memory/2026-03-15.md`
- В userbot введён owner-only debug-контур reasoning:
  - мысли извлекаются из `<think>` или plain-text `Thinking Process`
  - в основной ответ они больше не должны попадать
  - отдельный доступ идёт через `!reasoning`
  - очистка — `!reasoning clear`
- Semantics registry теперь truthful:
  - `telegram_userbot.streaming = buffered_edit_loop`
  - `telegram_userbot.reasoning_visibility = owner_optional_separate_trace`
- Live evidence:
  - `artifacts/ops/userbot_runtime_truth_user3_latest.json`
  - `output/playwright/owner-userbot-truth-reasoning-smoke-20260315.png`
- Selective test suite:
  - `./venv/bin/python -m pytest tests/unit/test_access_control.py tests/unit/test_userbot_privacy_guards.py tests/unit/test_userbot_voice_flow.py tests/unit/test_capability_registry.py tests/unit/test_capability_registry_web_endpoints.py -q`
  - `45 passed`


## Дополнение: FinOps / Deep Research

- В handoff вынесены две прикладные выжимки из отдельных deep research отчётов:
  - `OPENCLAW_FINOPS_APPLICABILITY_RU.md`
  - `OPENCLAW_FINOPS_SECOND_OPINION_RU.md`
- Их лучше обсуждать в новом диалоге уже в режиме планирования, потому что следующий логичный шаг — перевод идей в конкретные routing / cost / local-first изменения, а не просто обзор текста.
