# AGENTS.md — Krab v7.0

> **Инструкция для AI-агентов** (Antigravity, Cursor, Claude, Gemini, Copilot).
> Все комментарии в коде и документация — **на русском**.

---

## Проект

**Krab** — Telegram AI Userbot. Персональный ассистент с AI-мозгом (Gemini Cloud + Local LLM),
голосом (MLX Whisper + TTS), зрением (Gemini Vision), памятью (RAG + BlackBox),
веб-разведкой (DuckDuckGo) и автономными агентами.

---

## Tech Stack

- **Язык:** Python 3.13+
- **Telegram:** Pyrogram 2.0
- **AI:** Google Gemini SDK, LM Studio / Ollama (local)
- **RAG:** ChromaDB + sentence-transformers
- **Audio:** MLX Whisper (Apple Silicon), gTTS
- **Логирование:** structlog
- **Тесты:** pytest + smoke_test.py (45 тестов)
- **OS:** macOS (Apple Silicon M-series)

---

## Quick Start

```bash
# 1. Виртуальное окружение (уже создано)
source .venv/bin/activate

# 2. Запуск бота
python -m src.main

# 3. Smoke-тесты (ОБЯЗАТЕЛЬНО перед push)
PYTHONPATH=. .venv/bin/python tests/smoke_test.py

# 4. Unit-тесты
pytest tests/ -v

# 5. Запуск одним кликом на macOS
open start_krab.command
```

---

## Структура проекта

```
src/
├── main.py              # Оркестратор — точка входа
├── core/                # Ядро (14 модулей)
│   ├── model_manager.py # AI Router: local + cloud
│   ├── rag_engine.py    # RAG v2.0 (ChromaDB)
│   ├── config_manager.py# YAML-конфигурация
│   ├── security_manager.py # Безопасность
│   ├── mcp_client.py    # MCP интеграция
│   ├── scheduler.py     # APScheduler задачи
│   ├── memory_archiver.py # Infinite Memory
│   ├── agent_manager.py # Swarm Intelligence (Phase 6)
│   ├── swarm.py         # Parallel task orchestrator
│   ├── tool_handler.py  # Function calling
│   ├── persona_manager.py # Персоны AI
│   ├── context_manager.py # Контекст диалогов
│   ├── error_handler.py # Обработка ошибок
│   ├── rate_limiter.py  # Rate limiting
│   ├── logger_setup.py  # structlog настройка
│   └── supervisor.py    # Watchdog + auto-restart
├── handlers/            # Обработчики команд (9 модулей)
│   ├── commands.py      # !help, !status, !model, !diagnose
│   ├── ai.py            # AI-ответы и reasoning
│   ├── tools.py         # !research, !scout, !nexus, !news, !translate, !say
│   ├── system.py        # !sh, !commit, !sysinfo, !refactor, !panic
│   ├── media.py         # !see, !hear — мультимедиа
│   ├── rag.py           # !rag — поиск по памяти
│   ├── persona.py       # !persona — управление ролями
│   ├── scheduling.py    # !remind — напоминания
│   ├── auth.py          # Авторизация и безопасность
│   └── mac.py           # !mac — macOS интеграция
├── modules/             # Внешние модули
│   ├── perceptor.py     # Audio (Whisper) + Vision (Gemini)
│   └── screen_catcher.py# Скриншоты
└── utils/               # Утилиты
    ├── web_scout.py     # WebScout v2.0 + deep_research()
    ├── black_box.py     # SQLite лог сообщений
    ├── self_refactor.py # AI самоанализ кода
    ├── system_monitor.py# Системные метрики
    └── dashboard_app.py # Streamlit dashboard
```

---

## Переменные окружения (.env)

```bash
TELEGRAM_API_ID=...          # Обязательно
TELEGRAM_API_HASH=...       # Обязательно
TELEGRAM_SESSION_NAME=kraab_pure_debug
OWNER_USERNAME=@yung_nagato
ALLOWED_USERS=user1,user2
GEMINI_API_KEY=...           # Обязательно для cloud AI
LM_STUDIO_URL=http://192.168.0.171:1234
LOG_LEVEL=INFO
```

---

## Конвенции кода

1. **Язык комментариев:** Русский
2. **Docstring:** В начале каждого файла и класса — на русском
3. **Импорты:** stdlib → сторонние → внутренние, разделённые пустой строкой
4. **Логирование:** `structlog` (НЕ `logging`)
5. **Ошибки:** `@safe_handler` декоратор для обработчиков
6. **Тесты:** Smoke + pytest. Все тесты должны пройти перед push
7. **Версии:** При обновлении версии — менять во ВСЕХ файлах (main.py, commands.py, dashboard_app.py)

---

## Roadmap

Полный мастер-план: **task.md** в `brain/` артефактах (19+ фаз, 200+ задач).

**Текущая цель:** Фаза 3 (v7.5) — Миграция Gemini SDK + Тотальная Очистка.

---

## Известные проблемы

- `google.generativeai` FutureWarning → нужна миграция на `google.genai`
- `streamlit` не установлен (dashboard опционален)
- Рудименты в `src/`: `userbot_bridge.py`, `openclaw_client.py` и др. (см. Фаза 3 в task.md)

---

**Last Updated:** 2026-02-11 | **Version:** v7.0
