# Follow-up Prompt Для Gemini 3.1 Pro (Frontend Stream)

Работаем продолжением в этом же диалоге.
Модель: Gemini 3.1 Pro (High), с использованием Nano Banana Pro для визуального polish.

## Sprint A (блокирующий, обязательно сначала)

Нужно довести `src/web/prototypes/nano/index_redesign.html` до совместимости с боевым `src/web/index.html`.

### Что исправить

1. Вернуть отсутствующие DOM id:
   - `assistantApiKey`
   - `feedbackStatsBtn`
   - `opsActionMeta`
   - `quickDeepBtn`
   - `quickDeepTopic`
2. Удалить мок-маркеры/заглушки:
   - `Mocked for Prototype View`
   - `Simulating the environment for the prototype showcase`
3. Сохранить совместимость с текущей JS-логикой (без backend-изменений).

### Проверка (обязательная)

Запустить:
`scripts/validate_web_prototype_compat.command`

Критерий успеха:

- `missing ids: 0`

- `mock markers: 0`

## Sprint B (новые задачи по дизайну интерфейсов)

Не трогая backend-код, подготовить UI-спеки и прототипы:

1. **Транскрибатор / Voice Console UI**
   - Файл-спека: `docs/frontend_ui_polish/TRANSCRIBER_UI_SPEC_RU.md`
   - Прототип: `src/web/prototypes/nano/transcriber_console.html`
   - Должны быть состояния: idle/listening/transcribing/error/success.

2. **Ops/Monitoring Center UI**
   - Файл-спека: `docs/frontend_ui_polish/OPS_CENTER_UI_SPEC_RU.md`
   - Прототип: `src/web/prototypes/nano/ops_center.html`
   - Должны быть состояния: normal/warn/critical + журнал событий.

3. **Единый UI стиль для всех интерфейсов**
   - Файл: `docs/frontend_ui_polish/CROSS_INTERFACE_STYLE_GUIDE_RU.md`
   - Описать токены, сетку, типографику, компоненты и правила адаптива.

## Ограничения

1. Не менять Python/handlers/OpenClaw-логику.
2. Не редактировать `src/web/index.html` напрямую.
3. Работать только в разрешённых ownership-путях frontend потока.

## Формат сдачи

1. Список изменённых файлов.
2. Что сделано по Sprint A и Sprint B.
3. Команды проверки и результаты.
4. Что готово к интеграции сразу.
5. Остаточные риски.
