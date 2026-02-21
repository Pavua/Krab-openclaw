# EXTERNAL PROMPT — GEMINI 3 PRO (FRONTEND R9)

## Контекст
Нужно сделать в web-панели понятный "Control Center" для OpenClaw:
- local/cloud режим;
- autoswitch статус/применение;
- каналный статус;
- быстрые action-кнопки.

Backend endpoint уже есть (и часть будет расширена backend-потоком):
- `GET /api/model/catalog`
- `POST /api/model/apply`
- `GET /api/openclaw/model-autoswitch/status`
- `POST /api/openclaw/model-autoswitch/apply`
- `GET /api/openclaw/channels/status` (должен появиться после backend R9)

## Границы (не нарушать)
1. Не менять Python/backend.
2. Работать только в frontend файлах.
3. Не ломать существующую панель, добавить блоки аккуратно.

## Файлы
- `/Users/pablito/Antigravity_AGENTS/Краб/src/web/index.html`
- (опционально) `/Users/pablito/Antigravity_AGENTS/Краб/src/web/prototypes/nano/index_redesign.html` если нужна синхронизация.

## Что сделать
1. Добавить новый раздел UI: **OpenClaw Control Center**.
2. В разделе отобразить:
   - текущий `force_mode` + active local model + cloud slots (из `/api/model/catalog`);
   - autoswitch статус (из `/api/openclaw/model-autoswitch/status`);
   - статус каналов (из `/api/openclaw/channels/status`), включая warnings.
3. Добавить кнопки actions:
   - Apply Autoswitch (`POST /api/openclaw/model-autoswitch/apply`);
   - Set Mode Local/Auto/Cloud (через `/api/model/apply`);
   - Refresh Status.
4. UX:
   - чёткие статусы `OK / WARN / FAIL`;
   - лоадеры при запросах;
   - видимые ошибки от API (не терять detail).
5. Мобильная адаптация:
   - блок должен читаться на ширине ~390px;
   - кнопки не ломают сетку.

## Проверка
1. Все новые кнопки кликабельны.
2. Ошибки API показываются в интерфейсе.
3. Нет конфликтов с текущими id/селекторами панели.

## Критерий готовности
1. Изменения только во frontend файлах.
2. В ответе перечислить:
   - какие новые id/блоки добавлены;
   - какие endpoint используются;
   - как выглядит fallback при ошибках API.
