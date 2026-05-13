# Cherry Studio — конфигурация 3 backend'ов

Документ описывает настройку Cherry Studio (десктопный LLM-клиент) для работы с тремя локальными backend'ами Краба. Каждый из них покрывает свой use-case: smart routing с инструментами, прямой MLX inference, и LM Studio как fallback/discovery.

## 1. Зачем 3 backend'a в Cherry Studio

| Backend | URL | Auth | Скорость (tok/s) | Tools | Стоимость | Privacy | Best for |
|---|---|---|---|---|---|---|---|
| **Krab OpenClaw smart** | `http://127.0.0.1:18789/v1` | Bearer `9FzMdS4A…` | ~30-80 (зависит от модели) | yes (MCP + RAG + web_search) | $$ (cloud Gemini routing) | hybrid (local + cloud) | tools, memory, swarm |
| **MLX RotorQuant direct** | `http://127.0.0.1:8088/v1` | none | ~120-200 (Gemma OptiQ 4-bit) | no | $0 | local | быстрый chat offline |
| **LM Studio backup** | `http://127.0.0.1:1234/v1` | Bearer `$LM_STUDIO_API_KEY` | ~40-100 (зависит от модели) | partial (через OpenClaw bridge) | $0 | local | discovery / fallback |

Кратко: **OpenClaw** = умный (но с cloud-зависимостью), **MLX** = быстрый (но тупой к tools), **LM Studio** = песочница для новых моделей.

## 2. Setup steps

В Cherry Studio: **Settings → Model Service → Add Custom Provider** (иконка `+` справа от списка провайдеров).

### 2.1. Krab OpenClaw smart

| Поле | Значение |
|---|---|
| Provider Name | `Krab OpenClaw` |
| Provider Type | `OpenAI` (совместимый API) |
| API URL | `http://127.0.0.1:18789/v1` |
| API Key | `9FzMdS4ACkMbowogjVYPPmDS0Oi3kwRQxkZHIlq241o` |
| Headers (опционально) | `x-openclaw-scopes: operator.write` |

**Manual model add** (Cherry Studio не делает auto-discovery моделей у custom provider):

- `openclaw/main` — основной routed endpoint (Gemini 3 Pro по дефолту)
- `openclaw/default` — alias к main, для совместимости

Нажать **"+ Add"** под списком моделей, ввести id вручную, сохранить.

### 2.2. MLX RotorQuant direct

| Поле | Значение |
|---|---|
| Provider Name | `MLX RotorQuant` |
| Provider Type | `OpenAI` |
| API URL | `http://127.0.0.1:8088/v1` |
| API Key | (оставить пустым или поставить `none`) |

**Manual model add** — нужен полный путь к модели на диске:

```
/Volumes/4TB SSD/LMStudio_models/mlx-community/gemma-4-26B-A4B-it-OptiQ-4bit
```

Cherry Studio передаёт id как есть в `model` поле — RotorQuant резолвит путь.

### 2.3. LM Studio backup

| Поле | Значение |
|---|---|
| Provider Name | `LM Studio` |
| Provider Type | `OpenAI` |
| API URL | `http://127.0.0.1:1234/v1` |
| API Key | `$LM_STUDIO_API_KEY` (значение токена из `~/.openclaw/secrets/lm_studio_token` или Settings → Network в LM Studio app) |

**Manual model add** — id берётся из LM Studio app (вкладка **My Models** → правый клик → Copy model identifier). Примеры:

- `gemma-4-e4b-it-mlx`
- `qwen3-coder-30b-a3b`

## 3. Когда какой использовать

| Ситуация | Backend |
|---|---|
| "Хочу быстро поспрашивать локально, без интернета" | MLX RotorQuant |
| "Хочу tools (RAG/search/MCP/memory)" | Krab OpenClaw |
| "Хочу попробовать модель которую только что скачал в LM Studio" | LM Studio direct |
| "Cloud упал / нет интернета, но нужен ответ" | MLX (offline) или LM Studio |
| "Нужен Russian translation длинного текста" | OpenClaw → Gemini 3 Flash |
| "Тестирую новую модель на speed/quality" | LM Studio direct (для изоляции от routing) |
| "Хочу записать ответ в memory Краба" | OpenClaw (другие backend'ы не пишут в `archive.db`) |

**Важно:** запросы через Cherry Studio к OpenClaw **не попадают** в Telegram userbot. Это side-channel: тот же gateway, но без MTProto-обвязки.

## 4. Известные gotchas

### 4.1. Gemma всегда thinks (Wave 221)

Gemma OptiQ и Gemma 3 mlx (любая Gemma family) всегда генерируют `<think>…</think>` блок перед ответом. Cherry Studio показывает его как обычный текст — нужно включить **Settings → Display → Show reasoning** (или **Hide reasoning blocks** — в зависимости от версии Cherry Studio). Иначе вывод выглядит шумным.

### 4.2. Tools работают только через OpenClaw

MLX direct и LM Studio direct **не поддерживают** function calling в формате, понятном Cherry Studio. Если выбрать MLX backend и попытаться вызвать tool — модель сгенерирует JSON в plain text, Cherry Studio это не распарсит.

Workaround: для tool-вызовов всегда использовать OpenClaw provider. Wave 229 findings по direct-MLX tools: **TODO** (на момент написания — direct MLX tool-bridge не реализован, см. `IMPROVEMENTS.md`).

### 4.3. LM Studio scheduler stalls

Если в LM Studio app в логах висит `PROCESSING PROMPT 0.00%` дольше 30 секунд — scheduler застрял (типично при switch модели в момент активного запроса).

Лечение:
1. LM Studio app → **My Models** → правый клик на модели → **Eject**
2. Подождать 5 сек
3. **Settings → Developer → Restart Server**
4. Заново загрузить модель (или дать lazy-load на следующем запросе)

### 4.4. OpenClaw 401 → проверить scope

Если Cherry Studio получает `401 Unauthorized` от OpenClaw — проверить header `x-openclaw-scopes: operator.write` (без него gateway режет write-операции).

### 4.5. Cherry Studio кэширует список моделей

После добавления новой модели в provider — **закрыть и переоткрыть** Cherry Studio (Cmd+Q, не просто закрытие окна). Иначе модель не появляется в dropdown'е чата.

## 5. Quick check команды

Перед настройкой Cherry Studio убедиться, что endpoints отвечают.

### 5.1. OpenClaw smart

```bash
curl -sS http://127.0.0.1:18789/v1/models \
  -H "Authorization: Bearer 9FzMdS4ACkMbowogjVYPPmDS0Oi3kwRQxkZHIlq241o" \
  -H "x-openclaw-scopes: operator.write" | jq '.data[].id' | head
```

Ожидается список model id. Если **401** — токен или scope. Если **404** — gateway не запущен (`openclaw gateway`). Если **500** — посмотреть `~/.openclaw/logs/gateway.log`.

### 5.2. MLX RotorQuant

```bash
curl -sS http://127.0.0.1:8088/v1/models | jq
```

Ожидается JSON с моделью. Если **connection refused** — RotorQuant LaunchAgent не загружен (`launchctl list | grep rotorquant`). Если **404 на /v1/models** — версия RotorQuant без OpenAI-compat (нужно обновить или использовать `/chat/completions` напрямую).

### 5.3. LM Studio

```bash
curl -sS http://127.0.0.1:1234/v1/models \
  -H "Authorization: Bearer $LM_STUDIO_API_KEY" | jq '.data[].id'
```

Ожидается список загруженных моделей. Если пусто `data: []` — ни одна модель не загружена в LM Studio app. Если **401** — токен не совпадает (Settings → Network → API token в LM Studio app). Если **connection refused** — server не запущен (LM Studio → Developer → Start Server).

### 5.4. End-to-end chat completion

```bash
curl -sS http://127.0.0.1:18789/v1/chat/completions \
  -H "Authorization: Bearer 9FzMdS4ACkMbowogjVYPPmDS0Oi3kwRQxkZHIlq241o" \
  -H "x-openclaw-scopes: operator.write" \
  -H "Content-Type: application/json" \
  -d '{"model":"openclaw/main","messages":[{"role":"user","content":"ping"}]}' | jq '.choices[0].message.content'
```

Должен вернуть текстовый ответ. Аналогично для MLX (без Authorization) и LM Studio (свой токен).

## 6. Где Cherry Studio хранит конфиги

Cherry Studio пишет настройки в локальный config:

- **macOS:** `~/Library/Application Support/CherryStudio/`
  - `config.json` — общие настройки
  - `providers.json` — список custom providers (зашифровано или plain, зависит от версии)
  - `chat_history.db` — SQLite база чатов
- **Logs:** `~/Library/Logs/CherryStudio/` (если включён debug mode в Settings → Advanced)

Бэкап конфига перед массовыми изменениями:

```bash
cp -r ~/Library/Application\ Support/CherryStudio ~/cherry_studio_backup_$(date +%Y%m%d)
```

Если что-то сломалось после обновления Cherry Studio — restore из backup и перезапустить.

## 7. Совместимость и edge cases

- **Streaming:** все 3 backend поддерживают SSE `stream: true`. Cherry Studio дефолтно включает streaming — отключать не нужно.
- **System prompt:** OpenClaw применяет свой system prompt поверх Cherry Studio. Если хочется чистого режима — использовать MLX direct.
- **Context window:** MLX OptiQ — 128k, LM Studio Gemma-4-e4b — 32k, OpenClaw → зависит от routed модели (Gemini 3 Pro = 2M).
- **Tool-call формат:** OpenClaw возвращает в OpenAI tool_calls schema, Cherry Studio парсит корректно. Direct backend'ы — нет.

## 8. Troubleshooting checklist

1. Endpoint отвечает на `curl /v1/models`?
2. API key прописан корректно (без trailing whitespace)?
3. Cherry Studio перезапущен после добавления provider?
4. Модель добавлена manually в provider (auto-discovery нет)?
5. Для OpenClaw — header `x-openclaw-scopes` присутствует?
6. Для MLX — RotorQuant LaunchAgent запущен (`launchctl list | grep rotorquant`)?
7. Для LM Studio — модель загружена в app (видна в **My Models** с зелёной точкой)?
