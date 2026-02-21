# EXTERNAL PROMPT — GEMINI 3 PRO (FRONTEND R10)

## Контекст
Нужно довести UX web-панели до операционного уровня после R9:
- в OpenClaw Control Center уже есть базовые кнопки;
- нужно добавить UX для local model lifecycle и ясные статусы ошибок;
- устранить путаницу между `file://` и `http://127.0.0.1:8080` режимами.

## Границы (не нарушать)
1. Не менять Python/backend.
2. Работать только во frontend-файлах.
3. Не ломать существующие `id` и обработчики.

## Файлы
- `/Users/pablito/Antigravity_AGENTS/Краб/src/web/index.html`
- (опционально sync) `/Users/pablito/Antigravity_AGENTS/Краб/src/web/prototypes/nano/index_redesign.html`

## Что сделать
1. В блоке **OpenClaw Control Center** добавить mini-секцию **Local Model Lifecycle**:
   - статус локальной модели (`loaded/not_loaded`, имя модели);
   - кнопки:
     - `Load Local`
     - `Unload Local`
     - `Refresh Local Status`
   - использовать endpoint (ожидаются от backend R10):
     - `GET /api/model/local/status`
     - `POST /api/model/local/load-default`
     - `POST /api/model/local/unload`

2. Добавить UX-статусы и цветовые бейджи:
   - `OK` (зелёный), `WARN` (янтарный), `FAIL` (красный).
   - для autoswitch/channels/local-status в одном визуальном стиле.

3. Добавить **runtime hint**, если страница открыта в `file://`:
   - показать заметный warning-banner:
     - «Панель открыта как файл. Открой через http://127.0.0.1:8080».
   - без изменений backend.

4. Обновить обработку ошибок API:
   - в `ocMeta` и связанных мета-блоках показывать `detail` из ответа;
   - не терять контекст (какая операция упала).

5. Адаптивность:
   - блок lifecycle + control center читабелен на ~390px;
   - кнопки не ломают layout и не вылазят из контейнера.

## Проверка
1. Кнопки lifecycle кликабельны и меняют статус в UI.
2. При 403/500 ошибка видна явно и человек понимает что делать.
3. Существующие action-кнопки R9 продолжают работать.

## Критерий готовности
1. Изменения только во frontend-файлах.
2. В отчете перечислить:
   - новые `id`;
   - какие endpoint использованы;
   - как выглядит banner для `file://`.
