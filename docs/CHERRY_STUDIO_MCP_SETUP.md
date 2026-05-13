# Cherry Studio — native MCP integration с Krab

Документ описывает, как подключить Cherry Studio к Krab через **встроенный MCP-клиент**
Cherry Studio (Settings → MCP). После настройки чаты в Cherry Studio получают прямой
доступ к инструментам Краба (RAG, swarm, memory, web search, MCP relay) без прокси.

> Связанные документы: [CHERRY_STUDIO_SETUP.md](CHERRY_STUDIO_SETUP.md) — настройка
> backend'ов как OpenAI-compatible providers (модели). Текущий документ — про **MCP-канал**,
> комплементарный к chat-провайдеру.

## 1. Зачем MCP для Cherry Studio

Cherry Studio с версии, поддерживающей нативный MCP-клиент, умеет подключаться к
внешним MCP-серверам и автоматически прокидывать их tools в LLM. Это даёт два
сценария поверх обычного chat:

- **Cherry Studio как frontend, Krab как tool backend.** Любая модель в Cherry Studio
  (включая внешние Anthropic / OpenAI / Gemini ключи пользователя) может вызывать
  Krab-инструменты: поиск в архивах iMessage/Telegram, RAG по long-term memory,
  запуск swarm-команд, web_search, MCP relay (Hammerspoon, Apple Notes, Reminders).
- **Без proxy.** В отличие от схемы "Cherry Studio → OpenClaw → tools" (см.
  `CHERRY_STUDIO_SETUP.md`), MCP идёт напрямую: Cherry Studio выступает MCP-клиентом,
  Krab — MCP-сервером. Меньше hops, меньше overhead, проще debug.

**Альтернатива — Wave 234 `/admin/spawn`** (см. `WAVE_234_ADMIN_SPAWN.md`): запуск
свёрма из веб-UI. Это удобно для one-off задач, но не даёт LLM в Cherry Studio
автоматически решать, когда и какие tools звать. MCP-интеграция как раз про
автоматический tool-routing на стороне модели.

## 2. Cherry Studio MCP Setup

> Предполагается, что Wave 236 уже задеплоен и endpoint
> `http://127.0.0.1:8080/api/mcp/sse` отвечает. Проверить:
> `curl -sS -H "Authorization: Bearer $(cat ~/.openclaw/krab_runtime_state/web_api_key.txt)" \
> http://127.0.0.1:8080/api/mcp/sse | head -5` — должна быть SSE-сессия (`event: ...`).

### 2.1. Открыть настройки MCP

В Cherry Studio: **Settings → MCP → Add MCP Server** (кнопка `+` в правом верхнем углу
панели MCP).

### 2.2. Заполнить поля

| Поле | Значение |
|---|---|
| Name | `Krab Tools` |
| Type | `HTTP/SSE` |
| URL | `http://127.0.0.1:8080/api/mcp/sse` |
| Auth Type | `Bearer Token` |
| Token | значение из `~/.openclaw/krab_runtime_state/web_api_key.txt` |
| Protocol Version | `2024-11-05` (см. секцию 6) |
| Enabled | ✅ |

**Где взять token:**

```bash
# Способ 1: файл
cat ~/.openclaw/krab_runtime_state/web_api_key.txt

# Способ 2: через /admin/env (требует уже авторизованной сессии в owner panel)
open http://127.0.0.1:8080/admin/env
# Ищи KRAB_WEB_API_KEY в списке env-vars
```

### 2.3. Сохранить и перезапустить

Нажать **Save**. Cherry Studio попытается подключиться сразу — статус должен быть
🟢 `Connected`. Если 🔴 — перезапустить Cherry Studio (бывает на первой регистрации
MCP-сервера, кеш handshake).

## 3. Verification

### 3.1. Проверить tool list

В Cherry Studio открыть любой chat и спросить:

> Какие у тебя есть tools от Krab?

Модель должна перечислить инструменты от MCP-сервера `Krab Tools` — обычно это
`memory_search`, `memory_save`, `swarm_dispatch`, `web_search`, `imessage_search`,
`telegram_search` и др. (точный список — какой Wave 236 пробрасывает).

### 3.2. Тестовый tool call

> Найди мои последние заметки про X.

Модель должна выбрать `memory_search` или `imessage_search`, дернуть Krab, и вернуть
результат. В Cherry Studio UI tool calls видны как развёрнутые блоки под ответом.

### 3.3. Где смотреть логи

- **Krab side**: `/admin/logs` с фильтром `module=mcp_gateway` (Wave 169 live log
  page) — показывает входящие MCP-запросы, tool dispatching, ответы.
- **Cherry Studio side**: Settings → MCP → выбрать `Krab Tools` → tab "Logs" (показывает
  handshake, errors, tool invocations со стороны клиента).

При несоответствии — сверить `protocolVersion` в handshake и Bearer token.

## 4. Известные ограничения

- **Tools исполняются в процессе Krab.** Cherry Studio получает full access к
  тому же tool set, который доступен Krab userbot'у. Это значит: web_search,
  swarm dispatch, чтение iMessage/Telegram архивов — всё доступно любому chat'у
  в Cherry Studio. Token защищает доступ, но если token утёк — компрометирован
  весь tool surface. Хранить `web_api_key.txt` как secret.
- **Rate limiting**: Wave 122-A middleware (`src/modules/web_app.py` middleware
  chain) ограничивает MCP-endpoint так же, как остальные `/api/*` маршруты.
  Дефолт — 60 req/min на token. При flood Cherry Studio получит `429`.
- **Auth обязателен.** Без `Authorization: Bearer ...` header endpoint вернёт
  `403 Forbidden`. Это не отключается флагом — owner panel middleware жёстко
  требует auth для `/api/mcp/*`.
- **Streaming**: SSE-канал держится открытым на всё время сессии. Если Krab
  перезапускается (`new Stop Krab.command` → `new start_krab.command`),
  Cherry Studio переподключится автоматически, но in-flight tool call упадёт
  с `connection reset`.
- **Только localhost.** Endpoint биндится на `127.0.0.1:8080`. Cherry Studio
  на другой машине не подключится без SSH-tunnel или Tailscale.

## 5. Альтернативные scenario

### "Я хочу Cherry Studio только chat (без Krab tools)"

Не добавлять MCP-сервер. Достаточно настроенного provider'a (см.
`CHERRY_STUDIO_SETUP.md`, секции про OpenClaw / MLX / LM Studio). Tools не появятся,
но chat работает.

### "Я хочу Cherry Studio + Krab tools"

Setup из секций 2-3 выше. Provider'ы (chat) и MCP-сервер (tools) — независимые
сущности, оба нужны.

### "Я хочу чтобы Krab Telegram отвечал через модель, выбранную в Cherry Studio"

Это **не** про MCP — это про модель-роутинг **на стороне Krab**. Использовать
`/admin/models` picker в owner panel (Wave 88) или Telegram-команду `!model set <id>`.
Cherry Studio здесь не нужен — Krab сам решает, какую модель использовать для
ответов в Telegram-чатах.

### "Я хочу запустить swarm из Cherry Studio one-off, без MCP"

Открыть `http://127.0.0.1:8080/admin/spawn` (Wave 234) — там UI для разового
запуска свёрма, без интеграции с Cherry Studio.

## 6. MCP protocol reference

Cherry Studio на момент Wave 236 использует MCP spec version **`2024-11-05`**.
Krab endpoint декларирует ту же версию в `initialize` response.

- Spec: <https://spec.modelcontextprotocol.io/specification/2024-11-05/>
- Transport: SSE (Server-Sent Events) поверх HTTP/1.1
- Auth: HTTP `Authorization: Bearer <token>` (не в spec напрямую, но рекомендуемый
  pattern для HTTP-транспорта; Krab именно так)
- Capabilities: server-side `tools`, `resources` (опционально), `prompts` (опционально)

Если Cherry Studio обновится до более новой версии spec — Krab side нужно поднять
`MCP_PROTOCOL_VERSION` в `src/core/mcp_gateway.py` (Wave 236) и проверить совместимость.

## 7. Troubleshooting

| Симптом | Причина | Fix |
|---|---|---|
| `403 Forbidden` в Cherry Studio MCP logs | wrong / missing token | Скопировать заново из `web_api_key.txt`, убрать пробелы |
| `Connection refused` | Krab не запущен или порт `8080` занят | `lsof -i :8080`, restart Krab через `new start_krab.command` |
| Tool list пустой | MCP gateway не зарегистрировал tools | `/admin/logs` filter `module=mcp_gateway`, искать `tools_registered=...` |
| Tool call зависает | LLM-side timeout vs Krab-side long-running | Tools >30s — увеличить timeout в Cherry Studio MCP settings |
| `429 Too Many Requests` | Wave 122-A rate limit | Подождать минуту или увеличить лимит в `web_app.py` middleware |
| Cherry Studio crash после Save | bad URL format | URL должен быть с `http://` префиксом и `/sse` суффиксом |

## 8. Безопасность

- **Token rotation**: при подозрении на утечку — сгенерировать новый
  `web_api_key.txt` (`openssl rand -base64 32 > ~/.openclaw/krab_runtime_state/web_api_key.txt`)
  и обновить в Cherry Studio MCP settings.
- **Audit log**: все MCP tool calls пишутся в `/admin/logs` с `module=mcp_gateway`.
  Можно периодически просматривать на предмет unexpected invocations.
- **Не публиковать конфиг**: при экспорте Cherry Studio settings (backup) — убедиться,
  что Bearer token redacted перед share.
