# Быстрый старт для нового чата

## Приложи эти файлы к следующему чату

```
docs/handoff/SESSION_HANDOFF.md    ← основное
docs/handoff/AUDIT_STATUS_2026-03-23_RU.md ← compact confirmed / partial / open matrix
docs/handoff/PROVIDER_STATUS.md    ← статус провайдеров
docs/handoff/MASTER_PLAN_SOURCE_OF_TRUTH.md ← как считать % и по какому плану идём
docs/MASTER_PLAN_VNEXT_RU.md       ← канонический execution plan
docs/MULTI_ACCOUNT_SWITCHOVER_RU.md ← как безопасно продолжать с другой учётки
CLAUDE.md                          ← архитектура проекта
GEMINI.md                          ← truthful-контекст для Gemini/Google контуров
```

Если handoff уже экспортирован в отдельную папку, лучше прикладывать всю свежую
папку `artifacts/handoff_<timestamp>/` целиком.

## Контекст для нового чата (копипаст)

> Продолжаем работу с Krab / OpenClaw из общей рабочей копии `/Users/Shared/Antigravity_AGENTS/Краб`.
> Если продолжаем из USER3-патча с последними fixes этой сессии, рабочая ветка:
> `codex/fix-handle-shop-export`.
> Если продолжаем по историческому runtime-handoff `pablito`, базовая ветка:
> `fix/routing-qwen-thinking`.
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
> 6. userbot начал отправлять ранние тех-уведомления, что запрос жив и модель всё ещё думает;
> 7. progress-notice теперь показывает не только стартовую модель, а текущий маршрут и номер попытки fallback-цепочки.
> 8. `handle_shop` больше не ломает старт `userbot_bridge`;
> 9. launcher переведён на per-account watchdog/proxy logs;
> 10. launcher больше не даёт ложный fail по LaunchAgent-gateway;
> 11. на другой учётке `Voice Gateway` уже может подниматься через per-account fallback.
>
> Что остаётся:
> 1. закрыть `partial`-пункты из `AUDIT_STATUS_2026-03-23_RU.md`: `#6`, `#7`;
> 2. перенести launcher-hardening обратно в `pablito` launcher;
> 3. решить, считать ли multi-account `Voice Gateway` отдельным shared-active сервисом, а не ссылкой на `pablito` path.
>
> Важно:
> 1. Канонический baseline проекта = `31%`.
> 2. Главный приоритет = `OpenClaw Stability Kernel`.
> 3. Второй приоритет = `ordinary-call translator`.
> 4. Если работа идёт с другой macOS-учётки, shared только repo/docs/artifacts; runtime/auth/browser state раздельные.

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
