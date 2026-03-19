# Быстрый старт для нового чата

## Приложи эти файлы к следующему чату

```
docs/handoff/SESSION_HANDOFF.md    ← основное
docs/handoff/PROVIDER_STATUS.md    ← статус провайдеров
CLAUDE.md                          ← архитектура проекта
```

## Контекст для нового чата (копипаст)

> Продолжаем работу с Краб (Telegram userbot). Ветка `fix/routing-qwen-thinking`.
>
> Главная проблема: GPT-5.4 через Copilot OAuth даёт 401 "Missing scopes: model.request"
> после 1-2 сообщений. Все остальные провайдеры (Gemini 3.1 Pro, Qwen) упираются в
> rate limit при thinking=high.
>
> Что уже исправлено: httpx.TimeoutException теперь триггерит fallback, таймаут 600s,
> GOOGLE_API_KEY = GEMINI_API_KEY_PAID в start_krab.command, qwen перед gemini-cli-flash.
>
> Что нужно сделать дальше: [список из SESSION_HANDOFF.md]

## Команды для диагностики (запустить в начале сессии)

```bash
# Статус системы
curl -s http://127.0.0.1:8080/api/health/lite | python3 -m json.tool

# Последние ошибки OpenClaw
tail -50 /Users/pablito/Antigravity_AGENTS/Краб/openclaw.log | grep -E "error|rate_limit|401"

# Статус ветки
cd /Users/pablito/Antigravity_AGENTS/Краб && git log --oneline -5

# Проверить GOOGLE_API_KEY (должен быть платный)
echo "GOOGLE_API_KEY начинается с: ${GOOGLE_API_KEY:0:10}..."
```
