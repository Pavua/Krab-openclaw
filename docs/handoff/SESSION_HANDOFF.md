# Session Handoff — Краб 19.03.2026

## Ветка
`fix/routing-qwen-thinking` (не смёрджена в main)

## Что было сделано в этой сессии

### ✅ Исправлено и закоммичено

| Коммит | Что |
|--------|-----|
| `4d781a9` | `httpx.TimeoutException` теперь попадает в fallback-loop (был баг: исключение вылетало СНАРУЖИ `for attempt in range(4)`, минуя все fallbacks) |
| `d329dd3` | Таймаут 300s → 180s; `Start Voice Gateway.command` — убран хардкод пути `/Users/pablito/...` |
| `32e8535` | Таймаут httpx 180s → 600s (`connect=30s, read=600s`) чтобы не обрывать внутренний fallback-цикл OpenClaw |

### ✅ Исправлено вне репо

- `new start_krab.command` (строки ~471-478): добавлен блок `if [ -n "${GEMINI_API_KEY_PAID:-}" ]; then export GOOGLE_API_KEY="$GEMINI_API_KEY_PAID"; fi` — теперь OpenClaw provider `google/` получает платный ключ
- `~/.openclaw/openclaw.json` fallbacks переупорядочены: `qwen-portal/coder-model` теперь ПЕРЕД `google-gemini-cli/gemini-3-flash-preview` (gemini-cli зависает на больших контекстах)

---

## Текущее состояние провайдеров (на 19.03.2026)

| Провайдер | Конфиг | Статус | Проблема |
|-----------|--------|--------|----------|
| `openai-codex/gpt-5.4` | primary, thinking=off | ❌ 401 после 2 сообщений | **HTTP 401 Missing scopes: `model.request`** — Copilot OAuth-токен не имеет разрешения на API-доступ к GPT-5.4. Это проблема подписки/токена, не квоты |
| `google/gemini-3.1-pro-preview` | fallback#1, thinking=high | ⚠️ rate_limit | С thinking=high OpenClaw делает 4 ретрая по 2–4s каждый → исчерпывает RPM быстро. Попробовать thinking=off для fallback |
| `qwen-portal/coder-model` | fallback#2, thinking=high | ⚠️ rate_limit через ~4 мин | Qwen portal RPM ограничен, агрессивные ретраи исчерпывают окно |
| `google-gemini-cli/gemini-3-flash-preview` | fallback#3 | ⚠️ зависает | Работает на warmup (28 токенов), но зависает на больших контекстах (50+ сообщений) |

### Ключевой вывод по GPT-5.4

Это не вопрос квот. Логи OpenClaw: `HTTP 401: Missing scopes: model.request`. Copilot API даёт scope `model.request` только при определённых планах (Copilot Enterprise + API access). Нужно либо:
1. Переключить primary на Gemini 3.1 Pro (пока самый стабильный)
2. Перелогиниться через `openclaw auth openai-codex` и проверить план

### Ключевой вывод по таймаутам

Проблема НЕ в слишком коротком/длинном таймауте — проблема в том что все провайдеры падают по своим причинам. После фиксов выше полная цепочка работает, просто медленно (2–5 мин). С 600s таймаутом OpenClaw успевает пройти всю цепочку.

---

## Pending задачи (из roadmap плана)

Файл плана: `/Users/pablito/.claude/plans/agile-mixing-teacup.md`

### Высокий приоритет (нужно в текущей ветке)
- [ ] **Починить GPT-5.4 OAuth**: попробовать `openclaw auth openai-codex` — перелогиниться, проверить что токен имеет `model.request`
- [ ] **Thinking для fallback-провайдеров**: выставить `thinking=off` или `low` для Gemini 3.1 Pro и Qwen в fallback режиме (они используются как резерв, не для сложных задач)
- [ ] **Смёрджить ветку** `fix/routing-qwen-thinking` в main

### Средний приоритет (следующая сессия)
- [ ] **`handle_browser`**: функция в `command_handlers.py` — зарегистрировать в COMMAND_MAP и `userbot_bridge.py`. `browser_bridge.py` уже создан.
- [ ] **`handle_audio_message`**: обработчик входящих голосовых/аудио сообщений Telegram. `perceptor.py` уже создан.
- [ ] **Тестирование Voice Gateway**: `Start Voice Gateway.command` создан, нужно проверить end-to-end
- [ ] **LM Studio интеграция**: SSD подключён, модели загружены — нужно проверить что routing работает

---

## Архитектурные заметки для нового чата

### Как работает fallback-цепочка

```
Telegram сообщение
  → openclaw_client.py _stream_with_fallback()
      → for attempt in range(4):
          → _openclaw_completion_once(model_id)
              → POST /v1/chat/completions → OpenClaw Gateway :18789
                  OpenClaw внутри делает СВОЮ цепочку fallbacks:
                  GPT-5.4 → Gemini 3.1 Pro → Qwen → gemini-cli-flash
              ← httpx.TimeoutException → ProviderError(retryable=True) → next attempt
          → если ProviderError(retryable) → выбрать следующую модель через _pick_cloud_retry_model()
```

**Важно**: OpenClaw имеет СВОЙ внутренний fallback и Краб имеет СВОЙ внешний. Не путать!

### Два разных Google-провайдера в OpenClaw

| Провайдер | Аутентификация |
|-----------|----------------|
| `google/` | `GOOGLE_API_KEY` из env (REST API) |
| `google-gemini-cli/` | OAuth через CLI `~/.gemini/` (не зависит от env) |

Исправление в `new start_krab.command`: `export GOOGLE_API_KEY="$GEMINI_API_KEY_PAID"` — теперь `google/` получает платный ключ.

### Настройки thinking

- Сохраняются в `~/.openclaw/openclaw.json` под `agents.defaults.models[model_id].params.thinking`
- После изменения через панель 8080 ТРЕБУЕТСЯ РЕСТАРТ OpenClaw gateway (не Краба!) — изменения в openclaw.json читаются только при старте gateway
- `thinking=high` увеличивает время ответа в 2-3x и потребляет RPM квоту агрессивно

---

## Состояние портов и процессов

| Сервис | Порт | Как проверить |
|--------|------|---------------|
| Краб web panel | 8080 | `curl http://127.0.0.1:8080/api/health/lite` |
| OpenClaw gateway | 18789 | `curl http://127.0.0.1:18789/health` |
| Voice Gateway | 8090 | `curl http://127.0.0.1:8090/health` |
| Chrome CDP | 9222 | `curl http://localhost:9222/json/version` |

## Runtime state файлы

- Lock/PID/stop-flags: `~/.openclaw/krab_runtime_state/`
- Watchdog log: `/tmp/krab_session_watchdog.log`
- OpenClaw log: `/Users/pablito/Antigravity_AGENTS/Краб/openclaw.log`
- App state (translator, reminders): `data/`

## Как перезапустить

```bash
cd /Users/pablito/Antigravity_AGENTS
bash "new Stop Krab.command"
# подождать 3-5 секунд
open "new start_krab.command"
# проверить:
curl -s http://127.0.0.1:8080/api/health/lite | python3 -m json.tool
```

## Как перезапустить только OpenClaw gateway (после изменений openclaw.json)

```bash
openclaw gateway stop
sleep 2
openclaw gateway start
```
