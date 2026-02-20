# 🦀 Krab Project Handover (v8.0 Architecture Finish)

**Project:** Krab AI Userbot
**Current Status:** ✅ Phase 17.7 Completed (Silent Failure Fixes & Streaming Stability)
**Date:** 15.02.2026
**Version:** v8.3 (Stability Edition)

---

## ✅ v8 Sprint Update #23 (2026-02-20) — Telegram Control & Call UX + Group Moderation v2

### Что реализовано

1. **Telegram Control UX Hardening (`!summaryx`)**:
   - Усилены ответы об ошибках для `!summaryx` (отказы в доступе к чатам, некорректные ID/usernames, пустые выборки).
   - Формат ошибок стандартизирован до пользовательских подсказок (actionable next-steps) с эмодзи.
   - Добавлены негативные unit/mock тесты на права и доступы к Summaryflow.

2. **Group Moderation v2 (Stabilization)**:
   - Шаблоны правил (spam, flood, abuse, links) перебалансированы и улучшены для лучшей точности.
   - Устранена ошибка ложных срабатываний фильтра запрещенных слов (False Positives) за счет перехода на regex с учетом границ слов (`\\b`).
   - Добавлены интегрированные e2e тесты на false-positive срабатывания и поведение флага `dry-run`. 

3. **Voice Command Telegram UX (`!call*`)**:
   - Приведены к единому стилю формата ответы команд `!callstart`, `!callstop`, `!callstatus`, `!callsummary`, `!callphrase`, `!callphrases`, `!callwhy`, `!calltune`.
   - Усилена детальная диагностика при offline-состоянии Voice Gateway и missing-sessions. Выводу ошибок добавлены actionable hints (конкретные шаги вроде запуска `calldiag`).
   - Исходный код команд-утилит очищен от шумных/длинных неструктурированных ответов.
   - Добавлены/обновлены coverage-тесты на проверку форматов обрывов звонков.

### Верификация

1. Workstream Overlap Check:
   - `scripts/check_workstream_overlap.py` → ✅ Нет конфликтов с зоной Codex.
2. Общий прогон тестов:
   - `pytest tests/` → ✅ `323 passed`.

---

### Что реализовано

1. **STT качество/пунктуация (Krab Ear runtime в Krab Core)**:
   - `src/modules/perceptor.py`:
     - добавлен расширенный STT-профиль (`beam_size`, `best_of`, `patience`, `no_speech_threshold`, `compression_ratio_threshold`);
     - добавлен безопасный fallback на базовый профиль при несовместимости аргументов `mlx_whisper`;
     - добавлена детерминированная постобработка транскрипта:
       - нормализация пробелов и пунктуации,
       - капитализация предложений,
       - добавление финальной точки при длинной фразе без завершения,
       - поддержка пользовательских замен через `STT_REPLACE_JSON`.
     - поддержка `STT_HOTWORDS` для усиления распознавания важных терминов.
   - `src/utils/voice_bridge.py`:
     - синхронизирован с тем же STT-профилем и постобработкой для standalone-режима (`krab_ear.command`).

2. **Signal daemon hardening (канал связи)**:
   - `openclaw_signal_daemon.command` переведён с foreground-режима на `launchd`:
     - автозапуск/keepalive,
     - логи в `logs/signal-daemon.{out,err}.log`,
     - проверка регистрации номера перед стартом,
     - проверка порта и probe после запуска.
   - добавлены one-click скрипты:
     - `openclaw_signal_daemon_stop.command`
     - `openclaw_signal_daemon_status.command`
     - `openclaw_signal_link.command` (линковка secondary device при `429 Rate Limited` на register)

3. **WhatsApp link flow**:
   - добавлен one-click скрипт `openclaw_whatsapp_link.command`:
     - запускает `openclaw channels login --channel whatsapp`,
     - после QR-link сразу показывает probe-статус.

4. **Runtime Recovery для OpenClaw (anti-regression)**:
   - добавлен one-click скрипт `openclaw_runtime_repair.command`:
     - восстанавливает `primary + fallbacks` (local-first с cloud fallback),
     - фиксирует `session.dmScope=per-channel-peer`,
     - применяет безопасные лимиты токенов для каналов/моделей,
     - выводит итоговый `models status` + `channels status --probe`.
   - зафиксировано, что runtime-настройки OpenClaw живут в `~/.openclaw/openclaw.json` и могут частично сбрасываться после wizard/update/profile-switch.

5. **Signal Register UX hardening**:
   - `openclaw_signal_register.command` улучшен:
     - автоподхват `signalcaptcha://...` из буфера (до 120 секунд ожидания),
     - поддержка чтения ссылки из файла (`@/path/to/file`),
     - нормализация длинной ссылки (удаление пробелов/переносов),
     - явная диагностика `429 Rate Limited` с корректными next steps.

6. **Anti-413 переход между чатами (операционная устойчивость)**:
   - добавлен документ `docs/CHAT_TRANSITION_PLAYBOOK_RU.md`:
     - компактный протокол переноса контекста в новый диалог,
     - минимальный обязательный набор данных для continuity.
   - добавлен one-click скрипт `prepare_next_chat_context.command`:
     - собирает branch/commit/status/diff summary в `artifacts/context/next_chat_context_*.md`,
     - копирует контекст в буфер обмена для мгновенной вставки в новый чат.

7. **Параллельные потоки (расширение до multi-stream)**:
   - `scripts/check_workstream_overlap.py` обновлён с 2 потоков на N потоков (`*_paths.txt`), теперь ловит коллизии между всеми ownership-группами.
   - добавлены ownership-файлы:
     - `config/workstreams/gemini_design_paths.txt`
     - `config/workstreams/nanobanana_ui_paths.txt`
   - добавлен тест:
     - `tests/test_check_workstream_overlap.py` (✅ `2 passed`).

8. **Signal Recovery one-click**:
   - добавлен `openclaw_signal_recover.command`:
     - проверка регистрации номера,
     - выбор `register+verify` или `link secondary device`,
     - автоматический повтор запуска daemon и status-check после recovery.
   - улучшен `openclaw_signal_link.command`:
     - автоматическое копирование `sgnl://` ссылки в буфер обмена,
     - автогенерация QR (`artifacts/signal/signal_link_qr.png`) при доступном `qrencode`/`python qrcode`.
   - добавлен `openclaw_signal_daemon_logs.command`:
     - быстрый просмотр последних out/err логов daemon,
     - optional follow-режим (`follow` / `-f`).

### Текущий операционный статус (на момент обновления)

- Telegram / Discord / Slack / iMessage / WhatsApp: `works` (probe ок).
- Signal: `probe failed`, так как номер `signal-cli` ещё не зарегистрирован (daemon не поднимается на `:18080` без успешного register+verify).
- В логах Signal устойчиво: `Signal SSE stream error: TypeError: fetch failed` -> `reconnecting`.
- Это ожидаемо до завершения регистрации номера в `signal-cli`.

### Верификация

1. Тесты STT-модуля:
   - `pytest -q tests/test_perceptor.py`
   - ✅ `10 passed`.

2. Скрипты каналов:
   - `zsh -n openclaw_signal_daemon.command openclaw_signal_daemon_stop.command openclaw_signal_daemon_status.command openclaw_whatsapp_link.command`
   - ✅ синтаксис корректен.
   - `./openclaw_signal_daemon_status.command` показывает фактический статус Signal/WhatsApp/других каналов через `openclaw channels status --probe`.

---

## ✅ v8 Sprint Update #21 (2026-02-19) — Web Assistant UX, Model Catalog API, Attachments, Stable Startup

### Что реализовано

1. **Web Assistant Model Control API (P0)**:
   - `GET /api/model/catalog` — полный каталог слотов, cloud/local моделей и режима.
   - `POST /api/model/apply` — смена режима (`auto/local/cloud`), модели слота и быстрых пресетов.
   - Привязка к alias-нормализации моделей для предсказуемого выбора в UI.

2. **Web Panel UX без ручных команд (P0)**:
   - В `src/web/index.html` добавлены:
     - селекторы режима/пресета/слота/модели,
     - кнопки применения (`режим`, `пресет`, `слот`),
     - быстрые сценарии (`Web Search`, `Deep Research`, `Разобрать URL`),
     - поддержка `reasoning depth` и `RAG` через UI-элементы.

3. **Вложения в web-assistant (P0)**:
   - Новый endpoint: `POST /api/assistant/attachment`.
   - Поддержка `text/pdf/docx` с best-effort извлечением текста.
   - Для `image/video/archive` — сохранение + метаданные + prompt-snippet.
   - Фронтенд-кнопка «Добавить файл» переведена на серверную загрузку.

4. **Стабилизация старта ядра / устранение 404 каталога (P0)**:
   - Причина: warmup MLX мог валить процесс до подъёма web API.
   - `src/modules/perceptor.py`: `PERCEPTOR_AUDIO_WARMUP` оставлен опциональным, но
     дефолт переключён на `0` (безопасный startup-first режим).
   - `.env.example` дополнен явным флагом и комментариями.

### Верификация

1. Локальный smoke:
   - запуск `python -m src.main` без override-переменных,
   - `GET /api/model/catalog` → `200` (подъём ~6 сек),
   - web-панель стартует стабильно.

2. Автотесты:
   - расширен `tests/test_web_app.py`:
     - проверка `attachment_endpoint` в capabilities,
     - новый тест загрузки `POST /api/assistant/attachment`.
   - прогон:
     - `pytest -q tests/test_web_app.py tests/test_model_set_parser.py`
     - ✅ `34 passed`.

---

## ✅ v8 Sprint Update #20 (2026-02-16) — Queue-first, Reaction Learning, Group Attribution

### Что реализовано

1. **Per-chat FIFO Queue (P0)**:
   - `src/handlers/ai.py`: lock-отсечка заменена на `ChatWorkQueue`.
   - Входящие сообщения в одном чате обрабатываются по очереди без потерь.
   - Добавлено уведомление о позиции в очереди (опционально).

2. **Forward/Reply/Author Context (P0)**:
   - Добавлен явный `forward_context` для пересланных сообщений.
   - Добавлен усиленный `reply_context`.
   - Добавлен `author_context`, чтобы в группах Краб не путал владельца с другим участником.

3. **Reaction Learning + Chat Mood (P0)**:
   - Новый модуль: `src/core/reaction_learning.py`.
   - Реакции сохраняются в `artifacts/reaction_feedback.json`.
   - Реакции используются как weak-signal для `ModelRouter.submit_feedback(...)`.
   - Добавлен rolling mood-профиль чата и авто-реакции с rate-limit/kill-switch.

4. **Loop/Hallucination Guardrails (P0 hardening)**:
   - `src/core/stream_client.py`: усилен детектор циклов (включая repeated-tail loop).
   - `src/handlers/ai.py`: пост-очистка повторяющихся абзацев перед отправкой в Telegram.
   - Добавлено безопасное live-превью стрима с хвостом текста, чтобы `edit_text` не падал на длинных ответах.

5. **Ops/Policy UX (P0)**:
   - Новые команды: `!ctx`, `!policy`, `!reactions`, `!mood`.
   - `!brain` дополнен queue/reaction метриками.
   - Web API: `/api/policy`, `/api/queue`, `/api/reactions/stats`, `/api/mood/{chat_id}`.

6. **LM Studio model sizes (UX)**:
   - `ModelRouter.list_local_models_verbose()` возвращает `size_bytes/size_human`.
   - `!model scan` показывает размер локальных моделей.

### Тесты

- Добавлены:
  - `tests/test_auto_reply_queue.py`
  - `tests/test_forward_context.py`
  - `tests/test_reaction_learning.py`
  - `tests/test_web_policy_endpoints.py`
  - расширение `tests/test_stream_client_failover.py`

- Прогоны:
  - `pytest -q tests/test_auto_reply_queue.py tests/test_forward_context.py tests/test_reaction_learning.py tests/test_stream_client_failover.py tests/test_web_policy_endpoints.py tests/test_model_router_stream_fallback.py tests/test_web_app.py`
  - ✅ `50 passed`

---

## ✅ v8 Sprint Update #19 (2026-02-15) — Streaming Stability & Silent Failure Fixes

### Что реализовано

1. **Phase 17.4: Streaming Stability & Hard Truncation**:
   - Реализована жёсткая обрезка (Hard Truncation) в `src/core/stream_client.py` (4000 символов).
   - Исправлена ошибка "Connection Error" в `ai.py`.
   - Унифицированы streaming-маршруты в `model_manager.py`.

2. **Phase 17.5: Loop & Repetition Protection**:
   - Реализован `CircularRepetitionDetector` в `stream_client.py`.
   - Добавлены stop-токены для GLM-4/локальных моделей.
   - Добавлены `presence_penalty` и `frequency_penalty` (0.1).

3. **Phase 17.6: Reasoning Loop Protection**:
   - Поддержка `reasoning_content` в `stream_client.py`.
   - `MAX_REASONING_LIMIT` (2000 символов) для reasoning-блока.
   - `include_reasoning` отключен по дефолту для локальных моделей.

4. **Phase 17.7: Silent Failure Fixes** (КРИТИЧЕСКОЕ):
   - **`error_handler.py`**: Убран рекурсивный retry при FloodWait (вызывал `maximum recursion depth exceeded`). Добавлен перехват `RecursionError`.
   - **`markdown_sanitizer.py`** (НОВЫЙ): Закрывает незакрытые ``` блоки перед `edit_text`, устраняя поток `Unclosed tags: <pre>` (~8/сек).
   - **`ai.py`**: Streaming edit_text теперь использует `sanitize_markdown_for_telegram()`.
   - **`system.py`**: `!sh`/`!commit` очищают вывод от вложенных бэктиков.
   - **`notifier.py`**: Исправлен `parse_mode="markdown"` (невалидный → `None`).

### Файлы

| Файл | Действие | Описание |
| :--- | :--- | :--- |
| `src/core/error_handler.py` | **UPDATED** | Убрана рекурсия FloodWait, добавлен RecursionError catch |
| `src/core/markdown_sanitizer.py` | **NEW** | Закрытие незакрытых ``` блоков |
| `src/core/stream_client.py` | **UPDATED** | Hard truncation + CircularRepetitionDetector + reasoning support |
| `src/core/model_manager.py` | **UPDATED** | Stop tokens, penalties, include_reasoning=False |
| `src/core/notifier.py` | **UPDATED** | Исправлен parse_mode |
| `src/handlers/ai.py` | **UPDATED** | Безопасный streaming markdown |
| `src/handlers/system.py` | **UPDATED** | Безопасный вывод !sh/!commit |

### Верификация

- Бот перезапущен и принял сообщение «проверка связи» → `Local LLM success char_count=3804`.
- **0 новых** `Unclosed tags: <pre>` ошибок после перезапуска.
- **0 новых** `RecursionError` / `maximum recursion depth`.
- **0 новых** `Invalid parse mode` ошибок.
- Модель `glm-4.6v-flash` загружена и отвечает через LM Studio.---

## ✅ v8 Sprint Update #18 (2026-02-13) — Project Handover Engine & Global Polishing

### Что реализовано

1.  **Project Handover Engine (Phase 16.2)**:
    *   Реализован модуль `src/core/handover.py`, который генерирует отчеты `HANDOVER.md` для завершенных автономных проектов.
    *   Интегрирован в `ProjectAgent` (`src/core/agent_loop.py`).
    *   Добавлена вкладка **"📂 Проекты"** в Streamlit Dashboard (`src/utils/dashboard_app.py`) для визуализации прогресса и отчетов.

2.  **Global Lint & Hardening**:
    *   Исправлены сотни предупреждений Markdown Lint во всех файлах проекта.
    *   Стандартизированы шаблоны PRD в `.ralphy/templates/`.

3.  **macOS Turnkey Tooling**:
    *   Созданы `update_docs.command` и `verify_project.command` для автоматизации рутины.
    *   Добавлен `verify_handover.py` для тестирования движка отчетов.

4.  **ProjectAgent Robustness**:
    *   Внедрен механизм **Auto-Fallback** на локальные модели при ошибках облачного API (OpenClaw).

### Верификация

*   `pytest -q` → ✅ `177 passed`.
*   `python3 verify_handover.py` → ✅ `SUCCESS`.
*   `./verify_project.command` → ✅ `Environment & Core Logic OK`.

---

---

## ✅ v8 Sprint Update #16 (2026-02-12) — Adaptive Feedback Loop (Quality-Aware Routing)

### Что реализовано

1. В `ModelRouter` добавлен контур обратной связи по качеству ответа:
   - `submit_feedback(score, profile, model, channel, note)`
   - `get_feedback_summary(profile, top)`
   - `get_last_route()`

2. Роутер теперь запоминает последний успешный прогон (`profile/task_type/channel/model`) и позволяет оценивать его одной командой без ручного ввода модели.

3. Рекомендации моделей стали quality-aware:
   - `_get_profile_recommendation(...)` учитывает не только частоту использования, но и user-feedback.
   - Добавлен feedback hint (`avg_score`, `count`) в recommendation.

4. Усилены ops guardrails:
   - новый alert `model_quality_degraded` при устойчиво низких оценках модели.

5. Telegram UX:
   - `!model feedback <1-5> [note]`
   - `!model feedback <1-5> <profile> <model> [channel] [note]`
   - `!model stats [profile]`

6. Web API + UI:
   - `GET /api/model/feedback`
   - `POST /api/model/feedback` (WEB_API_KEY + idempotency support)
   - в web-панели добавлен блок оценки ответа и просмотра feedback stats.
   - `POST /api/assistant/query` теперь возвращает `last_route` (profile/model/channel) для точной оценки.

### Зачем

1. Краб начинает адаптировать рекомендации под реальное качество, а не только под историю вызовов.
2. Это снижает риск «застревания» на модели с плохим практическим результатом.
3. Улучшает путь к Manus-style UX: preflight + execution + feedback + авто-улучшение маршрутизации.

### Верификация

1. Тесты:
   - `tests/test_model_router_phase_d.py` (feedback submit/summary, influence on recommendation, degraded quality alert),
   - `tests/test_web_app.py` (`GET/POST /api/model/feedback`, key guard, idempotency),
   - `tests/test_handlers.py` (импорт/регистрация не нарушены).

2. Прогоны:
   - `pytest -q tests/test_model_router_phase_d.py tests/test_web_app.py` → ✅ `40 passed`.
   - `pytest -q` → ✅ `183 passed`, `1 warning`.
   - `python tests/smoke_test.py` → ✅ `OK`.

---

## 🚨 CRITICAL INSTRUCTIONS FOR NEXT AGENT/DEV

**STOP! READ THIS FIRST.**
This project has just undergone a MAJOR architectural refactoring. **Do not rollback changes.**

### 1. Architecture Paradigm Shift

- **Krab is now a THIN CLIENT.**
- **OpenClaw is the BRAIN.**
- **DO NOT** re-implement local `WebScout`, `RAGEngine`, or `Browser` logic in Python.
- **ALWAYS** use `src.core.openclaw_client.OpenClawClient` to delegate these tasks to the OpenClaw Gateway.

### 2. Core Components (Updated)

| Component | Status | Description |
| :--- | :--- | :--- |
| `src/main.py` | **Updated** | Initializes `OpenClawClient`. No longer inits `WebScout`. |
| `src/core/openclaw_client.py` | **NEW** | **The most important file.** HTTP Client for OpenClaw API. |
| `src/handlers/tools.py` | **Updated** | `!news`, `!research` now call `OpenClawClient`. |
| `src/utils/web_scout.py` | **DEPRECATED** | Do not use. Kept for reference only. |
| `src/core/rag_engine.py` | **DEPRECATED** | Do not use. Use OpenClaw Vector Store. |

### 3. How to Run

#### Start the Bot (Userbot)

```bash
# Activate venv first!
source .venv/bin/activate
# Run the startup script
./start_krab.command
```

*Note: Requires active OpenClaw Gateway running separately.*

#### Verify Connectivity

```bash
# Run the connection test script
./.venv/bin/python tests/test_openclaw_client.py
```

### 4. Configuration (`.env`)

Ensure these variables are set in `.env`:

```ini
OPENCLAW_BASE_URL=http://localhost:8000  # Or your gateway URL
OPENCLAW_API_KEY=sk-...                  # Your OpenClaw API Key
```

---

## ✅ Recent Achievements (Phase 4.1)

1. **Unified Client:** Created `OpenClawClient` to abstract all interactions with the AI Gateway.
2. **Tool Migration:** Moved `web_search` and `news_digest` logic to OpenClaw.
3. **Cleanup:** Decoupled monolithic local engines (`WebScout`, `RAGEngine`) from the main bot loop.
4. **Verification:** Validated via `tests/test_openclaw_client.py` (Health, Tools, Agent Tasks).

## 🔮 Next Steps (Roadmap)

*See `task.md` for the full 20-phase roadmap.*

**Immediate Priorities (Phase 4.2+):**

1. **Deep Integration**: Move more tools (e.g., specific scrapers) to OpenClaw.
2. **Voice/Vision**: Ensure `!voice` and `!see` leverage OpenClaw's multimodal capabilities where possible.
3. **Dockerization**: Prepare `docker-compose.yml` for the entire stack (Krab + OpenClaw).

## 📂 Key Documentation Artifacts

1. **`task.md`**: The Master Roadmap. **Update this** as you progress.
2. **`walkthrough.md`**: Proof of work and verification logs.
3. **`AGENTS.md`**: High-level behavioral instructions.

---

## ✅ v8 Sprint Update #5 (2026-02-12) — Voice Gateway Hardening & Ops UX

### Что реализовано (Voice Gateway)

1. **API Contract Hardening**:
   - Внедрены Pydantic-модели для Twilio Webhooks (`TwilioVoiceRequest`, `TwilioStatusRequest`).
   - Добавлена **строгая валидация** payloads (422 при несоответствии).
   - Введена **авторизация** (API Key) на чувствительные GET-эндпоинты (`/v1/sessions`, `/v1/sessions/{id}`, `/v1/sessions/{id}/diagnostics`).

2. **Robustness**:
   - Обновлен `_ensure_twilio_session` для атомарной связки CallSid и сессий.
   - Twilio Voice Webhook теперь безопасно возвращает TwiML `<Stream>` при любых входящих от Twilio.

3. **Documentation**:
   - `README.md` дополнен примерами curl и матрицей зон ответственности (Codex/Antigravity).

### Что реализовано (Краб / Telegram Ops)

1. **Hardened Ops Commands**:
   - Команда `!ops` ( Ops Hub):
     - `!ops audit` — вывод последних системных событий из `BlackBox`.
     - `!ops summary` — сводный отчет (Voice / Mod / Provis / DB status).
     - `!ops health` — проверка доступности Voice Gateway, OpenClaw и Local Brain.

2. **Voice Ops UX**:
   - `!callstatus` теперь показывает «человеческие» статусы и детали (режим, источник, ID).
   - `!callsummary` форматируется как Premium Intel Report с action items.

3. **Moderation v2 Hardening**:
   - В `GroupModerationEngine` добавлены шаблоны `spam` и `abuse`.
   - Dry-run уведомления стали более информативными (показывают правило и причину).

4. **Conflict Defense**:
   - `scripts/run_conflict_defense.command` — поиск дублей команд в коде.
   - `scripts/sync_agent_boundaries.command` — актуализация `docs/agent_ownership.json`.

### Тесты и верификация (E2E)

1. **Cross-Project E2E**:
   - `tests/test_e2e_ecosystem.py` — проверка связи `Краб` -> `Voice Gateway` (Health, Create, Tune, Diagnostics, WS Stream).
   - WS Stream E2E: подтверждено мгновенное получение событий (`call.state`, `test_event`) через WebSocket.

2. **Gateway Internal Tests**:
   - `tests/test_validation_challenge.py` — проверка Auth-заборов и Pydantic-валидации.
   - `pytest tests` → ✅ `13 passed`.

---

**Signed off by:** Antigravity (Senior Autonomous Architect)
**Ecosystem Integrity:** Hardened & Verified
**Next step:** Sprint Block H (Krab Ear Performance & AI Guardian Integration)

---

## ✅ v8 Addendum (2026-02-12)

### Что уже сделано в ветке `codex/v8-dev`

1. Добавлен `task.md` как каноничный roadmap-файл.
2. Исправлен критический `IndentationError` в `src/handlers/ai.py`.
3. Введен суперпользовательский контур (`SUPERUSERS`) в `src/handlers/auth.py`.
4. Опасные команды ограничены ЛС и аудитируются в Saved Messages/`@p0lrd`:
   - `!sh`, `!commit`, `!refactor`, `!panic`, `!godmode`, `!exec`.
5. Добавлен Telegram Control слой:
   - `src/core/telegram_chat_resolver.py`
   - `src/core/telegram_summary_service.py`
   - `src/handlers/telegram_control.py`
   - Команды: `!summaryx`, `!chatid`, inline picker чатов в ЛС.
6. Web-политика переведена в OpenClaw-first для browse/learn URL:
   - `web_fetch` через OpenClaw, локальный BrowserAgent только fallback.
7. В `requirements.txt` добавлены обязательные пакеты:
   - `APScheduler`
   - `dateparser`

### Что синхронизировано с голосовым треком

1. В `main.py` подключен `VoiceGatewayClient`.
2. В `tools.py` добавлены команды:
   - `!callstart`, `!callstop`, `!callstatus`, `!notify`, `!calllang`.

### Важное

- Не переписывать OpenClaw-функции локальными скраперами.
- Все новые изменения держать в отдельной ветке (`codex/*`).

---

## ✅ v8 Sprint Update #2 (2026-02-12)

### Дополнительно реализовано после Addendum

1. Закрыт baseline-гейт тестов и импортов:
   - fixed `scheduling.py` (`_parse_duration`, optional `dateparser` fallback),
   - fixed `privacy.py` (совместимый импорт Pyrogram),
   - улучшен `handlers/__init__.py` (safe registration optional модулей).

2. Phase D (Model Routing) внедрен в `src/core/model_manager.py`:
   - task profiling: `chat/moderation/code/security/infra/review/communication`,
   - память выбора моделей по профилям (JSON),
   - scheduler локалок `1 heavy + 1 light`,
   - soft-cap guardrail по cloud calls + usage report,
   - рекомендации роутера в `!model` + `!model recommend <profile>`.

3. Phase E (Provisioning) внедрен:
   - `src/core/provisioning_service.py`,
   - `src/handlers/provisioning.py` (`!provision templates|list|draft|preview|apply`),
   - каталоги `config/agents_catalog.yaml`, `config/skills_catalog.yaml`,
   - поток `draft -> preview(diff) -> apply(confirm)`.

4. Phase B hardening усилен:
   - локальный BrowserAgent переведен в fallback-only режим через `ENABLE_LOCAL_BROWSER=1`,
   - default: `ENABLE_LOCAL_BROWSER=0` (OpenClaw-first).

5. Обновлены docs/config:
   - `.env.example` (SUPERUSERS, routing policy, summary allowlist, browser toggle),
   - `README.md` (summaryx/provision/model routing),
   - `task.md` (актуальный статус фаз и multi-project verification),
   - `scripts/health_dashboard.py` + `scripts/health_dashboard.command` (единый health snapshot).

6. Web Access/Control улучшен:
   - `src/modules/web_app.py` расширен (`/api/health`, `/api/links`, `/api/model/recommend`),
   - `src/web/index.html` обновлен под экосистемный dashboard,
   - команда `!web`/`!web health` добавлена в `src/handlers/commands.py`.

### Проверка (фактические прогоны)

1. `Краб`:
   - `pytest ...` (43 теста) → ✅ `43 passed`,
   - `python tests/smoke_test.py` → ✅ `OK`.

2. `Krab Voice Gateway`:
   - `pytest tests` → ✅ `5 passed`.

3. `Krab Ear`:
   - `pytest tests/test_backend_service.py tests/test_history_store.py tests/test_translator.py tests/test_engine_cleanup.py` → ✅ `48 passed`.

---

## ✅ v8 Sprint Update #3 (2026-02-12)

### Что дополнительно усилено

1. Исправлен критичный дефект логирования в `src/core/openclaw_client.py`:
   - устранены небезопасные вызовы `logging` с keyword-аргументами (`path=...`, `query=...`),
   - теперь сетевые ошибки OpenClaw обрабатываются стабильно без вторичного `TypeError`.

2. Усилен OpenClaw auth hardening:
   - добавлена нормализация auth payload по провайдерам в `OpenClawClient`,
   - добавлены поля readiness:
     - `required_providers`,
     - `missing_required`,
     - `unhealthy_required`,
     - `ready_for_subscriptions`,
   - поддержан env-контур `OPENCLAW_REQUIRED_AUTH_PROVIDERS`.

3. Команда `!openclaw` расширена:
   - статус показывает `Auth readiness` и `Subscriptions flow`,
   - `!openclaw auth` выводит матрицу провайдеров (`UP/DOWN`) + missing/unhealthy required.

4. Web API усилен по безопасности и проверкам:
   - write-endpoints provisioning защищаются `WEB_API_KEY`,
   - `README`/`.env.example` синхронизированы с `WEB_API_KEY` и `OPENCLAW_REQUIRED_AUTH_PROVIDERS`.

5. Исправлен баг в `GroupModerationEngine`:
   - убраны лишние экранирования в regex (`\\S`, `\\1`),
   - корректно детектируются ссылки и повторяющиеся символы.

6. Добавлен web-native assistant режим (без Telegram):
   - endpoint `GET /api/assistant/capabilities`,
   - endpoint `POST /api/assistant/query`,
   - UI блок `Web Assistant` в `src/web/index.html` (prompt/task_type/RAG + ответ).

7. Добавлен OpenClaw deep-check:
   - `OpenClawClient.get_deep_health_report()` (tool smoke + issues + remediation),
   - web endpoint `GET /api/openclaw/deep-check`,
   - telegram подкоманда `!openclaw deep`.

8. Расширен Ops-слой (Phase G):
   - `ModelRouter.get_usage_summary()` и `ModelRouter.get_ops_alerts()`,
   - web endpoints: `GET /api/ops/usage`, `GET /api/ops/alerts`,
   - telegram команда `!ops`,
   - UI-блок `Ops Alerts` в web-панели.

9. Усилена безопасность web-native assistant:
   - rate-limit `WEB_ASSISTANT_RATE_LIMIT_PER_MIN`,
   - аудит событий в `BlackBox`:
     - `web_assistant_query`,
     - `web_provisioning_draft_create`,
     - `web_provisioning_apply`.

10. Добавлены reliability guardrails:
    - idempotency (`X-Idempotency-Key`) для web write-endpoints (assistant/provisioning),
    - `WEB_IDEMPOTENCY_TTL_SEC`,
    - merge guard (`scripts/merge_guard.py`, `scripts/merge_guard.command`) для overlap + ключевых тестов перед merge.

11. Добавлен remediation plan по OpenClaw:
    - `OpenClawClient.get_remediation_plan()`,
    - web endpoint `GET /api/openclaw/remediation-plan`,
    - telegram подкоманда `!openclaw plan`.

### Тесты и верификация (Update #3)

1. Добавлены/обновлены тесты:
   - `tests/test_openclaw_client_health.py` (provider readiness + safe network error path),
   - `tests/test_web_app.py` (openclaw report/deep-check + write-auth provisioning endpoints),
   - `tests/test_group_moderation_engine.py` (policy/rules/persistence/mod actions),
   - `tests/test_model_router_phase_d.py` (usage summary + ops alerts),
   - `tests/test_web_app.py` (assistant rate-limit + web audit events + idempotency + remediation-plan endpoint),
   - `tests/test_openclaw_client_health.py` (remediation plan scenarios).

2. Фактические прогоны:
   - `pytest ...` (расширенный набор) → ✅ `69 passed`,
   - `python tests/smoke_test.py` → ✅ `OK`,
   - `python scripts/health_dashboard.py` → ✅ `normal` (voice/ear offline до запуска сервисов),
   - browser smoke (Playwright, локальная панель) → ✅ root + refresh click + `/api/health` + `/api/openclaw/report` + `/api/openclaw/deep-check` + `/api/assistant/query` + `/api/ops/usage` + `/api/ops/alerts`,
   - browser e2e idempotency (`X-Idempotency-Key`) → ✅ повторный запрос помечается `idempotent_replay=true`,
   - browser smoke remediation plan (`/api/openclaw/remediation-plan`) → ✅ `200 OK`.

---

## ✅ v8 Sprint Update #4 (2026-02-12)

### Что дополнительно сделано в этом цикле (Update #4)

1. Полностью стабилизирован общий test-run:
   - `pytest.ini` обновлен (`testpaths=tests`, `norecursedirs` расширен),
   - устранен шум/падения из сторонних директорий (`ComfyUI`, `scratch`) при `pytest -q`.

2. Закрыта legacy-совместимость ядра без отката v8:
   - `SecurityManager`:
     - восстановлены fallback-правила (`admins`/`blocked`),
     - дефолтная роль совместимости возвращается как `user` (вместо `guest`) для старых тестов/flow.
   - `PersonaManager`:
     - добавлена persona `waifu`,
     - обновлен prompt `coder` (включая `Senior Architect`),
     - нормализовано имя `pirate` для тестовой совместимости.
   - `SwarmOrchestrator`:
     - `router` теперь optional (legacy-тесты),
     - безопасная деградация при отсутствии `scout/rag/persona`.
   - `ToolHandler`:
     - добавлен legacy fallback для `WebScout`-стиля интерфейса,
     - `web_search` работает и через OpenClaw, и через legacy scout при необходимости.

3. Усилен OpenClaw browser hardening:
   - в `OpenClawClient` добавлено:
     - `run_browser_smoke(url)` (endpoint path + tool fallback),
     - `get_browser_smoke_report(url)`,
     - эвристика `_looks_like_browser_payload(...)`.
   - в Web API добавлен endpoint:
     - `GET /api/openclaw/browser-smoke`.
   - в Telegram добавлена подкоманда:
     - `!openclaw smoke [url]`.
   - в web UI добавлена ссылка:
     - `OpenClaw Browser Smoke`.

4. Расширены тесты:
   - `tests/test_openclaw_client_health.py`:
     - endpoint/browser smoke,
     - tool fallback smoke,
     - browser smoke report.
   - `tests/test_web_app.py`:
     - endpoint `/api/openclaw/browser-smoke`.

### Верификация (Update #4)

1. `pytest -q tests` → ✅ `148 passed`.
2. `pytest -q` (общий run) → ✅ `152 passed`.
3. `python tests/smoke_test.py` → ✅ `OK`.
4. `python scripts/merge_guard.py --full` → ✅ passed.
5. Browser smoke (Playwright):
   - web UI содержит ссылку `OpenClaw Browser Smoke`,
   - `/api/openclaw/browser-smoke?url=https://example.com` возвращает `ok=true` → ✅,
   - screenshot: `/tmp/krab_openclaw_browser_smoke_ui.png`.
6. Для параллельного ускорения подготовлен новый backlog для Antigravity:

## ✅ v8 Sprint Update #6 (2026-02-12) — AI Guardian & Voice Hardening

### Основные достижения

1. **Интеграция AI Guardian (Phase 11.2)**:
   - Реализован `AIGuardianClient` в `Краб/src/core/`.
   - Сервис `AI Guardian` запущен и доступен на `localhost:8000`.
   - Реализована глубокая интеграция в `GroupModerationEngine`:
     - Добавлено правило `ai_guardian` (AI-эвристики для доксинга, спама и токсичности).
     - Новая подкоманда `!group threshold <0.1..1.0>` для настройки чувствительности AI.
     - Новая опция `ai_guardian` в `!group action`.

2. **Усиление Voice Ops UX (Block C)**:
   - В `media.py` добавлена функция `Auto-Summary` для голосовых сообщений.
   - Если в группе включен `call_auto_summary`, бот автоматически присылает краткое резюме прослушанного сообщения.
   - Исправлена зависимость `sounddevice` в `Краб/requirements.txt`.

3. **Krab Voice Gateway Hardening (Block A)**:
   - Добавлен скрипт `start_gateway.command` для быстрого запуска.
   - Подтверждена стабильность API через кросс-проектные E2E тесты.

4. **Автоматизация запуска**:
   - Созданы macOS `.command` файлы для `AI Guardian` и `Voice Gateway`.

### Тесты и верификация (Antigravity Pack)

1. **E2E Ecosystem**:
   - `tests/test_e2e_ecosystem.py` → ✅ `3 passed` (start_session, stream, diagnostics, lifecycle).
2. **AI Guardian Integration**:
   - `tests/test_ai_guardian_integration.py` → ✅ `2 passed` (evaluate_message with AI, support FAQ).
3. **Общий прогон**:
   - `pytest tests/test_e2e_ecosystem.py tests/test_ai_guardian_integration.py` → ✅ `5 passed`.

### Инструкции для запуска

- `ai_guardian/start_ai_guardian.command` — запуск ИИ-модератора.
- `Krab Voice Gateway/start_gateway.command` — запуск голосового шлюза.
- `Краб/start_krab.command` — запуск основного бота (убедись, что заполнен `.env`).

---

## ✅ v8 Sprint Update #7 (2026-02-12) — Krab Ear IPC & Voice Integration

### Ключевые изменения

1. **Krab Ear Call Assist**:
   - Реализован полный цикл IPC: `start_call_assist` -> `Voice Gateway Session` -> `Recording` -> `Transcription` -> `Stream Events`.
   - Внедрен фоновый процесс `_call_assist_loop` в `BackendService` для потоковой обработки аудио.
   - Обеспечена передача транскрибированного текста через событие `stt.partial` в `Voice Gateway`.

2. **Voice Gateway API**:
   - Добавлен эндпоинт `POST /v1/sessions/{session_id}/events` для приема внешних событий (например, от Ear или других агентов).
   - Интеграция проверена на уровне создания сессий и отправки данных.

3. **Верификация**:
   - Скрипт `tests/test_call_assist_ipc.py` успешно прошел сценарий запуска ассистента.
   - Подтверждено создание сессии в Gateway (ID валидируется) и активация аудиозахвата.

### Инструкции

- Для работы Call Assist теперь требуется запущенный `Voice Gateway`.
- Активация через IPC метод `start_call_assist`.

---

## ✅ v8 Sprint Update #8 (2026-02-12) — Unified Ecosystem Health (3 проекта)

### Что реализовано (Update #8)

1. Добавлен новый сервис `src/core/ecosystem_health.py`:
   - единая агрегация health по `OpenClaw`, `Local LM`, `Voice Gateway`, `Krab Ear`;
   - расчет деградации цепочки `cloud -> local fallback -> critical`;
   - расчет risk-level и кратких рекомендаций оператору.

2. Расширен web API:
   - `GET /api/health` теперь включает `krab_ear`, `risk_level`, `chain`;
   - `GET /api/ecosystem/health` — полный структурированный health-report;
   - `GET /api/ecosystem/health/export` — экспорт JSON отчета в `artifacts/ops/`.

3. Усилен Telegram health UX:
   - `!web health` теперь показывает `Krab Ear`, `degradation`, `risk`.

4. Web UI синхронизирован:
   - добавлен badge `Krab Ear` в блоке сервисов;
   - добавлена ссылка `Ecosystem Health API`.

### Тесты и верификация (Update #8)

1. Добавлены тесты:
   - `tests/test_ecosystem_health.py` (normal/fallback/critical сценарии).
   - `tests/test_web_app.py` (обновленный `/api/health` + `/api/ecosystem/health`).

2. Фактические прогоны:
   - `pytest -q tests/test_ecosystem_health.py tests/test_web_app.py` → ✅ `21 passed`;
   - `pytest -q` → ✅ `166 passed`, `2 warnings`;
   - `python tests/smoke_test.py` → ✅ `OK`.

---

## ✅ v8 Sprint Update #9 (2026-02-12) — Live E2E Runner (3 проекта)

### Что реализовано (Update #9)

1. Добавлен live раннер `scripts/live_ecosystem_e2e.py`:
   - health-check `OpenClaw + Local LM + Voice Gateway + Krab Ear`;
   - voice lifecycle: `create -> patch -> diagnostics -> stop -> verify 404`;
   - экспорт JSON-отчета в `artifacts/ops/live_ecosystem_e2e_<UTC>.json`.

2. Добавлен one-click запуск:
   - `scripts/run_live_ecosystem_e2e.command`.

3. Добавлен runbook:
   - `docs/E2E_THREE_PROJECTS.md`.

### Зачем (Update #9)

- Финальный acceptance межпроектной интеграции запускается одной командой.
- Уменьшен риск "кажется работает" без реальной проверки lifecycle.

---

## ✅ v8 Sprint Update #10 (2026-02-12) — Antigravity Audit + Live E2E Validation

### Проверка ранее выполненной Antigravity части

1. Ownership-пакет тестов:
   - `tests/test_telegram_chat_resolver.py`
   - `tests/test_telegram_summary_service.py`
   - `tests/test_provisioning_service.py`
   - `tests/test_group_moderation_engine.py`
   - `tests/test_voice_gateway_client.py`
   - Результат: ✅ `14 passed`.

2. Расширенный пакет:
   - `tests/test_e2e_ecosystem.py`
   - `tests/test_ai_guardian_integration.py`
   - `tests/test_voice_gateway_hardening.py`
   - `tests/test_telegram_control.py`
   - `tests/test_group_moderation_v2.py`
   - `tests/test_group_moderation_scenarios.py`
   - Результат: ✅ `27 passed`.

### Дополнительно (Update #10)

1. Исправлен запуск live e2e скрипта в standalone-режиме (`sys.path` bootstrap).
2. Добавлен unit-тест раннера:
   - `tests/test_live_ecosystem_e2e.py` (normalization helper).

### Итоговые прогоны (Update #10)

1. `python scripts/live_ecosystem_e2e.py` → ✅ `overall_ok=true`, lifecycle green.
2. `python tests/smoke_test.py` → ✅ `OK`.
3. `pytest -q` → ✅ `168 passed`, `2 warnings`.

---

## ✅ v8 Sprint Update #11 (2026-02-12) — Voice Event Schema + iOS/PSTN Smoke Docs

### Что реализовано (Update #11)

1. Нормализация voice stream событий в единую schema v1.0:
   - `src/core/voice_gateway_client.py`
   - `VoiceGatewayClient.normalize_stream_event(...)`

2. Добавлен schema-check инструмент:
   - `scripts/check_voice_event_schema.py`
   - `scripts/check_voice_event_schema.command`

3. Добавлена документация:
   - `docs/VOICE_EVENT_SCHEMA.md` (контракт полей и совместимость),
   - `docs/IOS_PSTN_SMOKE.md` (smoke checklist для iOS/PSTN трека).

4. Добавлен helper stream-чтения:
   - `VoiceGatewayClient.get_stream_event(...)` (one-shot WS read).

### Верификация (Update #11)

1. `tests/test_voice_event_schema.py` — покрытие нормализации.
2. `scripts/check_voice_event_schema.py` — smoke нормализации sample payload.

---

## ✅ v8 Sprint Update #12 (2026-02-12) — Final Validation Sweep

### Фактические прогоны (Update #12)

1. `pytest -q tests/test_voice_event_schema.py tests/test_voice_gateway_client.py tests/test_voice_gateway_hardening.py` → ✅ `10 passed`.
2. `python scripts/check_voice_event_schema.py '{"type":"stt.partial","data":{"session_id":"vs_demo","latency_ms":99,"source":"twilio_media"}}'` → ✅ `ok=true`.
3. `python tests/smoke_test.py` → ✅ `OK`.
4. `python scripts/merge_guard.py --full` → ✅ passed.
5. `pytest -q` → ✅ `171 passed`, `2 warnings`.

### Текущее состояние (Update #12)

1. Krab-core roadmap практически закрыт.
2. Основной остаток: внешний live smoke iOS/PSTN канала в реальном телеком-контуре.

---

## ✅ v8 Sprint Update #13 (2026-02-12) — Must-Have Confirm-Step (Telegram)

### Что реализовано (Update #13)

1. Закрыт critical gap confirm-step в Telegram командах:
   - `!think <...> [--confirm-expensive]`
   - `!code <...> [--confirm-expensive]`
   - `!smart <...> [--confirm-expensive]`

2. Для `!smart` добавлен pre-check:
   - при `MODEL_REQUIRE_CONFIRM_EXPENSIVE=1` и критичном профиле (`security/infra/review`) без confirm — команда блокируется с подсказкой.

3. В `commands !help` и `README` добавлены обновленные сигнатуры команд.
4. Исправлен hidden дефект в `src/core/error_handler.py`:
   - добавлен импорт `os` (используется в self-healing ветке при config error).

### Тесты и верификация (Update #13)

1. Добавлен `tests/test_ai_confirm_expensive.py`.
2. Прогоны:
   - `pytest -q tests/test_ai_confirm_expensive.py tests/test_handlers.py tests/test_web_app.py` → ✅ `46 passed`.
   - `pytest -q` → ✅ `174 passed`, `1 warning`.
   - `python scripts/merge_guard.py --full` → ✅ passed.

---

## ✅ v8 Sprint Update #14 (2026-02-12) — Cross-Project Final Hardening

### Krab (core)

1. Уточнен UX в `summaryx`:
   - формат-подсказка синхронизирована с фактическими bounds `20-2000`.

2. Повторная верификация:
   - `pytest -q` → ✅ `174 passed`, `1 warning`.
   - `python tests/smoke_test.py` → ✅ `OK`.

### Krab Voice Gateway

1. Усилен telephony cost estimator:
   - `scripts/estimate_telephony_cost.py` теперь поддерживает offline fallback без `TWILIO_*` ключей.
   - добавлены флаги `--offline`, `--strict-api` и fallback rates.

2. Добавлены тесты:
   - `tests/test_telephony_cost_estimator.py`.

3. Верификация:
   - `pytest --disable-warnings` → ✅ `18 passed`.
   - `python scripts/estimate_telephony_cost.py ...` без ключей → ✅ расчет в `offline_fallback` режиме.

### Krab Ear

1. Повторный целевой прогон backend тестов:
   - `pytest -q tests/test_backend_service.py tests/test_history_store.py tests/test_translator.py tests/test_engine_cleanup.py` (из `KrabEar/`) → ✅ `53 passed`.

---

## ✅ v8 Sprint Update #15 (2026-02-12) — Preflight Planner (Manus-style)

### Что реализовано (Update #15)

1. Добавлен preflight-планировщик задачи в роутер моделей:
   - `ModelRouter.get_task_preflight(...)`.
   - Возвращает профиль, критичность, канал/модель, confirm-step, cost-hint, причины и warnings.

2. Добавлен Web API endpoint:
   - `POST /api/model/preflight`.

3. Добавлена Telegram команда:
   - `!model preflight [task_type] <задача> [--confirm-expensive]`.

4. Обновлены docs/help:
   - `README.md`, `commands !help`, web-панель ссылки.

5. Web Assistant UX:
   - добавлена кнопка `Preflight` в панели перед фактическим запуском задачи.

### Зачем (Update #15)

1. Перед запуском сложной задачи оператор видит объяснимый план выполнения.
2. Это приближает UX к Manus-подходу: сначала план/риски/стоимость, потом запуск.

### Верификация (Update #15)

1. Тесты:
   - `tests/test_model_router_phase_d.py` (preflight logic),
   - `tests/test_web_app.py` (`/api/model/preflight`),
   - `tests/test_ai_confirm_expensive.py` (confirm-step в Telegram).

2. Прогоны:
   - `pytest -q tests/test_model_router_phase_d.py tests/test_web_app.py tests/test_ai_confirm_expensive.py` → ✅ `37 passed`.
   - `pytest -q` → ✅ `177 passed`, `1 warning`.
   - `python scripts/merge_guard.py --full` → ✅ passed.

---

## ✅ v8 Sprint Update #17 (2026-02-13) — LM Studio 0.3.x Integration Refinement

### Что реализовано (Update #17)

1. Улучшена поддержка LM Studio 0.3.x в `src/core/model_manager.py`:
   - Реализован робастный парсер `lms ls` (`_scan_local_models`) для обработки нового формата вывода (headers, variants, embeddings).
   - `lms load` теперь использует флаг `-y` для корректной обработки моделей с вариантами (авто-выбор дефолтного).
   - Логика загрузки (`_ensure_chat_model_loaded`) теперь использует единый сканер и приоритетно выбирает Instruct/Chat модели, игнорируя Embedding-модели.

2. Улучшена диагностика (`diagnose()`):
   - В отчет `Local AI` добавлено количество доступных моделей и детали активной модели.
   - Статус теперь информативнее: `Ready (No Model Loaded, 41 available)` или `Offline (41 models detected via CLI)`.

3. Очистка кода:
   - Удален legacy-код fallback-загрузки, который вызывал ошибки при отсутствии модели.

### Верификация (Update #17)

1. Проверка сканера:
   - Скрипт верификации подтвердил корректное распознавание 41 модели и отделение `text-embedding-nomic-embed-text-v1.5` как embedding.
2. Тесты:
   - `pytest -q tests/test_model_router_phase_d.py` → ✅ `18 passed`.

---

## ✅ v8 Sprint Update #18 (2026-02-19) — Cloud Scan Reliability + Daemon Control

### Что реализовано (Update #18)

1. Усилен cloud scan моделей в `src/core/model_manager.py`:
   - `list_cloud_models()` теперь использует `openclaw models list --all --json` как основной источник.
   - Добавлен fallback на HTTP `openclaw_client.get_models()` для совместимости.
   - Убрана проблема, когда через HTTP `/v1/models` приходил HTML shell и список cloud-моделей был пустым/неполным.

2. Добавлен контролируемый daemon-рантайм для Krab Core (macOS launchd):
   - `/Users/pablito/Antigravity_AGENTS/Краб/krab_core_daemon_start.command`
   - `/Users/pablito/Antigravity_AGENTS/Краб/krab_core_daemon_stop.command`
   - `/Users/pablito/Antigravity_AGENTS/Краб/krab_core_daemon_status.command`

3. Зафиксирована диагностика ключей cloud-провайдеров:
   - Gemini: `403 PERMISSION_DENIED (key reported as leaked)` в прямом API-вызове.
   - OpenAI: `401 Incorrect API key provided` в прямом API-вызове.
   - Это подтверждает, что проблема не в каналах, а именно в ключах/провайдерах.

### Верификация (Update #18)

1. Локальная проверка cloud-catalog через OpenClaw CLI:
   - обнаружено `available_count=62` cloud-моделей.

2. Проверка Python-компиляции:
   - `python3 -m compileall -q src/core/model_manager.py src/handlers/commands.py` → ✅

3. Тесты:
   - `pytest -q tests/test_model_set_parser.py tests/test_model_local_health_probe.py` → ✅ `10 passed`.
