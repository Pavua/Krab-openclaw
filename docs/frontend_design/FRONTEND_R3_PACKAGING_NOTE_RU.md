# Frontend R3: CSS Packaging Note

## Обзор

В рамках этапа **Frontend R3** был проведен рефакторинг CSS-архитектуры прототипов интерфейсов Krab Web Panel V2. Главная цель рефакторинга — устранение дублирования кода, консистентность визуального стиля и упрощение поддержки.

Все общие, переиспользуемые стили были вынесены в единый файл: `nano_theme.css`.

## Затронутые файлы

- `src/web/prototypes/nano/nano_theme.css` (Создан)
- `src/web/prototypes/nano/index_redesign.html` (Рефакторинг стилей)
- `src/web/prototypes/nano/transcriber_console.html` (Рефакторинг стилей)
- `src/web/prototypes/nano/ops_center.html` (Рефакторинг стилей)
- `docs/frontend_ui_polish/CROSS_INTERFACE_STYLE_GUIDE_RU.md` (Обновлен)

## Структура `nano_theme.css`

Файл `nano_theme.css` построен по принципу "Design Tokens + Base Components" и включает следующие секции:

1. **CSS Variables (`:root`)**:

   Модифицированная палитра *Nano Banana Pro Deep Space*:
   - Backgrounds (`--bg-base`, `--bg-panel`, `--bg-surface`, `--bg-surface-hover`)
   - Borders (`--border-subtle`, `--border-card`, `--border-focus`)
   - Text Colors (`--text-main`, `--text-muted`, `--text-placeholder`)
   - Accents & States (`--accent-cyan`, `--state-ok`, `--state-warn`, `--state-bad`, `--state-purple`)
   - Shadows & Radii
   - Typography (`--font-main`, `--font-mono`)

2. **Base Styling (`*`, `body`)**:

   Сброс box-sizing, плавные переходы (`transition`), глобальный шрифт, цвет текста и базовый фон (линейный градиент космоса).

3. **Layout Elements**:

   Базовые контейнеры (`.container`), заголовки (`header`, `h1`, `.subtitle`).

4. **UI Components**:

- **Cards**: `.card`, `.card-title`, `.card-value`, `.card-meta`
- **Buttons**: Базовый `button`, `.primary` (с градиентом и свечением)
- **Inputs**: Поля ввода `.field`, `.form-group`, `.form-label`, `select`
- **Badges**: `.badge`, со статусами `.ok`, `.warn`, `.bad`

## Правила работы со стилями в прототипах

- **Обязательное подключение**: Любой новый HTML-прототип должен подключать `nano_theme.css` в `<head>`:

```html
<link rel="stylesheet" href="nano_theme.css">
```

- **Запрет на дублирование**: Запрещается объявлять переменные палитры (`:root`) или стили для базовых компонентов (например, `.card`, `.button`) внутри тега `<style>` в HTML-файле.
- **Page-Specific Styles**: Тег `<style>` в HTML файле должен содержать *только* стили, уникальные для данной конкретной страницы.
  - *Пример (index_redesign.html)*: Специфичные сетки (`.grid-metrics`), стили для Assistant Output.
  - *Пример (transcriber_console.html)*: Стили для окна транскрипта (`.transcript-window`, `.t-chunk`), анимация LED-индикаторов.
  - *Пример (ops_center.html)*: Стили конкретных уведомлений (`.alert-item`), таблица журнала (`.journal-container`).

## Валидация

До внедрения в build-систему проверка совместимости проводится скриптом `scripts/validate_web_prototype_compat.py`. HTML-прототипы остаются view-only шаблонами, но используют общий CSS-ресурс.
