# iOS/PSTN Smoke Checklist

**Дата:** 2026-02-12
**Назначение:** быстрый smoke для трека звонков (iOS/PSTN) перед продовым прогоном.

## Область проверки

1. Voice Gateway telephony hooks.
2. Стабильность media stream и reconnect.
3. Consent policy (`auto_on`/`auto_off`) и отражение в статусах.
4. Деградация при временных сбоях сети/вебхуков.

## Предусловия

1. Запущен `Krab Voice Gateway`.
2. Настроены URL вебхуков провайдера телефонии (Twilio/эквивалент).
3. В `Краб` доступен `VOICE_GATEWAY_URL`.
4. (Опционально) запущен `Krab Ear` backend для desktop-side flow.

## Сценарии smoke

1. Webhook intake:
   - Отправить тестовый `voice` webhook.
   - Ожидаемо: HTTP 200, session создана/связана, лог без traceback.

2. Status callback:
   - Отправить `status` webhook (ringing -> in-progress -> completed).
   - Ожидаемо: статус сессии обновляется корректно.

3. Media WS reconnect:
   - Оборвать WS media поток и восстановить.
   - Ожидаемо: reconnect без падения процесса, события продолжают поступать.

4. Consent policy:
   - Запустить с `notify_mode=auto_on`, затем переключить на `auto_off`.
   - Ожидаемо: изменение видно в `!callstatus`/диагностике и в session payload.

5. Degradation:
   - Временно остановить OpenClaw.
   - Ожидаемо: AI деградация `degraded_to_local_fallback` (при доступной локалке), команда не падает.

## Артефакты

1. Health snapshot:
   - `python scripts/health_dashboard.py`

2. Live E2E:
   - `scripts/run_live_ecosystem_e2e.command`
   - отчет: `artifacts/ops/live_ecosystem_e2e_<UTC>.json`

3. Event schema:
   - `scripts/check_voice_event_schema.command`

## Критерий PASS

1. Нет crash/traceback при webhook/media сценариях.
2. Session lifecycle проходит (`create -> patch -> diagnostics -> stop`).
3. Consent переключается и отражается в статусах.
4. При деградации сохраняется управляемый fallback, без silent-failure.
