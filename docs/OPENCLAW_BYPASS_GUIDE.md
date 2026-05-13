# OpenClaw Bypass Guide (Wave 245)

Аварийный режим: **все** LLM-запросы Krab идут напрямую в локальный backend
(MLX local `:8088` или LM Studio `:1234`), полностью минуя OpenClaw gateway.

## Когда включать

Включай `KRAB_OPENCLAW_BYPASS_ENABLED=1`, если выполняется одно или больше:

- OpenClaw gateway сломан (malformed `mcp_servers.context7`, runtime crash,
  WebSocket transport regression);
- Cherry Studio / клиенты репортуют "internal error" на `openclaw/main`;
- Sentry прислал alert с тегом `recommend=enable_KRAB_OPENCLAW_BYPASS_ENABLED`
  (recommender автоматически срабатывает при fail rate > 50% за час);
- ты в полевой обстановке, нужно продолжать переписку через Telegram,
  пока гейтвэй не починен.

## Как включить

```bash
# Глобально для текущей сессии:
export KRAB_OPENCLAW_BYPASS_ENABLED=1

# Или в .env (persists через рестарт):
echo "KRAB_OPENCLAW_BYPASS_ENABLED=1" >> /Users/pablito/Antigravity_AGENTS/Краб/.env

# Перезапусти Krab:
/Users/pablito/Antigravity_AGENTS/new\ Stop\ Krab.command
sleep 5
/Users/pablito/Antigravity_AGENTS/new\ start_krab.command
```

В логах появится warning ровно один раз:

```
WARNING openclaw_bypass_enabled env=KRAB_OPENCLAW_BYPASS_ENABLED note=...
```

## Что произойдёт

- `OpenClawClient.send_message_stream` обходит весь OpenClaw chain
  (cloud routing, fallback цепочка, retry, semaphore queue) и идёт
  напрямую через `_direct_lm_fallback` → MLX local / LM Studio.
- `OpenClawAdapter.stream` (Hermes AgentEngine) автоматически наследует
  bypass через тот же `send_message_stream`.
- Swarm DM боты и cron pipeline — тот же путь.
- Runtime route channel помечается как `openclaw_bypass` с подробным
  `route_detail` для observability.

## Риски

- **Нет MCP tools.** Всё, что предоставляет OpenClaw layer (`web_search`,
  `peekaboo`, `tor_fetch`, любые MCP serverы) — недоступно. Только чистый LLM
  chat.completions.
- **Нет fallback chain.** Если local backend упал (MLX `:8088` или
  LM Studio `:1234` не отвечают) — bypass возвращает explicit error,
  cloud fallback **НЕ** триггерится.
- **Нет audit log в OpenClaw.** Запросы не попадают в gateway runtime log,
  observability dashboard покажет пустые counters для cloud routes.
- **Нет cloud моделей.** `google/gemini-3-pro-preview`, `openai/gpt-5`,
  vertex и т.д. — недоступны. Только модели, загруженные в локальный backend.

## Как убедиться, что bypass работает

1. Проверь логи на старте:
   ```bash
   /Users/pablito/Antigravity_AGENTS/Краб/scripts/krab_log_tail.sh | grep openclaw_bypass
   ```
2. Отправь тестовое сообщение в DM Telegram. В логах должно появиться:
   ```
   INFO openclaw_bypass_path_used chat_id=... model=mlx-local-kv4/...
   ```
3. Проверь runtime route:
   ```bash
   curl -sS http://127.0.0.1:8080/api/runtime/route | jq .
   # channel должен быть "openclaw_bypass"
   ```

## Recovery — как выключить, когда gateway починен

```bash
# Удали env или поставь в 0:
export KRAB_OPENCLAW_BYPASS_ENABLED=0
# Или удали строку из .env

# Перезапусти Krab:
/Users/pablito/Antigravity_AGENTS/new\ Stop\ Krab.command
sleep 5
/Users/pablito/Antigravity_AGENTS/new\ start_krab.command
```

В логах появится:
```
INFO openclaw_bypass_disabled env=KRAB_OPENCLAW_BYPASS_ENABLED
```

После recovery нормальное routing (cloud + fallback chain + MCP tools)
вернётся автоматически.

## Sentry recommender

Модуль `src/core/openclaw_bypass_recommender.py` отслеживает success/fail
rate health-probe вызовов gateway. При:

- >= 5 samples за последний час;
- fail rate >= 50%;
- bypass ещё **не** включён;
- прошло >= 1 час с прошлого alert;

отправляет Sentry warning с тегом `recommend=enable_KRAB_OPENCLAW_BYPASS_ENABLED`.
Это подсказка оператору — не автоматическое включение. Решение остаётся
за человеком.
