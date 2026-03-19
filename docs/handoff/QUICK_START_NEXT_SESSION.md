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
> primary = `codex-cli/gpt-5.4`,
> fallback chain = `google-gemini-cli/gemini-3-flash-preview` -> `openai-codex/gpt-5.4` -> `qwen-portal/coder-model`,
> `:8080/api/health/lite` и owner panel показывают тот же route,
> warmup проходит, а Codex CLI переживает restart без выпадения из runtime truth.
>
> Что уже исправлено:
> 1. userbot больше не обрывает buffered OpenClaw по ложному first-chunk timeout;
> 2. `!status` теперь truthful по runtime route;
> 3. несколько private-сообщений подряд склеиваются в один запрос;
> 4. устранён drift `agents.defaults.model.primary` vs `agents.list[0].model`;
> 5. `codex-cli/*` больше не путается с локальным LM Studio путём;
> 6. userbot начал отправлять ранние тех-уведомления, что запрос жив и модель всё ещё думает.
>
> Что остаётся:
> 1. закоммитить текущие repo-правки;
> 2. вручную проверить живой Telegram round-trip на пачке из 2-3 сообщений подряд;
> 3. отдельно решить, оставляем ли `openai-codex/gpt-5.4` в fallback chain после дальнейших наблюдений.

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
