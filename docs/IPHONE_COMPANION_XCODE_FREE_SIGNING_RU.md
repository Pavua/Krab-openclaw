# iPhone companion: Xcode Free Signing (без paid Apple Developer)

Дата фиксации: `2026-03-14`

Этот документ описывает минимальный и быстрый путь, чтобы запустить iPhone companion
на реальном устройстве без платного аккаунта разработчика. Цель — получить первый
controlled trial вживую, не трогая paid entitlements.

## Что нужно заранее

- iPhone на iOS 16+ с включенным Developer Mode.
- Установленный Xcode (логин Apple ID в Xcode обязателен).
- iPhone и Mac на одной Wi‑Fi сети.
- Запущены `Krab Voice Gateway` и `Krab Ear`.

Примечание: free signing обычно живет 7 дней, после чего нужно повторно собрать и установить приложение.

## Шаг 1. Открыть iOS skeleton

В репозитории есть готовый SwiftUI‑скелет:

- `Krab Voice Gateway/ios/KrabVoiceiOS`

Быстрый запуск:

- `Krab Voice Gateway/scripts/open_ios_skeleton.command`

## Шаг 2. Создать Xcode проект

1. В Xcode создать новый проект: iOS App (SwiftUI).
2. Указать уникальный `Bundle Identifier`, например `com.pavua.krabvoice`.
3. Включить `Automatically manage signing`.
4. Выбрать `Team` = `Personal Team` (ваш Apple ID).
5. Сохранить проект в локальной папке вне shared repo, например `~/Projects/KrabVoiceiOS`.

## Шаг 3. Перенести файлы skeleton

Добавить в проект Swift‑файлы:

- `KrabVoiceApp.swift`
- `ContentView.swift`
- `GatewayClient.swift`
- `GatewayStreamClient.swift`
- `CallManager.swift`
- `PushRegistryManager.swift`
- `Models.swift`

Лучше использовать `Add Files to ...` с опцией `Copy items if needed`.

## Шаг 4. Info.plist и permissions

Добавить в `Info.plist`:

- `NSMicrophoneUsageDescription` = `Нужен доступ к микрофону для перевода звонка`.
- `NSLocalNetworkUsageDescription` = `Нужен доступ к локальной сети для подключения к Krab Gateway`.

Для free signing пока не включаем:

- `Push Notifications`
- `VoIP`
- `Background Modes`

Эти capabilities лучше включать уже после перехода на AltStore/SideStore или paid аккаунт.

## Шаг 5. Настроить gateway URL и API key

В приложении есть настройки:

- `Gateway URL` по умолчанию `http://127.0.0.1:8090` — для iPhone нужно заменить на IP вашего Mac.
- `Gateway API key` оставить пустым, если `KRAB_VOICE_API_KEY` не задан.

Получить IP Mac:

- Wi‑Fi: `ipconfig getifaddr en0`
- USB‑tethering: `ipconfig getifaddr en1`

Пример:

- `Gateway URL = http://192.168.1.22:8090`

## Шаг 6. Запуск на iPhone

1. Подключить iPhone по кабелю или включить Wireless Debugging.
2. Выбрать устройство в Xcode и `Run`.
3. Разрешить “Trust Developer” на iPhone.
4. Запустить приложение и проверить `Health-check`.

## Шаг 7. Связать с owner panel

1. В owner panel зарегистрировать `device_id` (например `iphone-dev-1`).
2. В приложении использовать тот же `device_id`.
3. Нажать `Старт` (создание session) и убедиться, что появляются live subtitles.

Важно: без PushKit устройство может оставаться в статусе `ATTENTION`.
Это нормально для первого trial.

## Когда переходить на AltStore/SideStore

Если нужен:

- постоянный доступ без 7‑дневного expiry;
- Push/VoIP flow;
- background‑режимы;

то после первого trial стоит перейти на `AltStore/SideStore`.
