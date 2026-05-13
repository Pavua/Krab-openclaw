# Cherry Studio + Krab MCP gateway

Wave 236 — Krab экспонирует свой tool registry как standalone MCP server
endpoint в owner panel (порт `8080`). Cherry Studio (или любой другой
MCP-клиент: IDE plugins, CLI tools, custom Python scripts) может
подключиться напрямую без proxy.

## Endpoints

Base: `http://127.0.0.1:8080/api/mcp/`

| Method | Path                          | Purpose                                       |
|--------|-------------------------------|-----------------------------------------------|
| GET    | `/api/mcp/server/info`        | Handshake: name, version, protocolVersion     |
| POST   | `/api/mcp/tools/list`         | Список инструментов (MCP-native format)       |
| GET    | `/api/mcp/tools/list`         | То же, GET-вариант для curl/browser           |
| POST   | `/api/mcp/tools/call`         | Выполнение tool: `{name, arguments}`          |
| GET    | `/api/mcp/sse`                | Server-Sent Events stream                     |
| GET    | `/admin/mcp`                  | HTML страница со списком + test runner        |

Protocol: MCP spec **2024-11-05** (`https://spec.modelcontextprotocol.io/`).

## Setup в Cherry Studio

1. Открыть **Settings → MCP Servers**.
2. **Add server** → выбрать transport `SSE` или `HTTP`.
3. Указать URL:
   - SSE: `http://127.0.0.1:8080/api/mcp/sse`
   - HTTP base: `http://127.0.0.1:8080/api/mcp`
4. Auth: добавить header **Authorization** = `Bearer <WEB_API_KEY>`.
   - WEB_API_KEY можно подсмотреть в `.env` (или установить через
     `export WEB_API_KEY=...`).
   - Если WEB_API_KEY пустой, owner panel открыт без авторизации
     (только loopback `127.0.0.1`).
5. Save → Cherry Studio выполнит handshake через `server/info` и подтянет
   tool list через `tools/list`.

После этого все Krab tools (peekaboo, web_search, tor_fetch, voice:*,
userbot_self:*, vpn:*, plus все proxied MCP-servers — brave-search,
firecrawl, github, sentry, hexstrike, tor-full, osint-tools, …) видны
в Cherry Studio chat completion.

## Пример: curl handshake

```bash
# 1. Server info
curl -s http://127.0.0.1:8080/api/mcp/server/info | jq

# 2. Tools list
curl -s -X POST http://127.0.0.1:8080/api/mcp/tools/list -d '{}' | jq '.tools | length'

# 3. Call tool
curl -s -X POST http://127.0.0.1:8080/api/mcp/tools/call \
  -H "Authorization: Bearer $WEB_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"name":"web_search","arguments":{"query":"weather Madrid"}}' | jq
```

## Response shape

`tools/list` (MCP spec):
```json
{"tools": [
  {"name": "web_search",
   "description": "Поиск информации в интернете…",
   "inputSchema": {"type":"object","properties":{"query":{"type":"string"}}}}
]}
```

`tools/call`:
```json
{"content": [{"type": "text", "text": "…результат…"}],
 "isError": false}
```

## SSE event stream

Подключение `GET /api/mcp/sse` шлёт:
1. `event: endpoint` — server metadata (name/version/protocolVersion).
2. `event: tools` — snapshot tool list.
3. `: ping <ts>` keepalive каждые ~15 секунд.

Лимит времени для тестов: `KRAB_MCP_SSE_MAX_SECONDS=<int>`.

## Auth матрица

| Endpoint              | WEB_API_KEY empty | WEB_API_KEY set                            |
|-----------------------|-------------------|--------------------------------------------|
| `server/info`         | open              | open (handshake)                           |
| `tools/list` (GET+POST)| open             | open (read-only)                           |
| `tools/call`          | open              | requires Bearer OR X-Krab-Web-Key / token  |
| `sse`                 | open              | open (read-only stream)                    |
| `/admin/mcp`          | open              | open (HTML)                                |

## Troubleshooting

- **Empty tools list** → MCP relay не запущен. Проверь
  `mcp_manager.is_running` (через `/api/system/status` или
  `mcp_manager.health_check()`).
- **tool returns isError=true** → ошибка пробрасывается из
  `mcp_manager.call_tool_unified` (timeout, отсутствует server, swarm
  allowlist block). Текст ошибки — в `content[0].text`.
- **403** на `tools/call` → Bearer не совпадает с `WEB_API_KEY`.

## Файлы

- Router: `src/modules/web_routers/mcp_gateway_router.py`
- Тесты: `tests/unit/test_mcp_gateway_router.py`
- Источник tools: `src/mcp_client.py::MCPClientManager.get_tool_manifest`
