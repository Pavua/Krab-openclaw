# 🦀 Krab v8 Roadmap (OpenClaw-first)

**Дата:** 2026-02-12
**Ветка разработки:** `codex/v8-dev`
**Статус:** In Progress (~99.7%: A-G функционально закрыты; добавлен adaptive feedback loop; финальный хвост — внешний iOS/PSTN live прогон на реальном канале)

## Цель

Сделать Krab тонким, управляемым оркестратором поверх OpenClaw с максимальным контролем Telegram, гибким роутингом моделей и интеграцией с независимыми проектами `Krab Ear` и `Krab Voice Gateway`.

## Фазы

### Phase A — Stabilization Baseline

- Исправить критические синтаксические/импортные сбои.
- Стабилизировать команды и help-реестр.
- Привести зависимости в рабочее состояние (`apscheduler`, `dateparser`).
- Статус: ✅ Done

### Phase B — OpenClaw Web/Auth/Browser Hardening

- Политика `web_fetch/web_search -> OpenClaw browser -> local fallback`.
- Подготовка OAuth-контуров (`openai-codex`, `google-gemini-cli`, optional `qwen-portal`).
- Статус: ✅ Done (`web_fetch/web_search` и fallback включены; auth readiness + deep-check + remediation plan + browser smoke API/command/web)

### Phase C — Telegram Summary & Max Control

- `!summaryx <X> [target] [--focus "..."]` через реальные сообщения Telegram API.
- `!chatid` для быстрой адресации.
- Picker недавних чатов в ЛС при отсутствии `target`.
- Статус: ✅ Done (v8.1 hardening: explicit errors, clean picker, concise msgs + Group Moderation v2 templates)

### Phase D — Model Routing

- Free-first hybrid: локалка для простых задач, облако для критичных.
- Базовые guardrails и память выбора моделей.
- Статус: ✅ Done+ (task profiling, память выбора, heavy/light scheduler, soft-cap usage, adaptive feedback loop `1-5` для quality-aware рекомендаций)

### Phase E — Agent/Skill Provisioning Layer

- Каталоги шаблонов агентов и навыков.
- Поток `draft -> preview -> apply` для owner/superuser.
- Статус: ✅ Done (`!provision`, `config/agents_catalog.yaml`, `config/skills_catalog.yaml`)

### Phase F — Multi-Project Integration

- Интеграция `Krab` ↔ `Krab Voice Gateway` через тонкий клиент.
- Интеграция с `Krab Ear` без жесткой связки.
- Статус: ✅ Baseline Done (`VoiceGatewayClient`, `!call*` команды, API+tests в `Krab Voice Gateway`)
- Дополнительно: ✅ Web-панель и API (`/api/health`, `/api/links`, `!web`) для оперативного доступа к экосистеме.
- Дополнительно: ✅ Web-native assistant режим (без Telegram): `/api/assistant/query` + UI блок в панели.
- Дополнительно: ✅ Live E2E раннер `scripts/live_ecosystem_e2e.py` + one-click `scripts/run_live_ecosystem_e2e.command` + гайд `docs/E2E_THREE_PROJECTS.md`.

### Phase G — Ops & Observability

- Soft-cap/alerts по расходам облачных моделей.
- Health/usage отчеты по каналам и моделям.
- Статус: ✅ Extended+++ (usage report JSON + soft-cap flag + model recommendations + ops alerts API/command/UI + web assistant rate-limit + audit events + idempotency + merge guard + ops ack/unack/history)
- Дополнительно: ✅ Unified ecosystem health (`OpenClaw + Local LM + Voice Gateway + Krab Ear`) через `/api/health`, `/api/ecosystem/health`, `/api/ecosystem/health/export` и `!web health`.

## Acceptance-гейт v8

- `python tests/smoke_test.py` проходит.
- `pytest tests/test_handlers.py tests/test_openclaw_client.py tests/test_summary.py` проходит.
- `summaryx` работает для текущего и удаленного чата.
- Опасные команды ограничены ЛС + аудитируются.

## Parallel Mode (Codex + Antigravity)

- Статус: ✅ Enabled (50/50 split зафиксирован)
- Ownership-файлы:
  - `config/workstreams/codex_paths.txt`
  - `config/workstreams/antigravity_paths.txt`
- Протокол:
  - `docs/parallel_execution_split_v8.md`
- Anti-collision проверка:
  - `scripts/check_workstream_overlap.command`
- Merge guard:
  - `scripts/merge_guard.command`
  - `python scripts/merge_guard.py --full`

## Последняя верификация (2026-02-12)

- `Краб`: `pytest tests/test_telegram_control.py tests/test_group_moderation_scenarios.py tests/test_group_moderation_v2.py ...` → ✅ 77 passed (added group moderation e2e).
- `Краб`: `python tests/smoke_test.py` → ✅ OK.
- `Краб`: `python scripts/health_dashboard.py` → ✅ normal (openclaw/local ok; voice/ear expected offline если сервисы не запущены).
- `Краб`: browser smoke (Playwright) для web-панели и API (`/`, `/api/health`, `/api/openclaw/report`, `/api/openclaw/deep-check`, `/api/assistant/query`, `/api/ops/usage`, `/api/ops/alerts`) → ✅ OK.
- `Краб`: browser smoke (Playwright) idempotency (`X-Idempotency-Key` для `/api/assistant/query`) → ✅ replay работает.
- `Краб`: browser smoke (Playwright) remediation plan (`/api/openclaw/remediation-plan`) → ✅ OK.
- `Краб`: browser smoke (Playwright) OpenClaw browser smoke (`/api/openclaw/browser-smoke`) + link в web UI → ✅ OK.
- `Краб`: full test run упрощен и стабилизирован через `pytest.ini` (`testpaths=tests`, ignore non-Krab dirs) → ✅ `pytest -q` = 152 passed.
- `Краб`: `pytest -q` (после ecosystem health расширения) → ✅ `166 passed`, 2 warnings.
- `Краб`: `pytest -q tests/test_ecosystem_health.py tests/test_web_app.py` → ✅ `21 passed`.
- `Краб`: `python scripts/live_ecosystem_e2e.py` → ✅ `overall_ok=true`, voice lifecycle (`create -> patch -> diagnostics -> stop -> verify 404`) green, отчет: `artifacts/ops/live_ecosystem_e2e_20260212_212008Z.json`.
- `Краб`: `pytest -q` (после live e2e runner) → ✅ `168 passed`, 2 warnings.
- `Краб`: voice schema нормализация добавлена (`VoiceGatewayClient.normalize_stream_event`) + checker (`scripts/check_voice_event_schema.py`) + runbook (`docs/VOICE_EVENT_SCHEMA.md`, `docs/IOS_PSTN_SMOKE.md`).
- `Краб`: `pytest -q tests/test_voice_event_schema.py tests/test_voice_gateway_client.py tests/test_voice_gateway_hardening.py` → ✅ `10 passed`.
- `Краб`: `python scripts/check_voice_event_schema.py '{"type":"stt.partial","data":{"session_id":"vs_demo","latency_ms":99,"source":"twilio_media"}}'` → ✅ `ok=true`.
- `Краб`: must-have confirm-step для Telegram-команд (`!think/!code/!smart --confirm-expensive`) + тесты `tests/test_ai_confirm_expensive.py`.
- `Краб`: `pytest -q tests/test_ai_confirm_expensive.py tests/test_handlers.py tests/test_web_app.py` → ✅ `46 passed`.
- `Краб`: preflight-планировщик задач (router + Web API + Telegram):
  - `ModelRouter.get_task_preflight(...)`
  - `POST /api/model/preflight`
  - `!model preflight [task_type] <задача> [--confirm-expensive]`
- `Краб`: `pytest -q tests/test_model_router_phase_d.py tests/test_web_app.py tests/test_ai_confirm_expensive.py` → ✅ `37 passed`.
- `Краб`: `pytest -q` (после preflight блока) → ✅ `177 passed`, 1 warning.
- `Краб`: adaptive feedback loop по моделям:
  - Router API: `submit_feedback(...)`, `get_feedback_summary(...)`, `get_last_route()`
  - Telegram: `!model feedback ...`, `!model stats [profile]`
  - Web API: `GET/POST /api/model/feedback`
  - Web UI: блок оценки ответа и просмотр feedback stats
- `Краб`: `pytest -q tests/test_model_router_phase_d.py tests/test_web_app.py` (после feedback-loop) → ✅ `40 passed`.
- `Краб`: `pytest -q` (после feedback-loop) → ✅ `183 passed`, 1 warning.
- `Краб`: `python tests/smoke_test.py` (после feedback-loop) → ✅ `OK`.
- `Krab Voice Gateway`: `pytest --disable-warnings` → ✅ `18 passed`.
- `Krab Voice Gateway`: telephony cost estimator теперь поддерживает offline fallback без Twilio ключей (`scripts/estimate_telephony_cost.py` + `tests/test_telephony_cost_estimator.py`).
- `Krab Ear`: `pytest tests/test_backend_service.py tests/test_history_store.py tests/test_translator.py tests/test_engine_cleanup.py` → ✅ 48 passed.
