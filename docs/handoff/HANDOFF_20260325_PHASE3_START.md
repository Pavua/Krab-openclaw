# Handoff 2026-03-25 — Начало Фазы 3

## Состояние на момент передачи

**Ветка**: `main` (актуальная, содержит phase2 + все фиксы этой сессии)

### Что сделано в этой сессии
1. **Отключены template-interceptors** — 4 функции (`_looks_like_model_status_question`, `_looks_like_capability_status_question`, `_looks_like_commands_question`, `_looks_like_integrations_question`) теперь возвращают `False`. Все вопросы уходят в LLM. Ветка `fix/disable-template-interceptors` смержена в main.
2. **BROWSER_FOCUS_TAB=0** — Chrome не выходит на передний план при работе с браузером. Включить: `BROWSER_FOCUS_TAB=1` в `.env` или `!config BROWSER_FOCUS_TAB=1`. Ветка `fix/browser-silent-mode` смержена в main.
3. **GEMINI_PAID_KEY_ENABLED=0** — платный Gemini ключ не используется без явного флага. Ветка `fix/gemini-paid-opt-in` смержена в main.
4. **TELEGRAM_MESSAGE_BATCH_MAX_MESSAGES=100** — добавлено в `.env`.
5. **SOUL.md исправлен** — `~/.openclaw/workspace-main-messaging/SOUL.md` теперь корректно описывает каналы: userbot (pablito) = основной, Bot = резервный.

### Важные детали конфигурации
- `.env` содержит `TELEGRAM_MESSAGE_BATCH_MAX_MESSAGES=100`
- `BROWSER_FOCUS_TAB` по умолчанию 0 — Chrome тихий. Для существующих вкладок работает. Новые вкладки Chrome всё равно открываются с фокусом (поведение ОС).
- `GEMINI_API_KEY_PAID` есть в `.env` но не используется без `GEMINI_PAID_KEY_ENABLED=1`
- Второй бесплатный Google ключ (другой аккаунт) → добавить в `GEMINI_API_KEY_FREE`

---

## Фаза 3 — System Control v2 (начать здесь)

### Описание из мастер-плана
> System Control v2: browser relay, окна, приложения, clipboard, screenshots/OCR, файловые операции, UI automation, notifications. Все capability через единую `Capability Registry + Policy Matrix`. Сначала translator-critical и owner-critical, потом расширение. Browser/MCP readiness в UI — реальная truth, не optimistic state.

### Что уже есть (не трогать, использовать как основу)
| Файл | Строк | Что делает |
|------|-------|-----------|
| `src/core/capability_registry.py` | 528 | `_ROLE_CAPABILITIES` dict, policy matrix по ролям. Основа есть. |
| `src/integrations/macos_automation.py` | ~200 | clipboard, notifications, open apps, active window info |
| `src/integrations/hammerspoon_bridge.py` | ~120 | window management через Hammerspoon HTTP API (:10101) |
| `src/integrations/browser_bridge.py` | 886 | CDP browser automation, `take_screenshot()`, `get_or_open_tab()` |
| `src/modules/web_app.py` | большой | веб-панель, endpoint'ы capability info |

### Порядок работы (согласован с пользователем)

**Шаг 1 — Capability Registry → единый источник правды**
- Сейчас web-панель и registry могут расходиться
- Сделать так чтобы web-панель читала capability state из `capability_registry.py`, а не строила свою копию
- Добавить в registry недостающие capability: `screenshots`, `ocr`, `ui_automation`, `tor_proxy`

**Шаг 2 — Browser/MCP readiness truth**
- Сейчас UI может показывать "connected" когда CDP/MCP на самом деле недоступен
- Добавить `health_check()` методы в bridge-классы
- Capability registry должна опрашивать их при построении snapshot

**Шаг 3 — Screenshots через capability matrix**
- `browser_bridge.take_screenshot()` уже есть
- Нужно: зарегистрировать в registry, добавить policy (owner only vs full), сделать доступным как команду `!screenshot`

**Шаг 4 — UI automation (AppleScript/Accessibility)**
- `macos_automation.py` уже есть базовые osascript вызовы
- Расширить: click by app/window/element, type text, focus app

**Шаг 5 — OCR поверх скриншотов**
- `pytesseract` или `mlx_ocr` (если есть на macOS ARM)
- Вход: screenshot bytes → выход: text

**Шаг 6 — Policy Matrix с командой `!cap`**
- `!cap browser on/off`, `!cap macos on/off`, etc.
- Hot-reload через config

**Шаг 7 (вишлист/низкий приоритет) — Tor**
- `TOR_ENABLED` флаг, `tor` subprocess, SOCKS5 :9050
- `tor_fetch(url)` в macos_automation.py
- Chrome с `--proxy-server="socks5://127.0.0.1:9050"`

### Начать с Шага 1

Конкретно: прочитать `src/core/capability_registry.py` полностью, затем найти в `src/modules/web_app.py` где строится capability snapshot для UI — и убедиться что они используют одну функцию, а не дублируют логику.

---

## Контекст пользователя
- Владелец: pablito
- Запуск: `new start_krab.command` из `/Users/pablito/Antigravity_AGENTS/`
- Репозиторий: `/Users/pablito/Antigravity_AGENTS/Краб/`
- Runtime OpenClaw config: `~/.openclaw/openclaw.json`
- Persona Краба: `~/.openclaw/workspace-main-messaging/`
- Новые ветки называть `codex/...` или `fix/...`
- Merge в main только после тестов
- Общаться только на русском
- НЕ использовать SIGHUP для перезапуска OpenClaw (использовать `openclaw gateway`)
- Перезапуск Краба: `new start_krab.command` / `new Stop Krab.command`

## Бэклог (помимо Фазы 3)
Файл: `/Users/pablito/.claude/projects/-Users-pablito-Antigravity-AGENTS-----/memory/project_krab_backlog.md`
- Mercadona навигация (нужны логи)
- iMessage OTP фильтрация
- "Передам" но не передаёт — forwarding механизм
- Timeout 15 мин → результат новым сообщением после ошибки
- Второй Google аккаунт → добавить GEMINI_API_KEY_FREE
