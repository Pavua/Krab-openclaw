# MCP Inventory — Wave 44-Z

Полный реестр MCP-серверов, доступных для Krab agent loop через OpenClaw gateway.
Обновлено: 09.05.2026.

Runtime authority — `openclaw mcp list` (`/Users/pablito/.openclaw/openclaw.json`).
Этот документ — справочник: что зарегистрировано, что доступно как kabель,
какие ключи нужны, и как добавить новый сервер.

## Зарегистрированы (`openclaw mcp list`)

| Name | Transport | URL / cmd | Назначение | Auth |
|---|---|---|---|---|
| `krab-telegram` | sse | `127.0.0.1:8011/sse` | Telegram userbot (yung_nagato session) | Pyrogram session file |
| `krab-telegram-owner` | sse | `127.0.0.1:8012/sse` | Telegram userbot (p0lrd / owner main) | Pyrogram session file |
| `krab-hammerspoon` | sse | `127.0.0.1:8013/sse` | macOS window mgmt через Hammerspoon HTTP `:10101` | none (localhost) |
| `krab-tor` | sse | `127.0.0.1:8014/sse` | Анонимный HTTP через Tor SOCKS5 (`tor_status`/`tor_fetch`/`tor_check_exit_ip`) | none (localhost) |

LaunchAgents:
- `scripts/launchagents/com.krab.mcp-yung-nagato.plist`
- `scripts/launchagents/com.krab.mcp-p0lrd.plist`
- `scripts/launchagents/com.krab.mcp-hammerspoon.plist`
- `scripts/launchagents/com.krab.mcp-tor.plist` (Wave 44-Z, активируется после merge в `main`,
  т.к. использует `cwd=/Users/pablito/Antigravity_AGENTS/Краб` где должен быть
  `src/mcp_tor_server.py`)

## Доступны в claude-plugins-official, НЕ зарегистрированы (нужны ключи)

Кэш: `/Users/pablito/.claude/plugins/cache/claude-plugins-official/`. Большинство —
HTTP-MCP с OAuth/HTTP-headers. Чтобы добавить, прописать ключ в `.env` и зарегистрировать.

| Сервер | Transport | URL / cmd | Назначение | Требуемый env / auth |
|---|---|---|---|---|
| context7 | stdio | `npx -y @upstash/context7-mcp` | Актуальные API docs для библиотек | `CONTEXT7_API_KEY` (уже в .env.example, опц.) |
| sentry | http | `https://mcp.sentry.dev/mcp` | Sentry issues + analytics | `SENTRY_AUTH_TOKEN` (OAuth flow) |
| linear | http | `https://mcp.linear.app/mcp` | Issues / projects / cycles | `LINEAR_API_KEY` (OAuth) |
| supabase | http | `https://mcp.supabase.com/mcp` | DB / edge functions / migrations | `SUPABASE_ACCESS_TOKEN` |
| notion | http | `https://mcp.notion.com/mcp` | Pages / databases | `NOTION_API_KEY` (OAuth) |
| slack | http | `https://mcp.slack.com/mcp` | Channels / DM / search | `SLACK_BOT_TOKEN` (OAuth flow) |
| atlassian | http | `https://mcp.atlassian.com/v1/mcp` | Jira / Confluence | `ATLASSIAN_API_TOKEN` |
| asana | sse | `https://mcp.asana.com/sse` | Tasks / projects | `ASANA_ACCESS_TOKEN` |
| figma | http | `https://mcp.figma.com/mcp` | Design files / variables / Code Connect | `FIGMA_ACCESS_TOKEN` |
| github | http | `https://api.githubcopilot.com/mcp/` | Issues / PRs / repos / actions | `GITHUB_PERSONAL_ACCESS_TOKEN` (header) |
| gitlab | http | `https://gitlab.com/api/v4/mcp` | GitLab issues / pipelines | GitLab PAT |
| firecrawl | local | (npx) | Web scraping | `FIRECRAWL_API_KEY` |
| stripe | http | `https://mcp.stripe.com` | Payments / subscriptions (PRODUCTION!) | `STRIPE_API_KEY` |
| firebase | stdio | `npx -y firebase-tools@latest mcp` | Firebase admin | gcloud auth |
| playwright | stdio | `npx @playwright/mcp@latest` | Headless browser (overlap with openclaw-browser) | none |
| planetscale | http | `https://mcp.pscale.dev/mcp/planetscale` | DB branches / queries | `PLANETSCALE_SERVICE_TOKEN` |
| huggingface | http | `https://huggingface.co/mcp?login` | Models / datasets / Spaces | `HF_TOKEN` |
| fastly-agent-toolkit | local | (см. plugin) | CDN / WAF | Fastly API key |

**Cloudflare** — НЕ найден в claude-plugins-official, но есть в инструменте `mcp__051b3196-...` ToolSearch.
Для production использовать `https://mcp.cloudflare.com/sse` с `CLOUDFLARE_API_TOKEN` (OAuth).

**Платные API** (предупреждение):
- `stripe` — production финансы; использовать только для read-only / staging.
- `vibe-prospecting` — платный data API.
- `zapier` — платная подписка.

## Skipped в этой Wave (нужен API key)

В Wave 44-Z НЕ регистрируем ни один из них до получения ключей:

```
context7, sentry, linear, supabase, notion, slack, atlassian, asana,
figma, github, gitlab, firecrawl, stripe, firebase, playwright,
planetscale, huggingface, cloudflare
```

Чтобы активировать: добавить ключ в `.env` → `openclaw mcp set <name> '<json>'`
(пример JSON см. в таблице выше + `claude-plugins-official/<plugin>/.mcp.json`).

## Tor MCP (новое в Wave 44-Z)

**Файл:** `src/mcp_tor_server.py`. **Тесты:** `tests/unit/test_mcp_tor_server.py` (8/8).

Tools:
- `tor_status()` → `{"available": bool, "exit_ip": str | None, "error": str | None}`.
- `tor_check_exit_ip()` → `{"ip": str | None}`.
- `tor_fetch(url, method, headers, timeout)` → `{"ok": bool, "status": int, "text": str (≤50KB), "url": str}` или `{"ok": False, "error": str}`.

Зависимости:
- Tor daemon: `com.krab.tor-daemon` LaunchAgent (уже работает на :9050).
- `src/integrations/tor_bridge.py` (httpx + SOCKS5 proxy).

**Legal use only.** Tor MCP не делает исключений для запрещённого контента.
Назначение: research, region-blocked docs, IP-rotation для тестов rate-limiter'ов,
.onion-зеркала легальных сервисов.

## Как добавить новый MCP

1. Получить API key, записать в `.env`.
2. Подсмотреть формат в `~/.claude/plugins/cache/claude-plugins-official/<name>/.mcp.json`.
3. Зарегистрировать:
   ```bash
   openclaw mcp set <name> '{"transport":"http","url":"https://...","headers":{"Authorization":"Bearer ${TOKEN}"}}'
   # для stdio:
   openclaw mcp set <name> '{"command":"npx","args":["-y","@vendor/mcp"]}'
   # для sse:
   openclaw mcp set <name> '{"transport":"sse","url":"http://127.0.0.1:PORT/sse"}'
   ```
4. Verify: `openclaw mcp list`.
5. Restart gateway: `openclaw gateway` (для применения).
6. Обновить таблицу в этом файле + system prompt suffix в
   `src/userbot/access_control.py` (search `KRAB_EXTERNAL_MCP_HINT_ENABLED`).

## codex-cli config

`~/.codex/config.toml` хранит свой список MCP. Wave 9-B отключил там
`krab-telegram*` чтобы избежать дублирования. **Не трогаем** в Wave 44-Z.

## Связанные файлы

- `src/mcp_tor_server.py` — Tor MCP server (новое).
- `src/integrations/tor_bridge.py` — SOCKS5 httpx обёртка.
- `src/userbot/access_control.py` — `_append_runtime_constraints()` →
  external MCP hint suffix (Wave 44-Z).
- `scripts/launchagents/com.krab.mcp-tor.plist`.
- `scripts/launchagents/com.krab.tor-daemon.plist`.
- `.env.example` — список env vars для будущих регистраций.
