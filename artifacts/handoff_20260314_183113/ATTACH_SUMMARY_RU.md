# Attach Summary (RU)

## Цель пакета

Передать весь контекст, артефакты и инструкции так, чтобы новый диалог
мог продолжить разработку без потерь.

## Готовность

- Проект: ~68%.
- Текущий блок (iPhone companion + live trial): ~58%.

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

## Что дальше

1. Пройти Xcode Free Signing на реальном iPhone и запустить companion через `Krab Voice Gateway/ios/KrabVoiceiOS`.
2. С устройства подтвердить `Health-check` и доступ к `http://<IP Mac>:8090`.
3. Зафиксировать first live subtitles/timeline для session `vs_0b93dc247b1d` или новой live-session (скрин + артефакт).

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

