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
> На момент handoff live runtime уже стабилизирован:
> primary = `google-gemini-cli/gemini-3-flash-preview`,
> `:8080/api/health/lite` и owner panel показывают тот же route,
> warmup и live smoke проходят.
>
> Что уже исправлено:
> 1. userbot больше не обрывает buffered OpenClaw по ложному first-chunk timeout;
> 2. `!status` теперь truthful по runtime route;
> 3. несколько private-сообщений подряд склеиваются в один запрос;
> 4. устранён drift `agents.defaults.model.primary` vs `agents.list[0].model`.
>
> Что остаётся:
> 1. закоммитить текущие repo-правки;
> 2. при желании отдельно чинить OAuth для `gpt-5.4`;
> 3. вручную проверить один Telegram round-trip с recap из 2-3 сообщений подряд.

## Команды для диагностики (запустить в начале сессии)

```bash
# Статус системы
curl -s http://127.0.0.1:8080/api/health/lite | python3 -m json.tool

# Последние ошибки OpenClaw
tail -50 /Users/pablito/Antigravity_AGENTS/Краб/openclaw.log | grep -E "error|rate_limit|401"

# Статус ветки
cd /Users/pablito/Antigravity_AGENTS/Краб && git log --oneline -5

# Проверить фактический route после warmup
curl -s http://127.0.0.1:8080/api/health/lite | python3 - <<'PY'
import json,sys
data=json.load(sys.stdin)
print(json.dumps(data.get("last_runtime_route", {}), ensure_ascii=False, indent=2))
PY
```
