# PROJECT_STATE_MIGRATION.md

Документ для переноса разработки в другую IDE. Состояние после рефакторинга Phase 9+, Stage A и Stage B.

---

## 1. Текущая архитектура OpenClaw и Krab

### 1.1 Общий поток

- **Telegram** → `KraabUserbot` (`src/userbot_bridge.py`) → команды через `run_cmd` + guard в `_process_message` → либо **CommandHandlers** (group=-1), либо **AI pipeline** (OpenClaw).
- **AI pipeline**: сообщение (текст + опционально фото) → `openclaw_client.send_message_stream()` → выбор модели через `model_manager.get_best_model(has_photo=...)` → при локальной модели `model_manager.ensure_model_loaded()` → запрос в **OpenClaw Gateway** (`OPENCLAW_URL`, обычно `http://127.0.0.1:18789`) → потоковый ответ.
- **OpenClaw Gateway** — внешний сервис: принимает `/v1/chat/completions`, проксирует к LM Studio или облаку (Gemini). Krab — клиент (Bearer token), не реализует сам gateway.

### 1.2 Ключевые компоненты

| Компонент | Файл | Назначение |
|-----------|------|------------|
| **KraabUserbot** | `src/userbot_bridge.py` | Telegram MTProto, команды, вызов OpenClaw, фото → base64, guard и stop propagation |
| **CommandHandlers** | `src/handlers/command_handlers.py` | Обработчики команд: !status, !help, !model, !clear, !config, !set, !diagnose и др. |
| **ModelManager** | `src/model_manager.py` | Синглтон: LM Studio v1 (load/unload/list), Lock, free_vram, cooling 1.5s, vision-aware выбор модели |
| **ModelRouter** | `src/core/model_router.py` | Выбор модели: local / cloud по цепочке fallback, при has_photo — облачная vision (gemini-2.5-flash) |
| **OpenClawClient** | `src/openclaw_client.py` | HTTP-клиент к OpenClaw: сессии по chat_id, sliding window истории, has_photo → get_best_model, fallback на прямой вызов LM Studio при ошибке |
| **local_health** | `src/core/local_health.py` | LM Studio: доступность, список моделей (API v1 → v0 fallback), эвристика vision (vl, vision, glm-4), models_discovered в DEBUG |
| **cloud_gateway** | `src/core/cloud_gateway.py` | Цепочка облачных моделей (tier 1/2/3), Gemini API, get_cloud_fallback_chain(), verify_gemini_access |
| **WebRouterCompat** | `src/modules/web_router_compat.py` | Адаптер для web-панели: делегирует в ModelManager и OpenClawClient |
| **WebApp** | `src/modules/web_app.py` | FastAPI, порт 8080 (или через deps), панель и API статуса/запросов к ассистенту |

### 1.3 Конфигурация

- `src/config.py`: `OPENCLAW_URL`, `OPENCLAW_TOKEN`, `LM_STUDIO_URL` (без trailing slash), `GEMINI_API_KEY*`, `MODEL`, `FORCE_CLOUD`, `LOCAL_PREFERRED_MODEL`, `MAX_RAM_GB`, `HISTORY_WINDOW_*`.
- Переменные окружения через `.env` (dotenv). Не коммитить секреты.

### 1.4 Режимы роутинга

- **local** — принудительно локальная модель (LM Studio).
- **cloud** — принудительно облачная (`config.MODEL`, обычно Gemini).
- **auto** — автоматический выбор: локаль по цепочке, при недоступности — облако. При `has_photo` — локальная vision при наличии, иначе облачная vision.

---

## 2. Что уже сделано (Stage A + B)

- **Stop propagation (Stage A1)**  
  - В `run_cmd` в `userbot_bridge.py`: в `finally` всегда вызывается `m.stop_propagation()`.  
  - В `_process_message`: если строка начинается с `!`/`/`/`.` и первое слово есть в `_known_commands`, выполняется ранний return (команда не уходит в AI pipeline).

- **!help (Stage A2)**  
  - `handle_help` в `command_handlers.py`: категории Core, AI/Model, Tools, System, Dev в формате v7.2. Зарегистрирован в `_setup_handlers()`.

- **!model и подкоманды (Stage A3)**  
  - `!model` — статус (режим, активная модель, облачная модель, LM Studio URL, FORCE_CLOUD).  
  - `!model local` / `cloud` / `auto` — переключение режима.  
  - `!model load <name>` — загрузка через `model_manager.load_model()`.  
  - `!model unload` — выгрузка через `model_manager.free_vram()`.  
  - `!model scan` — единый список: блок «☁️ Облачные» (get_cloud_fallback_chain + API), блок «💻 Локальные» (LM Studio), без жёсткого лимита в 20 строк.

- **LM Studio API v1 (Stage B1)**  
  - Load: `/api/v1/models/load` → fallback `/v1/models/load`.  
  - Unload: `/api/v1/models/unload` (instance_id) → fallback `/v1/models/unload` (model).  
  - List: `/api/v1/models` → fallback `/v1/models`. Нормализация ответа (key/id, display_name/name, capabilities.vision).  
  - `LM_STUDIO_URL` в config без trailing slash.

- **VRAM и Lock (Stage B2)**  
  - В `ModelManager`: `self._lock = asyncio.Lock()`, все load/unload под lock.  
  - `get_loaded_models()`, `free_vram()` (выгрузка всех + синхронизация `_current_model`).  
  - После unload / free_vram — `await asyncio.sleep(1.5)`.  
  - В load_model при нехватке RAM — вызов free_vram без deadlock (lock отпускается перед free_vram).

- **Vision-aware routing (Stage B3)**  
  - В `openclaw_client.send_message_stream`: `has_photo = bool(images)`, передаётся в `model_manager.get_best_model(has_photo=has_photo)`.  
  - В ModelManager при has_photo и не FORCE_CLOUD: приоритет локальной модели с supports_vision (vl/vision/glm-4 + capabilities.vision из API). Иначе — облачная vision через ModelRouter (gemini-2.5-flash).  
  - В LM Studio fallback payload с изображениями не обрезается (используется полная история сессии).

- **Прочие правки**  
  - Лог `models_discovered` в `local_health.py` переведён на DEBUG.  
  - В `openclaw_stream_start` добавлено логирование `has_photo`.

---

## 3. Нерешённые задачи (Stage C и сопутствующее)

- **Сохранение режима роутинга в JSON (Stage C5)**  
  - Режим (local/cloud/auto) и при необходимости FORCE_CLOUD хранятся только в памяти (`config.FORCE_CLOUD` и т.п.).  
  - Нужно: сохранять в файл (например `runtime_state.json` или путь из `config.RUNTIME_STATE_PATH`), загружать при старте, чтобы после перезапуска режим восстанавливался.

- **Фикс ошибки 400 через автозагрузку модели в пайплайне**  
  - При запросе к OpenClaw с моделью, которая в LM Studio не загружена, возможна ошибка 400 (или аналог).  
  - Нужно: в пайплайне перед отправкой запроса (или при получении 400 из-за «модель не загружена») вызывать автозагрузку нужной локальной модели (например через `model_manager.ensure_model_loaded(model_id)` или аналог) и при необходимости повтор запроса.

- **Добавление !ops (Stage C2)**  
  - Реализовать `handle_ops`: базовая сводка по использованию (cost_analytics / observability).  
  - Зарегистрировать в `handlers/__init__.py` и в `_setup_handlers()` в `userbot_bridge.py`, добавить в `_known_commands`.

- **Прочие пункты Stage C (по плану)**  
  - C1: расширить !status (LM Studio статус, загруженная модель, режим роутинга, версия OpenClaw из /health).  
  - C3: кросс-канальный аудит (Telegram, web, OpenClaw) — одинаковое поведение local/cloud/auto и vision fallback.  
  - C4: юнит- и интеграционные тесты (stop propagation, !help/!model, LM Studio v1, free_vram, vision routing).  
  - C5 (доп.): убрать дубликат блока `if not full_response` в `userbot_bridge.py`; синхронизировать дефолт `LM_STUDIO_URL` с политикой конфига.

---

## 4. Полезные ссылки по коду

- План: `krab_phase_9+_unified` (в Cursor plans или аналог).  
- Правила реализации: `technical_addendum_phase9.md` в корне проекта.  
- Запуск: см. `run_krab.sh`, `Krab.command`, конфиг в `src/config.py` и `.env`.
