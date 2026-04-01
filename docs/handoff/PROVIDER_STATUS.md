# Статус провайдеров — Краб (актуально на 19.03.2026, подтверждено 28.03.2026, addendum 01.04.2026)

## Addendum 01.04.2026 21:32 — recovery после обновления OpenClaw

Что подтвердили live:

- после обновления появился runtime drift: `codex-cli/gpt-5.4` был declared как primary,
  но provider catalog для `codex-cli` оказался неполным;
- это давало `Unknown model` на warmup и затем `Config invalid` из-за
  `models.providers.codex-cli.baseUrl`;
- после починки registry-shape и последовательного restart gateway
  конфиг стартует без этого validation-error;
- прямые live probes в `/v1/chat/completions` вернули `200 OK` и тексты
  `OK-CODEX` / `OK-CODEX-2`.

Что изменено в коде Краба:

- `scripts/openclaw_model_registry_sync.py` теперь досеивает provider-level shape
  для alias-провайдеров вроде `codex-cli`;
- `src/openclaw_client.py` теперь умеет проходить несколько cloud quality retries подряд,
  а не только один.

Что важно не перепутать:

- текущий инцидент по `codex-cli` был не про отсутствие квоты `GPT-5.4`,
  а про runtime registry drift + слишком короткую retry-цепочку;
- browser incident (`9223` / `9222` / `18800`) остаётся отдельной задачей
  и не должен смешиваться с provider verdict для `codex-cli`.

## Addendum 28.03.2026 — подтверждение актуальности

Провайдерская цепочка и API-ключи из этого документа подтверждены актуальными:
- `codex-cli/gpt-5.4` — работает как primary (сессии до 48 минут без падений)
- `google-gemini-cli/gemini-3-flash-preview` — работает как safety fallback
- `GEMINI_API_KEY` → paid key (AIzaSyAifJ_...) — в силе
- Никаких изменений в provider chain на 28.03.2026 не вносилось.

---

## Addendum 16:47 — что подтвердили после утреннего регресса

### Gemini REST API: используется именно платный ключ

- `.env` сейчас содержит три разных переменных:
  - `GEMINI_API_KEY_PAID = AIzaSyAifJ_0...vSNy3A`
  - `GEMINI_API_KEY_FREE = AIzaSyA07LwN...LhPUKY`
  - `GEMINI_API_KEY = AIzaSyAifJ_0...vSNy3A`
- `GOOGLE_API_KEY` также указывает на `AIzaSyAifJ_0...vSNy3A`.
- Provider `google/` в `~/.openclaw/agents/main/agent/models.json` использует
  placeholder `apiKey = GEMINI_API_KEY`, а не literal secret.
- Direct probe `GET https://generativelanguage.googleapis.com/v1beta/models`
  с текущим `GEMINI_API_KEY` вернул `HTTP 200`.

Вывод: в текущем live-контуре REST-доступ к Gemini идёт через paid key, а не через
free-проект.

### Owner panel `:8080`: почему раньше казалось, что цепочка не сохраняется

- Корень проблемы был составной:
  1. stale cached HTML в уже открытой вкладке;
  2. старые локальные значения могли приезжать в payload сохранения и ломать
     валидацию fallback-цепочки.
- Теперь на `GET /` и `GET /nano_theme.css` выставлены anti-cache заголовки:
  - `Cache-Control: no-store, no-cache, must-revalidate, max-age=0`
  - `Pragma: no-cache`
  - `Expires: 0`
- Fresh browser acceptance подтвердил:
  - в глобальном редакторе цепочки `runtimeChainModelSelect_*` нет локальных
    model ids;
  - `codex-cli/gpt-5.4` и cloud fallback-цепочка сохраняются успешно;
  - local модели остаются только в отдельном селекторе разового запуска
    `Модель для этого запуска (облако + local)`, что является нормой.

### codex-cli provider truth

- `codex-cli` теперь оформлен как отдельный provider в owner panel и recovery:
  - статус показывает `CLI OK / CLI login missing / CLI missing`;
  - доступна helper-кнопка `Login Codex CLI.command`;
  - панель больше не притворяется, что это обычный OAuth provider.

## Addendum 03:52 — текущая truth после restart

### Runtime-конфиг сейчас

```
Primary:    codex-cli/gpt-5.4
Fallback 1: google-gemini-cli/gemini-3-flash-preview
Fallback 2: openai-codex/gpt-5.4
Fallback 3: qwen-portal/coder-model
```

### Короткий operational verdict

| Маршрут | Что подтверждено | Расследование / гипотеза |
|--------|-------------------|---------------------------|
| `codex-cli/gpt-5.4` | ✅ переживает restart, warmup и серии запросов; пока не падал, но latency плавает от ~1s до ~60s | Похоже на QoS/очередь выше нашего кода, а не на локальный session-history баг |
| `google-gemini-cli/gemini-3-flash-preview` | ✅ usable как быстрый fallback по подписке Google AI Pro | Нужен как safety-net, если Codex-маршруты снова уходят в плавающий QoS |
| `openai-codex/gpt-5.4` | ⚠️ одиночные запросы проходят, но именно этот путь исторически ловил таймауты в реальном чате и быстро деградирует по latency | Отдельная гипотеза: consumer-friendly OAuth-path хуже подходит для агентного use-case |
| `qwen-portal/coder-model` | ⚠️ fallback-only, резервный слот | Держим только как последний OAuth-резерв, не как желательный daily path |

### Что важно не перепутать

- `openai-codex` больше нельзя описывать как "абсолютно мёртвый": сейчас он живой, но плохой как primary.
- `codex-cli` лучше не описывать как "быстрый": он сейчас рабочий, но неровный по времени ответа.
- `google/gemini-3.1-pro-preview` сейчас исключён из автоматической live-цепочки не потому, что сломан ключ, а потому что путь через платный API нежелателен по стоимости и раньше упирался в `rate_limit`.
- Investigational колонку про возможную выгоду OpenAI держим как гипотезу, не как доказанный факт.

## Конфигурация OpenClaw (`~/.openclaw/openclaw.json`)

```
Primary:    codex-cli/gpt-5.4
Fallback 1: google-gemini-cli/gemini-3-flash-preview
Fallback 2: openai-codex/gpt-5.4
Fallback 3: qwen-portal/coder-model
```

## Диагностика провайдеров

### codex-cli/gpt-5.4

**Статус**: ✅ текущий live primary  
**Проверено**:
- controlled restart проходит успешно;
- warmup OpenClaw отвечает `200 OK`;
- `:8080/api/health/lite` показывает `last_runtime_route.provider = codex-cli`;
- owner panel `:8080` после `Sync Data` показывает `Рекомендовано (Routing) = codex-cli/gpt-5.4`.
**Ограничение**: latency нестабильна; даже stateless-серия может гулять от секунд до минуты.

### google-gemini-cli/gemini-3-flash-preview

**Статус**: ✅ быстрый fallback по подписке  
**Проверено**:
- OAuth-профиль живой;
- маршрут виден в runtime inventory и остаётся в честной fallback-цепочке;
- пригоден как safety-net при деградации Codex-маршрутов.
**Ограничение**: это уже не primary, а резерв для скорости и предсказуемости.

### openai-codex/gpt-5.4

**Статус**: ⚠️ нестабильный fallback  
**Проверено**:
- текущий OAuth-профиль читается как валидный;
- одиночные live-probe могут проходить через OpenClaw API;
- routing status показывает `OAuth OK`, но при этом локально видимые scopes ограничены `openid/profile/email/offline_access`.
**Проблема**:
- в реальном чате путь исторически ловил таймауты;
- на сериях запросов быстро деградирует по latency;
- одного факта `OAuth OK` недостаточно, чтобы считать путь production-stable.
**Рекомендация**: держать только fallback-слотом и наблюдать дальше.

### qwen-portal/coder-model

**Статус**: ⚠️ резервный fallback  
**Аутентификация**: OAuth через `qwen-portal` profile  
**Проблема**: portal RPM лимит остаётся низким даже при `thinking=off`.

### google/gemini-3.1-pro-preview (REST API)

**Статус**: 💤 не в active chain  
**Ключ**: `GOOGLE_API_KEY = GEMINI_API_KEY_PAID`  
**Факт live-проверки**: direct REST probe сейчас отвечает `HTTP 200` с ключом
`AIzaSyAifJ_0...vSNy3A`
**Почему не используем автоматически**:
- пользовательский приоритет сейчас на утилизацию уже оплаченных подписок `OpenAI Plus` и `Google AI Pro`;
- этот путь раньше уходил в `rate_limit`;
- это отдельный платный API-контур, который не нужен как default fallback при текущей стратегии.

### google-antigravity/*

**Статус**: ⛔ исключён из live-цепочки  
**Причина**: в текущем окружении нет usable-квоты, поэтому в runtime его не трогаем.

## Что реально исправлено в этой сессии

- userbot больше не рвёт buffered cloud-ответ слишком рано;
- userbot начал заранее отправлять тех-уведомления, что запрос жив и модель всё ещё думает;
- `!status` теперь показывает фактический route, а не stale model из конфига;
- несколько быстрых private-сообщений одного отправителя склеиваются в один запрос;
- runtime truth синхронизирован: gateway, health-lite и owner panel смотрят на один primary.
