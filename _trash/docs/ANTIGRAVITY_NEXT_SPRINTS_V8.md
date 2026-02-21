# Antigravity Next Sprints (Post-v8 Core Lock)

**Дата:** 2026-02-12
**Контекст:** Krab core стабилен (`pytest -q` зеленый), можно ускоряться по смежным трекам.

## Цель для Antigravity
Закрыть межпроектные e2e и продуктовые фичи в зоне ownership Antigravity, чтобы экосистема `Krab + Krab Ear + Krab Voice Gateway` была production-ready.

## Ветки
1. `Краб`: `codex/v8-dev` (только antigravity-owned файлы по split).
2. `Krab Ear`: `codex/krab-ear-v2`.
3. `Krab Voice Gateway`: `codex/voice-gateway-v1`.

## Sprint Block A (P0): Voice Gateway API Contract Complete

### Что сделать
1. Довести и зафиксировать API:
   - `POST /v1/sessions`
   - `PATCH /v1/sessions/{id}`
   - `DELETE /v1/sessions/{id}`
   - `GET /v1/sessions/{id}`
   - `GET /v1/sessions/{id}/stream` (WS события: `stt.partial`, `translation.partial`, `tts.ready`, `call.state`)
2. Закрыть Twilio hooks:
   - `POST /v1/telephony/twilio/voice`
   - `POST /v1/telephony/twilio/status`
   - `WS /v1/telephony/twilio/media`
3. Добавить строгую валидацию payload + понятные error-codes.

### Acceptance
1. `pytest tests` в `Krab Voice Gateway` — green.
2. Интеграционный тест webhook + media WS — green.
3. Документация контрактов в README + примеры `curl`/WS.

## Sprint Block B (P0): Krab Ear Call Assist IPC + UI Finish

### Что сделать
1. В backend `Krab Ear` гарантировать IPC-методы:
   - `start_call_assist`
   - `stop_call_assist`
   - `get_call_assist_state`
   - `list_audio_inputs`
2. Провести сквозную настройку полей:
   - `voice_gateway_url`
   - `voice_gateway_api_key`
   - `call_notify_default`
   - `capture_source_mode`
   - `ui_last_tab`
3. В UI стабилизировать вкладки:
   - `Диктовка`
   - `Live перевод`
   - `История`

### Acceptance
1. Тесты `Krab Ear` backend/ui-state — green.
2. Ручной smoke: `start -> stream -> stop` без зависаний.
3. Сохранение и восстановление settings между перезапусками.

## Sprint Block C (P1): Telegram Voice Ops UX Hardening

### Что сделать
1. Улучшить UX команд в `Краб` (antigravity ownership):
   - `!callstart`, `!callstatus`, `!callstop`, `!notify`, `!calllang`
2. Добавить более информативные статусы ошибок (gateway offline, invalid state, auth).
3. Добавить summary завершенного звонка в Telegram (короткий и расширенный режим).

### Acceptance
1. Тесты `tests/test_voice_gateway_hardening.py` + `tests/test_telegram_control.py` — green.
2. Нет падений handler-ов при offline Voice Gateway.
3. В group-чате dangerous call-операции ограничены правилами безопасности.

## Sprint Block D (P1): Group Moderation v2 E2E Scenarios

### Что сделать
1. Расширить шаблоны rule engine (spam, flood, abuse, links).
2. Добавить dry-run режим в пользовательский поток (`!group`), не только в коде.
3. Улучшить audit trail для модерации (кто/что/почему, привязка к правилу).

### Acceptance
1. `tests/test_group_moderation_v2.py` — green.
2. `tests/test_group_moderation_scenarios.py` — green.
3. Отчет о действиях модерации читаем и не шумит лишними false-positive.

## Sprint Block E (P1): Cross-Project E2E Pack

### Что сделать
1. E2E сценарий:
   - `Krab command` -> `VoiceGateway session` -> `event stream` -> `Telegram status update`.
2. Автотест деградации:
   - cloud down -> local fallback path сигнализируется корректно.
3. Подготовить единый smoke-runner для трех проектов (с пошаговым выводом).

### Acceptance
1. Документ `docs/E2E_THREE_PROJECTS.md` с шагами запуска и expected outputs.
2. Авто-smoke сценарий запускается одной командой.
3. Итоговый отчет содержит состояние каждого узла цепочки.

## Sprint Block F (P0): iOS/PSTN Readiness Track

### Что сделать
1. Подготовить минимальный iOS call-flow smoke:
   - CallKit/PushKit wake-up (mock),
   - reconnect при потере сети,
   - восстановление voice session state.
2. Проверить consent-политику:
   - `auto_on` по умолчанию,
   - ручное переключение ON/OFF,
   - корректное отражение в статусах.
3. Добавить telephony failure scenarios:
   - webhook timeout/retry,
   - media WS reconnect,
   - graceful degradation.

### Acceptance
1. Новый набор тестов iOS/PSTN smoke — green.
2. Документ `docs/IOS_PSTN_SMOKE.md` с шагами и expected outcomes.
3. Regression на текущие voice команды не ломается.

## Sprint Block G (P1): Telegram Call UX Finalization

### Что сделать
1. Привести формат ответов call-команд к единому стилю:
   - статусы, ошибки, рекомендации.
2. Добавить enrich для `!callsummary`:
   - выделение action items,
   - краткая метрика качества сессии (latency/cache hits).
3. Добавить command hints:
   - после ошибок предлагать конкретный следующий шаг.

### Acceptance
1. `tests/test_voice_gateway_hardening.py` полностью green.
2. `tests/test_telegram_control.py` полностью green.
3. Сообщения читаемые, без избыточного текста, но с actionable guidance.

## Sprint Block H (P1): Telemetry Normalization

### Что сделать
1. Нормализовать event schema между `Krab` и `Krab Voice Gateway`.
2. Добавить mapping полей:
   - session_id, event_type, latency_ms, source, severity.
3. Добавить smoke на совместимость schema версий.

### Acceptance
1. Контрактный тест schema-version green.
2. Документ `docs/VOICE_EVENT_SCHEMA.md`.
3. Деградация при неизвестном поле без падений.

## Ограничения split (обязательно)
1. Не менять файлы из зоны Codex ownership.
2. Перед push запускать:
   - `scripts/check_workstream_overlap.command`
3. Любой overlap => отдельный микро-PR с явным согласованием.
