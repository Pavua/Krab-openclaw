# Antigravity Remaining Work (v8 Finish Pack)

**Дата:** 2026-02-12
**Контекст:** Core в `Краб` стабилен, основной хвост — live E2E и телефония/iOS.

## P0 — сделать в первую очередь

1. `Krab Voice Gateway`: Twilio production path hardening.
- Проверить и зафиксировать контракты:
  - `POST /v1/telephony/twilio/voice`
  - `POST /v1/telephony/twilio/status`
  - `WS /v1/telephony/twilio/media`
- Добавить интеграционные тесты retry/reconnect/timeout.
- Acceptance: `pytest tests` green + сценарий media reconnect подтвержден.

2. `Krab Ear`: стабильность call-assist в длинной сессии.
- Прогнать soak-тест `start -> stream -> stop` (15-30 мин).
- Проверить восстановление после временной потери Voice Gateway.
- Acceptance: нет утечек/зависаний, state корректно возвращается в `idle`.

3. Межпроектный live E2E.
- Сценарий: `Краб !callstart -> Voice Gateway session -> stream events -> !callstatus -> !callstop`.
- Сценарий деградации: `OpenClaw down -> local fallback` и корректная сигнализация в Telegram/Web.
- Acceptance: документ с фактическими логами и reproducible шагами.

## P1 — следующим блоком

1. iOS/PSTN readiness track.
- Smoke для CallKit/PushKit wake-up/reconnect (mock + реальный контур где возможно).
- Проверка consent policy (`auto_on`, `on/off override`) на всем call-flow.
- Acceptance: `docs/IOS_PSTN_SMOKE.md` + green smoke tests.

2. Event schema normalization (`Краб` <-> `Voice Gateway`).
- Зафиксировать поля: `session_id`, `event_type`, `latency_ms`, `source`, `severity`.
- Добавить контрактный тест совместимости версий.
- Acceptance: `docs/VOICE_EVENT_SCHEMA.md` + совместимость без падений.

3. Telegram call UX polish.
- Унифицировать форматы ответов `!call*`, усилить actionable hints при ошибках.
- Acceptance: `tests/test_voice_gateway_hardening.py` и `tests/test_telegram_control.py` green.
