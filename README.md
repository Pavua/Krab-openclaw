
# 🦀 Краб (Krab) v6.0 — Elite AI Userbot

**Персональный AI-ассистент, интегрированный в Telegram-аккаунт.**
Работает на базе LM Studio (Local), Google Gemini (Cloud) и OpenClaw Gateway.
Модульная архитектура, автономный агент, мультимодальность.

---

## 🚀 Быстрый старт (v7.6 OpenClaw Edition)

1. **Активация**: `source .venv/bin/activate`
2. **Запуск**: `./start_krab.command`
3. **Web-панель**: открой `http://127.0.0.1:8080` (или `!web` в Telegram)
4. **One-click (macOS)**: двойной клик по `Start Dashboard.command`

**Требования:**
- Запущенный **OpenClaw Gateway** (на порту 8000 или другом).
- Настроенный `.env` (см. ниже).

---

## 🔥 Ключевые возможности

### 🧠 OpenClaw Intelligence (New!)
- **Central Brain**: Все сложные AI-задачи делегируются в OpenClaw Gateway.
- **Tools**: Поиск, новости и анализ теперь работают через OpenClaw Tools.
- **Thin Client**: Krab выступает как интерфейс в Telegram, а мозг — в OpenClaw.
- **Web Policy**: `web_search/web_fetch` через OpenClaw по умолчанию; локальный BrowserAgent только fallback и по флагу `ENABLE_LOCAL_BROWSER=1`.
- **Web Panel API**: `/api/stats`, `/api/health`, `/api/ecosystem/health`, `/api/ecosystem/health/export`, `/api/links`, `/api/model/recommend`, `/api/model/preflight`, `/api/model/feedback`, `/api/ops/usage`, `/api/ops/alerts`, `/api/ops/history`, `/api/ops/ack/{code}`, `/api/ops/cost-report`, `/api/ops/report`, `/api/ops/report/export`, `/api/ops/bundle`, `/api/ops/bundle/export`, `/api/ops/maintenance/prune`, `/api/openclaw/report`, `/api/openclaw/deep-check`, `/api/openclaw/remediation-plan`, `/api/openclaw/browser-smoke`, `/api/provisioning/*`.
- **Web-native Assistant**: можно работать с Крабом напрямую из панели (без Telegram) через `/api/assistant/query`.

### 🧭 Model Routing (Phase D)
- Профили задач: `chat`, `moderation`, `code`, `security`, `infra`, `review`, `communication`.
- Стратегия `free-first hybrid`: локальные модели в приоритете для простых задач, облако для критичных.
- Планировщик локалок: `1 heavy + 1 light`, heavy+heavy выполняются последовательно.
- Память выбора моделей: рекомендации для похожих задач и статистика использования.
- Guardrails: soft-cap по облачным вызовам (`CLOUD_SOFT_CAP_CALLS`) + бюджетные алерты (`CLOUD_MONTHLY_BUDGET_USD`, `MONTHLY_CALLS_FORECAST`).
- Preflight перед запуском: `!model preflight [task_type] <задача> [--confirm-expensive]` и `POST /api/model/preflight`.
- Adaptive Feedback Loop: `!model feedback <1-5> [note]`, `!model feedback <1-5> <profile> <model> [channel] [note]`, `!model stats [profile]`.
- Quality-aware рекомендации: роутер учитывает user-feedback при выборе моделей.

### 🎭 Персоны
- `!personality list` — список доступных ролей
- `!personality coder` — Python/JS сеньор
- `!personality analyst` — Аналитик данных
- Динамическая смена личности "на лету"

### 🌐 Инструменты (via OpenClaw)
- `!scout <запрос>` — Deep Research (веб-поиск + AI-аналитика)
- `!translate <текст>` — Авто-определение языка и перевод
- `!say <текст>` — Озвучка текста (TTS)
- `!news <тема>` — Сводка новостей по теме
- `!see` — Скриншот экрана + AI-описание
- `!callstart [auto_to_ru|ru_es_duplex] [mic|system_audio|mic_plus_system] [on|off] [local|cloud|hybrid]` — старт звонковой voice-сессии
- `!callstatus` — статус активной voice-сессии
- `!callstop` — остановка активной voice-сессии
- `!notify on|off` — политика уведомления собеседника
- `!calllang auto_to_ru|ru_es_duplex` — смена языка/режима в сессии
- `!calldiag` — диагностический срез (latency/counters/fallback/cache)
- `!callsummary [max_items]` — краткая сводка звонка и список задач
- `!callphrase <текст> [ru->es|es->ru]` — быстрая реплика с мгновенным переводом/озвучкой
- `!callphrases [ru->es|es->ru]` — библиотека быстрых фраз
- `!callwhy` — explain-диагностика причины отсутствия перевода
- `!calltune [adaptive|low|stable] [latency_ms] [vad] — runtime тюнинг буфера/VAD
- `!summaryx <X> [target] [--focus "тема"]` — summary реальных сообщений выбранного Telegram-чата
- `!chatid` — ID/тип/название текущего чата

### 🧩 Provisioning (Phase E)
- `!provision templates [agent|skill]` — шаблоны ролей.
- `!provision draft <agent|skill> <name> <role> <описание>` — создать draft.
- `!provision preview <draft_id>` — diff перед применением.
- `!provision apply <draft_id> confirm` — безопасный apply в каталог.
- Каталоги: `config/agents_catalog.yaml`, `config/skills_catalog.yaml`.

### 🛠 Система
- `!status` — Здоровье систем (интерактивные кнопки)
- `!diagnose` — Полная диагностика (CPU, RAM, AI, RAG)
- `!web` / `!web health` — URL панели и health экосистемы
- `!ops` — usage/alerts по маршрутизации моделей и cloud/local расходу
- `!ops history [N]` — история ops-снимков
- `!ops cost [monthly_calls]` — cost report (оценка расходов)
- `!ops report [N]` — полный ops-report
- `!ops export [N]` — экспорт ops-report в JSON
- `!ops bundle [N]` — ops-report + health snapshot в одном JSON
- `!ops prune [days] [keep]` — очистка ops history по retention
- `!ops ack <code> [note]` — подтвердить alert
- `!ops unack <code>` — снять подтверждение alert
- `!openclaw [status|auth|browser|tools|deep|plan|smoke]` — глубокая диагностика OpenClaw (включая auth readiness, remediation и browser smoke)
- `scripts/health_dashboard.command` — единый health JSON по цепочке `cloud -> local fallback -> voice -> krab ear`.

### 🌐 Web-first режим (без Telegram)
- В панели добавлен блок **Web Assistant** (prompt/task_type/RAG).
- В блоке Assistant есть **Preflight** кнопка (проверка маршрута до запуска).
- API:
  - `GET /api/assistant/capabilities`
  - `POST /api/assistant/query`
  - `POST /api/model/preflight`
  - `GET /api/model/feedback`
  - `POST /api/model/feedback`
- Если задан `WEB_API_KEY`, запросы в write-endpoints идут с заголовком `X-Krab-Web-Key`.
- Для защиты от перегруза: `WEB_ASSISTANT_RATE_LIMIT_PER_MIN` (по умолчанию 30 запросов/мин на клиента).
- Для дедупликации повторных write-запросов: `X-Idempotency-Key` + `WEB_IDEMPOTENCY_TTL_SEC` (по умолчанию 300 сек).

### 🤝 Параллельная разработка (Codex + Antigravity)
- Разделение ownership: `config/workstreams/codex_paths.txt` и `config/workstreams/antigravity_paths.txt`.
- Проверка конфликтов перед merge: `scripts/check_workstream_overlap.command`.
- Подробный протокол: `docs/parallel_execution_split_v8.md`.
- Быстрый пакет запуска Antigravity:
  - `docs/ANTIGRAVITY_START_HERE.md`
  - `docs/ANTIGRAVITY_WORKSTREAM_PROMPT.md`
  - `docs/ANTIGRAVITY_BACKLOG_V8.md`
  - `docs/ANTIGRAVITY_NEXT_SPRINTS_V8.md`
  - `docs/ANTIGRAVITY_REMAINING_V8.md`
  - `scripts/start_antigravity_parallel.command`
- Merge guard перед интеграцией:
  - `scripts/merge_guard.command`
  - `python scripts/merge_guard.py --full`
- One-click полная проверка:
  - `scripts/run_v8_full_validation.command`
- One-click экспорт Ops отчета:
  - `scripts/export_ops_report.command`
- One-click ops bundle (report + health):
  - `scripts/generate_ops_bundle.command`
- One-click live E2E (3 проекта):
  - `scripts/run_live_ecosystem_e2e.command`
  - `docs/E2E_THREE_PROJECTS.md`
- Voice schema + PSTN/iOS smoke:
  - `docs/VOICE_EVENT_SCHEMA.md`
  - `docs/IOS_PSTN_SMOKE.md`
  - `scripts/check_voice_event_schema.command`
- `!config` — Горячая перезагрузка конфигурации
- `!sh <cmd>` — Выполнение shell-команд (Owner only)
- `!commit <msg>` — Git push в GitHub
- `!panic` — Stealth Mode (блокировка доступа)

### ⏰ Планирование
- `!remind 30m Обед` — Напоминания
- `!timer 5m` — Быстрый таймер
- `!mac` — Управление macOS (громкость, музыка, блокировка)

---

## 📚 Полный список команд

| Команда | Описание |
|---------|----------|
| **AI** | |
| `!think <вопрос> [--confirm-expensive]` | Глубокий reasoning (chain-of-thought) |
| `!smart <задача> [--confirm-expensive]` | Агентный воркфлоу с инструментами |
| `!code <задача> [--confirm-expensive]` | Генерация кода |
| `!learn <текст>` | Запомнить в RAG-память |
| `!exec <код>` | Выполнить Python (Owner) |
| **Медиа** | |
| 📷 Фото | Авто-анализ через Vision AI |
| 🎤 Голосовое | Авто-транскрибация (MLX Whisper) |
| 📎 Документ | Парсинг PDF/DOCX + индексация RAG |
| 🎬 Видео | Анализ через Gemini Video API |
| **Инструменты** | |
| `!scout <q>` | Deep Research (веб + AI) |
| `!translate <text>` | Перевод (авто-определение языка) |
| `!say <text>` | Text-to-Speech (Milena) |
| `!news <topic>` | Сводка новостей |
| **Система** | |
| `!status` | Статус систем |
| `!diagnose` | Полная диагностика |
| `!config` | Настройки (горячая перезагрузка) |
| `!help` | Список команд |
| `!logs` | Последние записи лога |
| `!sh <cmd>` | Shell-команда (Owner) |
| `!commit <msg>` | Git push (Owner) |
| `!panic` | Stealth Mode (Owner) |
| **Расписание** | |
| `!remind <time> <text>` | Напоминание |
| `!timer <time>` | Таймер |
| `!see [вопрос]` | Screen Awareness |
| `!mac <cmd>` | macOS Bridge |
| **Персоны** | |
| `!personality <role>` | Сменить личность |
| `!summary` | Резюме переписки |
| `!rag stats/clean/export` | Управление RAG |

---

## 📂 Структура проекта (v6.0 Modular)

```
Krab/
├── start_server_mode.command # 🐳 Docker Launcher
├── start_god_mode.command    # 🌌 Native Launcher (macOS)
├── src/
│   ├── main.py              # Оркестратор (220 строк)
│   ├── handlers/            # 🆕 Модульные обработчики
│   │   ├── __init__.py      # register_all_handlers()
│   │   ├── auth.py          # Централизованная авторизация
│   │   ├── commands.py      # !status, !diagnose, !config, !help, !logs
│   │   ├── ai.py            # !think, !smart, !code, !learn, !exec
│   │   ├── media.py         # Фото, видео, аудио, документы
│   │   ├── tools.py         # !scout, !translate, !say, !news
│   │   ├── system.py        # !sh, !commit, !sysinfo, !panic
│   │   ├── scheduling.py    # !remind, !timer, !see
│   │   ├── mac.py           # !mac (macOS Bridge)
│   │   ├── rag.py           # !rag (управление памятью)
│   │   └── persona.py       # !personality, !summary
│   ├── core/                # Ядро (18 модулей)
│   │   ├── model_manager.py # Router: Local ↔ Cloud AI
│   │   ├── rag_engine.py    # ChromaDB RAG
│   │   ├── swarm.py         # Phase 10: Swarm Intelligence
│   │   ├── security_manager.py
│   │   ├── config_manager.py
│   │   └── ...
│   └── modules/
│       └── perceptor.py     # Vision + Audio (STT/TTS)
├── tests/                   # Pytest тесты
├── config/                  # YAML конфигурация
└── .env                     # API ключи и настройки
```

## 🛡 Безопасность

- Бот отвечает **только** владельцу и пользователям из `ALLOWED_USERS`.
- Команды `!exec`, `!sh`, `!panic` доступны только Owner.
- Stealth Mode (`!panic`) — мгновенная блокировка всех команд.
- Токены в `.env`, не попадают в логи и Git.

---

## ⚙️ Конфигурация (.env)

```bash
# Telegram
TELEGRAM_API_ID=your_api_id
TELEGRAM_API_HASH=your_api_hash
TELEGRAM_SESSION_NAME=krab_v6_session
OWNER_USERNAME=your_username
ALLOWED_USERS=user1,user2

# AI Models (настраиваемые, без хардкодов)
GEMINI_API_KEY=your_key
GEMINI_CHAT_MODEL=gemini-2.0-flash
GEMINI_THINKING_MODEL=gemini-2.0-flash-thinking-exp
GEMINI_PRO_MODEL=gemini-2.0-pro-exp
GEMINI_VISION_MODEL=gemini-2.0-flash

# Local AI
LM_STUDIO_URL=http://localhost:1234/v1
WHISPER_MODEL=mlx-community/whisper-large-v3-turbo
LOCAL_VISION_ENABLED=0
LOCAL_VISION_MODEL=
LOCAL_VISION_TIMEOUT_SECONDS=90
LOCAL_VISION_MAX_TOKENS=1200

# OpenClaw
OPENCLAW_BASE_URL=http://127.0.0.1:18789
OPENCLAW_API_KEY=sk-...
OPENCLAW_REQUIRED_AUTH_PROVIDERS=openai-codex,google-gemini-cli,qwen-portal-auth

# Web API write protection
WEB_API_KEY=your_secure_key
WEB_ASSISTANT_RATE_LIMIT_PER_MIN=30
```

---

**Developed with 🦀 by Antigravity (Ralph Mode).**
