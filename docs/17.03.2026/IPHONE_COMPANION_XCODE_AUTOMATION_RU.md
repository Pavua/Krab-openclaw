# iPhone companion: автоматизация Xcode bootstrap

Дата: `2026-03-14`

Этот документ фиксирует новый per-account сценарий для iPhone companion:
shared-репозиторий остаётся общим, а локальный Xcode project создаётся отдельно
для текущей macOS-учётки без конфликтов между `USER3`, `pablito` и другими аккаунтами.

## Что добавлено

- `scripts/generate_iphone_companion_xcode_project.py`
- `scripts/ios_companion_project_lib.py`
- `Prepare iPhone Companion Xcode Project.command`
- `Check iPhone Companion Simulator Build.command`

## Как это работает

1. Берётся SwiftUI skeleton из `../Krab Voice Gateway/ios/KrabVoiceiOS`.
2. В текущей учётке создаётся локальная папка:
   - `~/Projects/KrabVoiceiOS-<user>`
3. В неё записывается `project.yml` для `xcodegen` и локальная `README_RU.md`.
4. Через `xcodegen` собирается `.xcodeproj`.
5. Затем запускается simulator build без code signing.
6. При вызове `Prepare iPhone Companion Xcode Project.command` проект ещё и открывается в Xcode.

## Почему это важно для multi-account режима

- репозиторий в `/Users/Shared/Antigravity_AGENTS/Краб` не засоряется user-specific Xcode metadata;
- signing и локальные Xcode user state живут в домашней папке текущей учётки;
- при возврате в `pablito` можно заново сгенерировать локальный проект без конфликтов с `USER3`.

## Что автоматизировано

- генерация bundle id по user+host;
- запись usage permissions в Info.plist;
- создание локального Xcode project;
- simulator smoke build;
- запись ops-артефакта в `artifacts/ops`.

## Что пока остаётся ручным шагом

- выбор `Team = Personal Team` в Xcode;
- первый запуск на реальном iPhone;
- подтверждение `Trust Developer` на устройстве;
- замена `Gateway URL` с `127.0.0.1` на IP текущего Mac.

## Быстрый запуск

- Подготовить и открыть проект:
  - `Prepare iPhone Companion Xcode Project.command`
- Прогнать только simulator smoke:
  - `Check iPhone Companion Simulator Build.command`

## Ожидаемый результат

После запуска генератора должен появиться свежий ops-артефакт вида:

- `artifacts/ops/iphone_companion_xcode_project_<user>_latest.json`

Он фиксирует:
- путь до локального проекта;
- bundle id;
- имя simulator target;
- прошёл ли simulator build;
- был ли проект открыт в Xcode.

## Текущий on-device блокер

- Обнаружен реальный iPhone: `iPhone 15 ProMax P` (`00008130-001C6D0C26F8001C`).
- Personal Team в Xcode найден автоматически: `Pavel Sergeevich (Personal Team)` / `J4F83MNBD9`.
- На шаге device build Xcode упирается в системную блокировку `The device is passcode protected` (`E800001A`).
- Это не кодовый баг и не проблема проекта: нужно просто держать iPhone разблокированным во время первой установки из Xcode.
- Артефакт блокера: `artifacts/ops/iphone_companion_device_build_blocker_user3_latest.json`

## Статус первого on-device прогона

- Tunnel и DDI после reboot iPhone восстановлены: устройство перешло в `connected`, `isUsable = true`.
- Free-signing build на реальном `iPhone 15 Pro Max` успешно завершён.
- Приложение установлено на устройство: `com.antigravity.krabvoice.user3.macbook.pro.pablito.local`.
- Первый launch блокируется системной защитой iOS, пока профиль разработчика не доверен вручную.
- Что нужно на iPhone: `Настройки -> Основные -> VPN и управление устройством -> Developer App -> Trust`.
- После trust можно повторно запустить приложение через `devicectl` или Xcode без новой сборки.
- Артефакт статуса: `artifacts/ops/iphone_companion_on_device_status_user3_latest.json`

## Обновление 2026-03-14 20:08: live session proof

- Приложение на `iPhone 15 Pro Max` не только запускается, но и доходит до создания live session.
- Подтверждён реальный `session_id`: `vs_4ad7901164f1`.
- После ручной правки `Gateway URL` на LAN-адрес Mac:
  - `http://192.168.0.171:8090`
  companion смог достучаться до Voice Gateway по сети.
- Проверка на Mac подтверждает оба health-endpoint:
  - `http://127.0.0.1:8090/health` -> `ok`
  - `http://192.168.0.171:8090/health` -> `ok`
- Для этой сессии вручную подтверждена и привязка device/session:
  - `device_id = 9add3a18-eb7a-4ca6-8f47-f2e0eecf0cb0`
  - `session_id = vs_4ad7901164f1`
- Session snapshot на gateway показывает:
  - `active_session = true`
  - `status = created`
  - `timeline_count = 0`
  - `why.code = no_audio_stream`

## Что это означает

- `Xcode Free Signing` завершён успешно.
- iPhone companion установлен, доверен и запускается на устройстве.
- Подключение к Voice Gateway по локальной сети работает.
- Live session создаётся с телефона и видна на gateway.
- Текущий фактический блокер сместился с сети/подписи на аудиотракт:
  в сессию пока не поступает микрофонный поток, поэтому субтитры пустые,
  а WebSocket закрывается после стартового обмена.

## Fast-follow задачи

- Поднять UX `Live`-экрана на `iPhone 15 Pro Max`:
  - secondary buttons сейчас уезжают под tab bar;
  - нужен надёжный dismiss клавиатуры;
  - экран лучше перевести на `ScrollView` с safe-area отступом.
- Довести uplink живого аудио:
  - запрос/проверка разрешения на микрофон;
  - удержание WS после создания session;
  - реальная отправка аудиокадров в gateway.


## Обновление 2026-03-14 21:28: fast-follow UI patch

- Для iPhone companion подготовлен отдельный fast-follow патч в репозитории `Krab Voice Gateway`.
- Изменения сделаны в `ios/KrabVoiceiOS/ContentView.swift`.
- Что улучшено:
  - `Live`-экран переведён на `ScrollView`, чтобы сервисные кнопки не уезжали под tab bar на `iPhone 15 Pro Max`;
  - добавлен dismiss клавиатуры по tap/scroll;
  - URL/API fields теперь настроены под более предсказуемый ввод на iPhone;
  - `device_id` нормализуется в lowercase консистентно, чтобы регистрация и bind работали с одним и тем же идентификатором.
- Проверка:
  - локальный клон `Krab Voice Gateway` собран через `xcodegen + xcodebuild` для `iPhone 17 Pro Max Simulator`;
  - итог: `BUILD SUCCEEDED`.

## Обновление 2026-03-14 21:58: live audio uplink proof

- Fast-follow патч и новый gateway с mobile audio endpoint доведены до реального on-device proof.
- На `iPhone 15 Pro Max` повторно установлен и запущен обновлённый `KrabVoice`.
- Подтверждён рабочий live pipeline:
  - `session_id = vs_f35900861c74`
  - `status = running`
  - `event = stt.partial`
  - `Оригинал = mobile_chunk#... speech=True`
  - `Перевод = RU: mobile перевод (...)`
- Пользователь проговорил в микрофон около 20 секунд, после чего сессия была остановлена вручную.
- После ручной остановки companion корректно показал:
  - `Сессия остановлена`
  - `event = call.closed`
- Это означает, что теперь фактически подтверждены:
  - `Xcode Free Signing`;
  - install + trust + launch на устройстве;
  - LAN-подключение к `Voice Gateway`;
  - создание live session;
  - uplink микрофонного аудио;
  - `stt.partial` и `translation.partial` события;
  - корректное завершение `call.closed`.

## Что ещё осталось довести

- Текущий `Перевод` пока diagnostic/synthetic (`mobile перевод (...)`), а не финальный production-quality pipeline.
- UI `Live`-экрана уже стал лучше, но ещё требует аккуратного production polish по safe area и масштабу элементов.
- Следующий инженерный фокус: заменить synthetic translation на реальный pipeline и дочистить iPhone UI.



## Обновление 2026-03-15 05:24: iPhone 14 Pro Max fallback acceptance

- Из-за нестабильного `CoreDevice tunnel` на `iPhone 15 Pro Max` acceptance продолжен на `iPhone 14 Pro Max` (`00008120-001C58983C00C01E`).
- На `iPhone 14 Pro Max` подтверждено:
  - приложение устанавливается и запускается;
  - `Gateway URL = http://192.168.0.171:8090` работает по LAN;
  - `Ре-регистрация устройства` отрабатывает;
  - создаётся живая сессия (`session_id = vs_a19cf4481acd`);
  - на экране отображается реальный русский `stt.partial`.
- Скриншоты подтверждают, что `Оригинал` заполняется русским текстом прямо на устройстве.
- Текущее поведение `Перевод` ожидаемо зеркалит исходный текст, потому что для проверки выставлен `target_lang = ru`.
- `Health-check` HTTP фактически работает, но в старой on-device сборке его фидбек почти не виден, так как статус сессии быстро перетирает сообщение о здоровье шлюза.
- В код уже внесён follow-up фикс:
  - явный `gatewayHealthText` в статусной карточке;
  - resume через `session snapshot`, чтобы после `Health-check` и `Ре-регистрации` UI подтягивал актуальную server-side сессию.
- Этот фикс собран в simulator (`BUILD SUCCEEDED`) и сохранён в ветке `codex/iphone-companion-ui-fastfollow` коммитом `4fe1c87`, но его on-device переустановка пока упёрлась в Apple install service по wireless deploy.

## Обновление 2026-03-15 20:00: fresh settings-fix build reinstalled on iPhone 14 Pro Max

- После отдельной real-device сборки свежий iOS-клиент с фиксом настроек успешно переустановлен на `iPhone 14 Pro Max` (`00008120-001C58983C00C01E`) по wireless `devicectl`.
- Переустановлена именно свежая сборка из:
  - `/Users/USER3/Library/Developer/Xcode/DerivedData/KrabVoice-dmarxbzrqkaskrhgnlpjivptmwnu/Build/Products/Debug-iphoneos/KrabVoice.app`
- Для этой сборки подтверждено наличие актуального speech-permission:
  - `NSSpeechRecognitionUsageDescription = Нужен доступ к распознаванию речи для живых субтитров и перевода.`
- Проверка `devicectl device info apps` показывает на устройстве установленный `KrabVoice 0.2.0` с bundle id:
  - `com.antigravity.krabvoice.user3.macbook.pro.pablito.local`
- CLI-запуск этой новой сборки упёрся только в блокировку экрана устройства (`Locked`), а не в install/tunnel-проблему.
- Значит текущий follow-up уже не про доставку `.app`, а про ручную on-device валидацию:
  - реагируют ли `translation_mode / source_lang / target_lang`;
  - появился ли видимый статус у `Health-check`;
  - закрыт ли баг с недоступными кнопками под плавающим системным таббаром.


## Обновление 2026-03-15 05:52: phrase-based ru/es mobile translation uplift

- В `Krab Voice Gateway` улучшен mobile translation layer для live partial-текста.
- Вместо прежнего префиксного fallback (`ES: ...` / `RU: ...`) gateway теперь применяет phrase-based правила для `ru -> es` и `es -> ru`.
- Это не финальный NMT/cloud translator, но уже даёт осмысленные partial-переводы без внешних API ключей.
- Проверка через live HTTP proof на gateway:
  - proof session: `vs_314de69629f5`
  - transcript: `Привет, проверка связи, завтра отправить договор`
  - translation.partial: `Hola, prueba de conexión, mañana enviar contrato`
- Unit tests для gateway обновлены и проходят (`tests/test_sessions_api.py`).
- Ветка `Krab Voice Gateway`: `codex/iphone-companion-ui-fastfollow`, commit с этим улучшением — следующий после `4fe1c87`.

## Обновление 2026-03-15 21:41 UTC: on-device settings truth + ru/es drift triage

- На свежей on-device сборке `iPhone 14 Pro Max` теперь подтверждено уже не только `stt.partial`, но и интерактивность настроек:
  - `translation_mode = ru_es_duplex` переключается;
  - `source_lang = ru` переключается;
  - `target_lang = es` переключается.
- `Health-check` теперь даёт видимый feedback в статусной карточке:
  - `Шлюз доступен ✅`.
- Живой on-device session proof после этого шага:
  - `session_id = vs_dd30cc9c2f46`
  - `event = stt.partial`
- Живой `ru -> es` partial proof на устройстве тоже получен, но пока нестабилен:
  - начало partial действительно уходит в испанский (`Prueba de conexión ...`),
  - затем перевод деградирует в смешанный `es+ru` текст,
  - после stop/start пользователь поймал iOS speech cancellation message:
    - `Speech ошибка: Recognition request was canceled`.
- Это уже не проблема сети, `Gateway URL` или picker'ов, а два оставшихся product-level хвоста:
  - drift в mobile `ru -> es` translation helper на длинных mixed partial;
  - ложная ошибка отмены распознавания при штатной остановке/перезапуске.
- Для этого уже собран и установлен follow-up фикс:
  - gateway translation helper жёстче держит явный `source_lang = ru`, не переопределяя его из-за латинских токенов вроде `health-check`;
  - phrase+word translation layer расширен для длинных русских partial;
  - `SpeechRecognitionManager` перестал показывать expected cancellation как пользовательскую ошибку.
- Проверки follow-up фикса:
  - `/Users/USER3/Projects/KrabVoiceGateway-user3/tests/test_sessions_api.py` -> `17 passed`;
  - `xcodebuild -project /Users/USER3/Projects/KrabVoiceiOS-user3-fastfollow/KrabVoice.xcodeproj -scheme KrabVoice -destination 'generic/platform=iOS' build` -> `BUILD SUCCEEDED`;
  - `devicectl install` на `iPhone 14 Pro Max` -> `App installed`.
- Ветка `Krab Voice Gateway`: `codex/iphone-companion-ui-fastfollow`, commit:
  - `3102c98` — `fix: stabilize mobile ru-es translation and speech cancellation`
- Текущий оставшийся шаг стал очень узким:
  - открыть уже переустановленную свежую сборку на `iPhone 14 Pro Max`;
  - повторить короткий `ru -> es` прогон;
  - подтвердить, что translation drift уменьшился, а `Recognition request was canceled` больше не всплывает как ошибка.
