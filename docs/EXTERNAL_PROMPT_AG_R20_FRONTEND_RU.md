# AG Prompt R20 Frontend — Health UX (Lite vs Deep)

Контекст:

## Контекст

- Проект: `/Users/pablito/Antigravity_AGENTS/Краб`
- Web Panel: `/Users/pablito/Antigravity_AGENTS/Краб/src/web/index.html`

- API:
  - быстрый liveness: `GET /api/health/lite`
  - deep health: `GET /api/health`

## Задача

Сделать понятный UX статусов здоровья: отдельно показывать, что процесс жив (`lite`), и отдельно что deep ecosystem check может быть degraded/slow.

## Требования

1. В dashboard добавить секцию `Core Health` с 2 индикаторами:
   - `Core Liveness` (из `/api/health/lite`)
   - `Ecosystem Deep Health` (из `/api/health`)
2. Для deep health показывать:
   - `status`, `degradation`, `risk_level`,
   - timestamp последнего успешного обновления,
   - fallback-текст при timeout/error.
3. Поведение refresh:
   - lite: каждые 3-5 секунд,
   - deep: каждые 20-30 секунд,
   - запросы не должны блокировать UI.
4. Никаких breaking changes существующих кнопок.
5. Визуально: аккуратный блок в текущем стиле панели.
Файлы:

- `/Users/pablito/Antigravity_AGENTS/Краб/src/web/index.html`
Тест/смок:

1. Локально открыть `http://127.0.0.1:8080`.
2. Убедиться, что оба индикатора обновляются независимо.
3. Приложить 1-2 скриншота результата.

**Формат ответа:**

1. Какие файлы изменены.

2. Что именно реализовано.
3. Команды/шаги проверки.
4. Риски/ограничения.
