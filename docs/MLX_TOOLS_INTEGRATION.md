# Wave 233: MLX tools integration — анализ и рекомендация

**Дата:** 2026-05-14
**Контекст:** Cherry Studio (или иной OpenAI-compat клиент) подключается напрямую к
`mlx_lm.server :8088` и не видит MCP инструментов Krab'а. Нужно понять — это
ограничение mlx_lm.server, Gemma 4, или архитектуры?

## TL;DR

- `mlx_lm.server` версии **0.31.3** поддерживает tools / `tool_calls` **полноценно
  и из коробки** (OpenAI-совместимо). Никаких патчей пакета не нужно.
- Gemma 4 имеет **встроенный tool parser** (`mlx_lm/tool_parsers/gemma4.py`),
  авто-подхватывается через `_infer_tool_parser()` по chat-шаблону модели.
- "Tools не работают" в связке Cherry Studio → :8088 — это **не баг mlx_lm**,
  а **архитектурный факт**: MCP-инструменты Krab'а живут в процессе Krab'а
  (`src/mcp_client.py::get_tool_manifest`). Любой внешний клиент, идущий мимо
  Krab'а, не получает этот манифест автоматически — `tools=[]` в запросе.
- **Рекомендация:** Option C (route через OpenClaw/Krab) для tool-required
  запросов. Cherry Studio → Krab webhook / userbot, не → :8088 напрямую.

## 1. Что внутри mlx_lm.server 0.31.3

### 1.1 Tool parsers (нативные)

`/opt/homebrew/anaconda3/lib/python3.12/site-packages/mlx_lm/tool_parsers/`

```
function_gemma.py   gemma4.py   glm47.py   json_tools.py   kimi_k2.py
longcat.py   minimax_m2.py   mistral.py   pythonic.py   qwen3_coder.py
```

### 1.2 Auto-detection (`tokenizer_utils.py::_infer_tool_parser`)

Парсер выбирается по подстрокам в `chat_template`:

| Маркер в chat_template | Парсер |
|---|---|
| `<\|tool_call>` + `<tool_call\|>` | **gemma4** ← наша модель |
| `<start_function_call>` | function_gemma |
| `<longcat_tool_call>` | longcat |
| `[TOOL_CALLS]` | mistral |
| `<\|tool_list_start\|>` | pythonic |
| `<tool_call>\n<function=` | qwen3_coder |
| ... | ... |

### 1.3 Сервер-пайплайн (`server.py`)

- При наличии `request.tools` и `tokenizer.has_tool_calling=True` — сервер
  передаёт `tools=` в `apply_chat_template`, генерация ведётся в режиме FSM
  (состояния `normal` / `tool` / `reasoning`), парсер вызывается на сегментах
  между `tool_call_start_tokens` и `tool_call_end_tokens`.
- В ответ собирается `choices[0].message.tool_calls: [{function:{name,arguments}}]`
  и `finish_reason: "tool_calls"` — **полностью OpenAI-совместимо**.

## 2. Live verification (1 запрос)

Запрос:

```bash
curl -sS http://127.0.0.1:8088/v1/chat/completions -H "Content-Type: application/json" -d '{
  "model": "/Volumes/4TB SSD/LMStudio_models/mlx-community/gemma-4-26B-A4B-it-OptiQ-4bit",
  "messages": [{"role":"user","content":"Какое время в Москве?"}],
  "tools": [{"type":"function","function":{
    "name":"get_time","description":"Get current time",
    "parameters":{"type":"object","properties":{"city":{"type":"string"}}}
  }}],
  "tool_choice": "auto",
  "max_tokens": 120,
  "chat_template_args": {"enable_thinking": false}
}'
```

Ответ (выдержка):

```json
{
  "choices": [{
    "finish_reason": "tool_calls",
    "message": {
      "role": "assistant",
      "reasoning": "The user is asking for the current time in Moscow...",
      "tool_calls": [{
        "id": "fcafd721-46f0-45b5-947c-d5a63704e383",
        "type": "function",
        "function": {"name": "get_time", "arguments": "{\"city\": \"Moscow\"}"}
      }]
    }
  }],
  "usage": {"prompt_tokens": 56, "completion_tokens": 56, "total_tokens": 112}
}
```

**Итог:** `tool_calls` присутствует, `arguments` — валидный JSON,
`finish_reason="tool_calls"`. Никаких telltale-следов "raw `<|tool_call>`-токенов
просочились в content" — парсер отработал чисто.

## 3. Где тогда баг "Gemma не видит инструменты Krab'a"?

**Не в mlx_lm.** В архитектурном разделении:

```
Cherry Studio  ─────── :8088 (mlx_lm.server) ─────── Gemma 4
       │
       └── tools=[] (пусто, Cherry Studio не знает про Krab MCP)

Krab userbot ── openclaw_client ── mcp_client.get_tool_manifest()
                                        │
                                        └── tools = [12+ MCP × 5-30 each]
                                            (krab-yung-nagato/krab-p0lrd/
                                             krab-hammerspoon/github/sentry/...)
```

`get_tool_manifest()` (`src/mcp_client.py:193`) собирает живой манифест из активных
MCP-сессий в **runtime процесса Krab'а**. Внешнему клиенту он недоступен ни через
:8088 (mlx_lm не знает про MCP), ни через :18789 без специального адаптера.

## 4. Workaround options

### Option A — pre-inject tools в системный промпт (минимальный код)

Krab делает MCP→OpenAI conversion как обычно, но добавляет:

- **HTTP-прокси** между Cherry Studio и :8088: слушает на `:8089`, при входящем
  `POST /v1/chat/completions` дёргает `mcp_client.get_tool_manifest()` и
  инжектит в `request.tools` ДО форварда на :8088. Tool-call в ответе ловит и
  выполняет через `call_tool_unified`.
- **Плюсы:** Cherry Studio работает без изменений, mlx_lm tool_calls остаются
  нативными (FSM-парсинг — никаких regex-хаков).
- **Минусы:** ещё один HTTP-хоп, нужен tool-execution loop вне userbot
  (иначе модель просит инструмент, а прокси не умеет его выполнить).
- **Сложность:** ~150-200 LOC новый модуль `src/integrations/mlx_tools_proxy.py`.

### Option B — Rapid-MLX / vllm-mlx

**Не оправдано.** mlx_lm 0.31.3 уже имеет полный набор tool-парсеров (10 семейств
моделей). Менять движок ради функциональности, которая уже есть, — лишний риск
(квантовка, KV-cache, скорость). Отклонено.

### Option C — route через OpenClaw / Krab userbot (рекомендуется)

Cherry Studio шлёт сообщение **в Krab** (Telegram DM с p0lrd, либо HTTP-эндпоинт
owner-панели на `:8080`), Krab прогоняет полный pipeline:

1. `openclaw_client.chat_completion` собирает MCP-манифест,
2. вызывает `:8088` с `tools=[...]`,
3. ловит `tool_calls`, выполняет через `mcp_client.call_tool_unified`,
4. возвращает финальный текст клиенту.

**Плюсы:**
- 0 нового кода — этот путь уже существует и оттестирован (Wave 221-225).
- MCP-tools, swarm, memory, RAG — всё работает.
- Один источник истины (Krab) для tool-state.

**Минусы:**
- Latency overhead: лишний tg-hop. Реально ~200-500ms на простых вызовах.
- Cherry Studio теряет direct-mode (но он и так не давал tools).

## 5. Рекомендация

**Option C** для production. **Option A** — если пользователь хочет конкретно
Cherry Studio как UI и не готов уходить в Telegram.

Конкретный план для Option C (zero-code):
1. В Cherry Studio: настроить endpoint = адрес Krab webhook / Telegram bot вместо
   `http://127.0.0.1:8088`.
2. Если нужен HTTP-API — использовать существующий owner-panel `:8080`
   (или поднять тонкий `/v1/chat/completions` адаптер поверх него — Wave 234).

Для Option A — отдельная сессия с TDD, FSM-тесты на стримминг tool_calls,
интеграция с `_safe_call_tool` и кэшем — нетривиально, оценка 1-2 дня.

## 6. Что НЕ делать

- ❌ Патчить `mlx_lm` package (sacred per RotorQuant rules, и не нужно — всё уже
  работает).
- ❌ Парсить `<|tool_call>...<tool_call|>` маркеры на стороне Krab вручную —
  mlx_lm делает это сам через FSM-генерацию.
- ❌ Менять `chat_template` модели — gemma4-parser авто-детектится из текущего
  шаблона.

## Ссылки

- `mlx_lm/server.py` строки 532-549 (forward `tools=` в `apply_chat_template`),
  668-676 (FSM transitions), 1440-1467 (collect tool_text → parser).
- `mlx_lm/tokenizer_utils.py:548-577` (`_infer_tool_parser`).
- `mlx_lm/tool_parsers/gemma4.py` (regex + `_gemma4_args_to_json`).
- `src/mcp_client.py:193` (`get_tool_manifest`).
- `src/openclaw_client.py:195-260` (Wave 221-225: MLX local backend wiring).
