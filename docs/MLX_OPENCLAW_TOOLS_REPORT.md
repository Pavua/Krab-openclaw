# MLX через OpenClaw — отчёт о tools integration (Wave 229)

**Дата:** 2026-05-14
**Контекст:** RotorQuant зарегистрировал `mlx-local-kv4` provider в `~/.openclaw/openclaw.json` → http://127.0.0.1:8088/v1.
**Цель отчёта:** проверить end-to-end доступность MLX (Gemma-4-26B OptiQ) через OpenClaw gateway :18789 и поведение `tool_calls` на этом маршруте.
**Метод:** read-only тестирование curl-ами. `openclaw.json` НЕ модифицировался.

---

## 1. Конфигурация provider'а (как есть)

```jsonc
"mlx-local-kv4": {
  "baseUrl": "http://127.0.0.1:8088/v1",
  "apiKey": "not-needed",
  "auth": "api-key",
  "api": "openai-completions",
  "models": [
    { "id": "/Volumes/4TB SSD/.../gemma-4-26B-A4B-it-OptiQ-4bit",
      "name": "Gemma-4-26B-A4B OptiQ (Baseline, :8088)",
      "reasoning": false, "contextWindow": 131072, "maxTokens": 4096 },
    { "id": ".../Huihui-Qwen3-4B-Instruct-2507-abliterated-q4-hi-mlx",
      "name": "Qwen3-4B Huihui (KV4, 105 tok/s)" },
    { "id": ".../Huihui-Qwen3-14B-abl-v2",
      "name": "Qwen3-14B Huihui v2 (KV4, 41.5 tok/s)" },
    { "id": ".../Llama-3.3-8B-Abl-128K",
      "name": "Llama-3.3-8B Abl 128K (KV4, 39.5 tok/s)" }
  ]
}
```

Provider зарегистрирован корректно. Однако MLX-сервер :8088 в моменте отдаёт
только **один** loaded model (`gemma-4-26B OptiQ`) — остальные пути в конфиге
указывают на модели, которые не загружены в текущей сессии mlx-lm-server.

OpenClaw `main` agent (`~/.openclaw/openclaw.json::agents.list[0]`):

```jsonc
{ "id": "main", "model": "codex-cli/gpt-5.5", "agentRuntime": { "id": "codex" }, ... }
```

— primary остаётся `codex-cli/gpt-5.5`, fallback chain не содержит
`mlx-local-kv4/*`. То есть **MLX зарегистрирован как provider, но никакой агент
его не использует ни как primary, ни как fallback**.

---

## 2. End-to-end тесты

### 2.1. Direct :8088 (baseline) — plain chat

```bash
curl http://127.0.0.1:8088/v1/chat/completions \
  -d '{"model":".../gemma-4-26B-A4B-it-OptiQ-4bit",
       "messages":[{"role":"user","content":"Скажи привет"}],
       "max_tokens":20}'
```

**Результат:** latency 2.84s, валидный ответ. ✅

### 2.2. Direct :8088 — с tools (function calling)

```bash
curl http://127.0.0.1:8088/v1/chat/completions -d '{
  "model": ".../gemma-4-26B-A4B-it-OptiQ-4bit",
  "messages":[{"role":"user","content":"What time is it in Moscow? Use the tool."}],
  "tools":[{"type":"function","function":{
    "name":"get_time",
    "parameters":{"type":"object","properties":{"city":{"type":"string"}},"required":["city"]}}}],
  "tool_choice":"auto", "max_tokens":150
}'
```

**Результат:** latency 1.87s, `finish_reason: tool_calls` ✅

```json
{
  "tool_calls": [{
    "function": {"name": "get_time", "arguments": "{\"city\": \"Moscow\"}"},
    "type": "function",
    "id": "28792fe1-80d7-492c-846d-129f30219f8d"
  }]
}
```

**Вывод:** mlx-lm-server **полностью реализует OpenAI tools API** —
правильно отрисовывает Gemma chat template со схемой инструментов, парсит
`<tool_call>…</tool_call>` маркеры в ответе модели и возвращает структурированные
`tool_calls`. Прозрачный drop-in OpenAI-compat backend.

### 2.3. OpenClaw :18789 — все варианты model identifier

| model                          | HTTP   | Тело ответа                                                          | Latency |
|--------------------------------|--------|----------------------------------------------------------------------|---------|
| `openclaw`                     | 200    | `{"error":{"message":"internal error","type":"api_error"}}`          | 2.08s   |
| `openclaw/main`                | 200    | `{"error":{"message":"internal error","type":"api_error"}}`          | 1.93s   |
| `openclaw/default`             | 200    | `{"error":{"message":"internal error","type":"api_error"}}`          | 1.85s   |
| `mlx-local-kv4/…gemma-4-26B…`  | 400    | `Invalid 'model'. Use 'openclaw' or 'openclaw/<agentId>'.`           | 0.06s   |

**Вывод:** OpenClaw `/v1/chat/completions` принимает только meta-aliases
`openclaw[/<agentId>]`. Прямой routing на provider/model **запрещён** на этом
эндпойнте. Это by design — gateway оборачивает реальный backend агента
(`codex-cli/gpt-5.5` для `main`) и не предоставляет clients доступ к
произвольным provider'ам.

### 2.4. Root cause `internal error`

`~/.openclaw/logs/gateway.err.log`:

```
[model-fallback/decision] decision=candidate_failed
  requested=codex-cli/gpt-5.5 candidate=codex-cli/gpt-5.5
  reason=unknown detail=Error loading config.toml:
  url is not supported for stdio in `mcp_servers.context7`
Embedded agent failed before reply: ...
openai-compat: chat completion failed: FailoverError:
  Error loading config.toml: url is not supported for stdio
  in `mcp_servers.context7`
```

`codex-cli` ругается на malformed `mcp_servers.context7` config — у stdio
транспорта указан `url`, что недопустимо. Эта ошибка **повторяется каждые
~30 минут** на cron heartbeat и блокирует *всю* `/v1/chat/completions`
поверхность OpenClaw. Это **pre-existing bug, не связан с MLX integration**.

---

## 3. Сравнение latency

| Маршрут                                             | Plain chat | Tools call    |
|-----------------------------------------------------|------------|---------------|
| Direct MLX :8088                                    | 2.84s      | 1.87s ✅      |
| OpenClaw :18789 (`openclaw/main` → codex-cli)       | broken     | broken        |
| OpenClaw :18789 → MLX (нет route, агент = codex)    | N/A        | N/A           |

Прямого `OpenClaw → MLX` пути в текущей конфигурации **не существует**.
Provider зарегистрирован, но ни один агент его не использует. Даже если бы
codex-cli не падал, `openclaw/main` ходил бы в `codex-cli/gpt-5.5`, а не в MLX.

---

## 4. Что нужно для рабочего OpenClaw → MLX (для RotorQuant)

Два независимых шага:

### Шаг A. Починить `openclaw/*` маршрут (P0, blocker)

В `~/.openclaw/openclaw.json` или в codex-cli `config.toml` (`~/.codex/config.toml`
или эквивалент) исправить запись `mcp_servers.context7` — убрать поле `url` если
transport = stdio, либо переключить transport на http/sse. Это **разблокирует
ВСЕ chat completions через OpenClaw**, не только MLX.

### Шаг B. Создать агент с MLX в primary/fallback

Добавить в `agents.list` новый агент (или дополнить `main.subagents`), у которого:

```jsonc
{
  "id": "mlx-main",
  "model": "mlx-local-kv4//Volumes/4TB SSD/.../gemma-4-26B-A4B-it-OptiQ-4bit",
  "agentRuntime": { "id": "openai-compat" },
  "tools": { "profile": "full" }
}
```

После этого `openclaw/mlx-main` будет ходить в :8088. **Tools при этом будут
работать** — gateway передаст OpenAI tools schema 1:1, а mlx-lm-server её
понимает (см. 2.2).

### Шаг C. (Опционально) добавить MLX в fallback chain `agents.defaults`

Чтобы при отказе Gemini/Vertex fallback включал локальный MLX как последний
рубеж до `codex-cli`.

---

## 5. Cherry Studio status

Cherry Studio, подключённое к OpenClaw :18789, видит в `/v1/models` только три
meta-alias'а (`openclaw`, `openclaw/default`, `openclaw/main`). Provider
`mlx-local-kv4` и его 4 модели **не экспортируются** в этот endpoint — они
доступны только внутри fallback chain агентов.

**Текущий вердикт для Cherry Studio:** MLX через OpenClaw недоступен в принципе,
пока не будет создан явный агент с MLX-primary (шаг B выше). Параллельно
`openclaw/main` сейчас **сам по себе сломан** из-за context7 stdio bug (шаг A).

---

## 6. Рекомендации

| Сценарий                                 | Рекомендация                                                        |
|------------------------------------------|---------------------------------------------------------------------|
| Speed-only, без tools                    | Direct :8088 (2.8s baseline)                                        |
| Tools без агентной обвязки               | Direct :8088 (полная OpenAI tools совместимость, 1.9s)              |
| Tools + skill registry + cost tracking   | OpenClaw → нужен агент с MLX primary (шаги A+B выше)                |
| Cherry Studio с MLX как backend          | Подключить напрямую к :8088, не через :18789 (минус skill registry) |

**Wave 221 enable_thinking fallback** в текущем тесте не наблюдался —
mlx-lm-server вернул ответ за один прогон без двойного запроса. Это
ожидаемо: Gemma-4-OptiQ не reasoning-модель (`reasoning: false` в config).

---

## 7. Артефакты тестов

- `/tmp/test_a.json`, `/tmp/test_b.json`, `/tmp/test_c.json`, `/tmp/test_d.json`
  — raw responses OpenClaw (все `internal error`).
- `/tmp/test_tools.json` — direct :8088 tools success.
- Логи: `~/.openclaw/logs/gateway.err.log` (context7 stdio loop).

**Никаких изменений в `~/.openclaw/openclaw.json`, `com.user.mlx-lm-server.plist`,
`archive.db`, `src/modules/web_app.py` или landing-страницах не выполнено.**
