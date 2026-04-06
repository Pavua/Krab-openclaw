# Краб — Архитектурный бэклог и задачи

> Составлен: 2026-03-23 | Обновлён: 2026-04-06
> Статус: Активная разработка
> Владелец: По

---

## 🚀 Глобальное видение (Ultimate Goals)

### 1. Рой автономных агентов (Multi-Agent Swarm)
**Цель:** Создание независимых виртуальных команд (трейдеры, кодеры, аналитики), которые могут общаться между собой. Например, команда трейдеров анализирует рынок и ставит задачу команде кодеров на написание/корректировку крипто-бота. Главный фокус — окупаемость и автономный заработок.

**Статус:** 🚧 В РАЗРАБОТКЕ (R18→R20, 2026-04-06) — инфраструктура + память + расписание + инструменты:
- `src/core/swarm_bus.py`: TeamRegistry (4 команды: traders/coders/analysts/creative) + SwarmBus (межкомандное делегирование через `[DELEGATE: team]`, max_depth=2)
- `src/core/swarm.py`: AgentRoom R18 — детектирует директивы делегирования, инжектирует результат в контекст
- `src/core/swarm_memory.py`: ✅ (2026-04-05) Персистентная память между сессиями — JSON в `~/.openclaw/krab_runtime_state/swarm_memory.json`, FIFO 50 записей/команда, auto-inject в system_hint ролей
- `src/core/swarm_scheduler.py`: ✅ (2026-04-05) Рекуррентный планировщик — `!swarm schedule traders 4h BTC`, `!swarm jobs`, `!swarm unschedule <id>`, гейт `SWARM_AUTONOMOUS_ENABLED`
- ✅ (2026-04-06) **Tool access**: web_search, tor_fetch (TOR_ENABLED), peekaboo, все MCP tools. Tool awareness hint инжектируется в промпт каждой роли. `SWARM_ROLE_MAX_OUTPUT_TOKENS`=4096, `role_context_clip`=3000.
- Команды в Telegram: `!swarm traders <тема>`, `!swarm teams`, `!swarm memory [команда]`, `!swarm schedule/jobs/unschedule`

**Следующий шаг:** E2E тест tools (web_search реально вызывается и возвращает данные), затем cross-team delegation E2E (traders → coders).

### 2. Максимальный доступ к macOS (Permission Audit)
**Цель:** Дать Крабу возможность полностью управлять файловой системой, окнами и процессами без ручного вмешательства.
**Статус:** ✅ ВЫПОЛНЕНО (2026-04-05) — полный аудит `artifacts/ops/macos_permission_audit_pablito_latest.json`, `overall_ready=true`. Full Disk Access, Accessibility, Screen Recording — всё выдано.

### 3. Интеграция с умным домом (HomePod mini)
**Цель:** В будущем подключить управление HomePod mini и другими устройствами Apple прямо из контекста диалога с Крабом. (Приоритет: низкий, ждет стабилизации ядра).

---

## 🔴 Критично (Стабильность системы)

### 4. Устранение OOM-крашей в Krab Ear (Транскрибация)
**Симптом:** Параллельная обработка аудио запускает несколько инстансов Whisper, уводя систему в swap.
**Решение:** Внедрить очередь `queue.Queue()` или `.lock` файл в `krab_ear_watchdog.py` для строго последовательной обработки.

**Статус:** ✅ ИСПРАВЛЕНО (2026-03-23) — добавлен `asyncio.Lock()` в Krab Voice Gateway (`app/stt_engines.py`)
для последовательной обработки Whisper. Только один инстанс за раз — OOM устранён.

### 5. Авто-восстановление шлюза (Self-healing)
**Симптом:** При падениях шлюз OpenClaw зависает (not loaded). Вотчдог пытался перезапускать, но из-за трёх багов не мог: (a) `time.sleep(2)` слишком мало для старта, (b) нет cooldown — pkill убивал ещё стартующий шлюз, (c) дублирование логов мешало анализу.
**Статус:** ✅ ПОЛНОСТЬЮ ИСПРАВЛЕНО (2026-03-23) — трёхуровневый self-healing:
1. **macOS LaunchAgent** (`ai.openclaw.gateway.plist`, KeepAlive=true) — launchd-уровень, самовосстановление ~5с, выживает перезагрузку. Установлен через `openclaw gateway install`.
2. **`telegram_session_watchdog.py`** — retry loop (8с), cooldown 180с, "уже жив"-проверка перед pkill, фикс дублирования логов.
3. **`new start_krab.command`** — обновлён: теперь знает о LaunchAgent (не запускает nohup-конкурент), добавлен `openclaw doctor --fix` перед стартом.
4. **`new Stop Krab.command`** — обновлён: не трогает gateway при LaunchAgent (gateway — инфраструктура, живёт независимо от бота).

### 6. Таймауты Telegram API при долгих задачах
**Симптом:** При вызове множества инструментов вебхук Telegram отваливается.
**Решение:** Перевести долгие задачи на асинхронную очередь (`sendMessage`) и увеличить таймаут в конфигурации OpenClaw.
**Статус:** ✅ ПОЛНОСТЬЮ ИСПРАВЛЕНО (2026-04-04) — два уровня:
1. (2026-03-23) Увеличены таймауты в `~/.openclaw/openclaw.json`: `channels.telegram.timeoutSeconds: 180`, retry-политика `{attempts: 5, minDelayMs: 500, maxDelayMs: 60000, jitter: 0.2}`.
2. (2026-04-04) Добавлена `_TelegramSendQueue` — per-chat async queue с exponential backoff (0.5→1→2с, до 3 попыток) для всех исходящих Telegram API вызовов (`_safe_edit`, `_safe_reply_or_send_new`, voice/document send). Ленивые воркеры, автостоп через 30с простоя. Cleanup при shutdown.

---

## 🟠 Важно (Расширение функционала и UX)

### 7. Прозрачность долгих запросов (Как в нативном дашборде)
**Проблема:** В Telegram не видно, что Краб работает над задачей, кажется, что он завис.
**Решение:** - Добавить промежуточные статусы в Telegram-транспорт ("Вызываю инструмент...", "Читаю скриншот...").
- Использовать `sendChatAction` (`typing`), чтобы индикатор набора текста висел всё время, пока ИИ думает.
**Статус:** ✅ ПОЛНОСТЬЮ ИСПРАВЛЕНО (2026-04-04) — три уровня:
1. (2026-03-28) Базовый UX-контур: `typing` во время reasoning/tool-flow, delivery-actions перед отправкой вложений.
2. (2026-04-04) Granular tool-stage narration: `_TOOL_NARRATIONS` dict (25 инструментов) в `openclaw_client.py` + `_narrate_tool()` с fallback по подстроке. Вместо "🔧 Выполняется: browser" теперь "🌐 Открываю браузер...", "📸 Делаю скриншот..." и т.д. Polling каждые 4 сек, автоматическое обновление temp_msg.

### 8. Telegram-транспорт: live-smoke голосовых и hygiene ответов
**Актуализация 2026-03-27:** owner private text+voice roundtrip, mention-gated/group flow и graceful-content после raw fallback уже подтверждены живым E2E через второй Telegram MCP аккаунт `p0lrd`; детали и артефакты перенесены в `RESOLVED.md`.

### 9. Обновление Vision API и чтение скриншотов
**Симптом:** `vision_read.py` стучился в устаревшую модель `gemini-1.5-pro-latest` (ошибка 404).
**Статус:** ✅ ПОЛНОСТЬЮ ИСПРАВЛЕНО (2026-03-23) — нативный путь через OpenClaw images уже работает, `vision_read.py` не используется в основном потоке обработки фото.

Нативная интеграция подтверждена в `userbot_bridge.py` (строки 3300–3420):
- При `message.photo` фото скачивается через `client.download_media()` → конвертируется в base64 → передаётся в `send_message_stream(..., images=[b64_img])` напрямую в OpenClaw.
- `vision_read.py` как subprocess нигде не вызывается (файл отсутствует в `src/`).
- Для фото-маршрута автоматически применяются увеличенные таймауты (`_resolve_openclaw_stream_timeouts(has_photo=True)`) и принудительный cloud-роутинг (`_should_force_cloud_for_photo_route`).

### 10. Парсинг Mercadona (Anti-bot)
**Решение:** Добавить `puppeteer-extra-plugin-stealth` и перехватывать XHR/Fetch запросы API через `page.on('response')` вместо нестабильного парсинга DOM-элементов.

### 14. Обновление OpenClaw v2026.3.13 → v2026.3.23-beta.1
**Статус:** ✅ ОБНОВЛЕНО (2026-03-23)
- v2026.3.22 имел баг паковки: `dist/control-ui/` отсутствовал в npm-пакете → дашборд не работал.
- v2026.3.22 ужесточил валидацию конфига: пришлось удалить `whatsapp`, `google-gemini-cli-auth` из plugins и поправить `browser.profiles.subscription-portal.driver: "extension"` → `"existing-session"`.
- Установлена бета v2026.3.23-beta.1 — UI включён, всё работает.
- Мониторить стабильность бета-версии.

### 15. Burst coalescing уже работает
**Статус:** ✅ ПОДТВЕРЖДЕНО — в логах видно `private_text_burst_coalesced absorbed_message_ids=['11127', '11128'] messages_count=3`. Склейка пересланных подряд сообщений работает.

### 17. Owner Panel: детерминированная initial hydration после рестарта
**Статус:** ✅ ПОЛНОСТЬЮ ИСПРАВЛЕНО (2026-03-28) — initial hydration теперь четырёхслойная:
1. `refreshAll()` уже не последовательный.
2. Translator first-paint идёт через единый `/api/translator/bootstrap`.
3. Owner panel поднимает last-good runtime sections из `localStorage` (`krab:owner-panel-bootstrap:v1`) до live refresh, поэтому cold reload больше не возвращает ключевые блоки в пустые `—`.
4. Верхний dashboard snapshot и high-value error-path теперь тоже cache-aware, поэтому transient fetch-failure не стирает уже поднятый first-paint обратно в `ERR/FAIL`.
5. `Core Liveness (Lite)` и `Ecosystem Deep Health` теперь при transient fetch-сбое сначала поднимают last-good bootstrap, а не прыгают сразу в `Offline/Error`.

**Оставшееся наблюдение:** `Browser / MCP Readiness` намеренно остаётся в `LOADING`, а не в cached-ready, потому что это volatile probe. Единичные `browser_action_probe_raw_failed` при зелёном acceptance пока считаем шумом health-probe, а не runtime-регрессией.

---

## 🔵 Глубокая интеграция в macOS

### 11. Локальная папка-шлюз "Inbox"
**Статус:** ✅ РЕАЛИЗОВАНО (2026-04-06) — LaunchAgent `ai.krab.inbox-watcher` мониторит `~/Krab_Inbox` через watchdog (FSEvents). Файлы пересылаются Крабу через `/api/notify`. Plist: `scripts/launchagents/ai.krab.inbox-watcher.plist`.

### 12. Глобальный macOS Hotkey
**Статус:** ✅ РЕАЛИЗОВАНО — Hammerspoon ⌘⇧K → текстовый ввод → Krab `/api/notify`. Apple Shortcut тоже настроен.

### 13. Управление окнами через Hammerspoon
**Статус:** ✅ РЕАЛИЗОВАНО — HTTP bridge на `localhost:10101`, Python bridge `src/integrations/hammerspoon_bridge.py`. POST `/window` с командами `left|right|maximize|...`.
