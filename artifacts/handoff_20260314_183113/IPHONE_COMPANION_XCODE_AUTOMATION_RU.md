# iPhone companion: автоматизация Xcode bootstrap

Дата: `2026-03-14`

Этот документ фиксирует новый per-account сценарий для iPhone companion:
shared-репозиторий остаётся общим, а локальный Xcode project создаётся отдельно
для текущей macOS-учётки без конфликтов между `USER3`, `pablito` и другими аккаунтами.

## Что добавлено

- `scripts/generate_iphone_companion_xcode_project.py`
- `src/integrations/ios_companion_project.py`
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
