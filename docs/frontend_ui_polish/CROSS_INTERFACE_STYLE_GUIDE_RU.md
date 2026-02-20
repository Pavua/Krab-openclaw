# Cross-Interface Style Guide (Nano Banana Pro)

## 1. Концепция

Данный стиль применяется ко всем веб-интерфейсам экосистемы Krab (Web Panel, Transcriber, Ops Center).
Основная визуальная тема: **Deep Space** с фокусом на читаемость данных и неоновые акценты. Отсутствие "визуального шума" (тяжелых градиентов фонов панелей), вместо этого — строгие сетки и hover-эффекты.

## 1.1. Базовый файл темы (nano_theme.css)

Начиная с этапа Frontend R3, в проекте используется единый файл базовых стилей: `src/web/prototypes/nano/nano_theme.css`.

Он содержит все общие CSS-переменные (цвета, шрифты, отступы), базовые стили для `body` и контейнеров, а также универсальные компоненты (кнопки, карточки, бейджи, поля ввода).

Все новые HTML-прототипы **должны** подключать этот файл в `<head>`:

```html
<link rel="stylesheet" href="nano_theme.css">
```

Блок `<style>` в самих HTML-файлах должен содержать **только** page-specific стили (специфичные сетки, уникальные элементы конкретного дашборда). Дублирование переменных или базовых элементов (например, `.card`, `.button`) в HTML-файлах запрещено.

## 2. Дизайн Токены (CSS Variables)

### 2.1. Цвета и Фоны

```css
:root {
    --bg-base: #050505;          /* Основной фон (космос) */
    --bg-panel: #0d0e12;         /* Фон карточек (panels) */
    --bg-surface: #15171e;       /* Фон интерактивных элементов (inputs) */
    --bg-surface-hover: #1e2129; /* Hover для карточек и инпутов */

    --border-subtle: rgba(255, 255, 255, 0.05); /* Легкий контур */
    --border-card: rgba(255, 255, 255, 0.1);    /* Контур карточек */
    --border-focus: rgba(14, 165, 233, 0.6);    /* Активный фокус */

    --text-main: #f8fafc;
    --text-muted: #94a3b8;
    --text-placeholder: #475569;
}
```

### 2.2. Акценты и Статусы

```css
:root {
    --accent-cyan: #0ea5e9;
    --accent-cyan-hover: #38bdf8;
    --accent-glow: 0 0 20px rgba(14, 165, 233, 0.3);

    --state-ok: #10b981;    /* Успех, Listening, Idle (greenish) */
    --state-warn: #f59e0b;  /* Предупреждение, Processing (amber) */
    --state-bad: #ef4444;   /* Ошибка, Critical, Offline (red) */
    --state-purple: #8b5cf6;/* Спец. процессы, AI Reasoning */
}
```

### 2.3. Тени и Скругления

```css
:root {
    --radius-sm: 8px;
    --radius-md: 12px;
    --radius-lg: 16px;
    --radius-pill: 9999px;

    --shadow-panel: 0 10px 30px -10px rgba(0,0,0,0.8);
}
```

## 3. Типографика

- **Headers & UI Elements:** `'Outfit', system-ui, sans-serif`
- **Код, Логи, Транскрибация:** `'Menlo', 'Monaco', 'Courier New', monospace` (или JetBrains Mono).
- Все заголовки `h1`-`h6` имеют `letter-spacing: -0.02em` для большей плотности, кроме uppercase labels (у них `letter-spacing: 0.05em`).

## 4. Сетка и Адаптив

- Базовый контейнер `max-width: 1280px` с `margin: 0 auto`.
- Используется CSS Grid:
  - `.grid-metrics`: `repeat(auto-fit, minmax(240px, 1fr))` — для дашбордов.
  - `.grid-split`: `1fr 2fr` или 50/50 блоки.
- На мобильных устройствах (`@media (max-width: 900px)`) сетка должна превращаться в `1fr`. Отступы контейнера уменьшаются с `32px` до `16px`.

## 5. Базовые Компоненты

- **Card**: Блоки `.card` имеют фон `--bg-panel`, бордер `--border-card`, скругление `--radius-lg` и легкий градиент на верхней границе через `::after`.
- **Button**:
  - Standard: `background: var(--bg-surface)`, border: `var(--border-subtle)`.
  - Primary: `linear-gradient(135deg, var(--accent-cyan), #0284c7)` без бордеров, плюс glow shadow.
- **Badge**: Метки статусов (OK, WARN, BAD) имеют цветной текст и прозрачный 10% фон своего цвета.

## 6. Иконки и Символы
