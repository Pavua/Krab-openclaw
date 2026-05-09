# MCP Inventory

Полный реестр MCP-серверов, доступных для Krab agent loop через OpenClaw gateway.
Обновлено: 10.05.2026 (Wave 50-B: deprecation `krab-tor` → `tor-full`).

> **Wave 50-B deprecation note (2026-05-10):** `krab-tor` (3-tool SSE wrapper
> на :8014) снят с регистрации в OpenClaw — `tor-full` (25 tools, stdio)
> обладает superset функциональностью (browse_onion / circuit-identity /
> shodan / censys / gpg / port_scan и др). 7-day observation period после
> Wave 45-F прошёл без issues. `src/mcp_tor_server.py` + plist preserved
> для archaeology и emergency fallback. См. секцию «Tor MCP» ниже.

Runtime authority — `openclaw mcp list` (`/Users/pablito/.openclaw/openclaw.json`).
Этот документ — справочник: что зарегистрировано, что доступно как кабель,
какие ключи нужны, и как добавить новый сервер.

**Helper:** `scripts/openclaw_mcp_register.py` + `scripts/mcp_inventory.toml`.

```bash
# Что уже зарегистрировано + что доступно из реестра:
python scripts/openclaw_mcp_register.py --list

# Зарегистрировать одну запись (требует токен в env, если указан в required_env):
python scripts/openclaw_mcp_register.py --add github

# Зарегистрировать всё, для чего токены доступны:
python scripts/openclaw_mcp_register.py --add-all-with-tokens

# Удалить:
python scripts/openclaw_mcp_register.py --remove <name>
```

## Зарегистрированы (`openclaw mcp list`, после Wave 45-F)

| Name | Transport | URL / cmd | Назначение | Auth |
|---|---|---|---|---|
| `krab-telegram` | sse | `127.0.0.1:8011/sse` | Telegram userbot (yung_nagato session) | Pyrogram session file |
| `krab-telegram-owner` | sse | `127.0.0.1:8012/sse` | Telegram userbot (p0lrd / owner main) | Pyrogram session file |
| `krab-hammerspoon` | sse | `127.0.0.1:8013/sse` | macOS window mgmt через Hammerspoon HTTP `:10101` | none (localhost) |
| ~~`krab-tor`~~ | ~~sse~~ | ~~`127.0.0.1:8014/sse`~~ | **DEPRECATED Wave 50-B (2026-05-10)** — заменён на `tor-full` (25 tools, superset). LaunchAgent unloaded; код preserved в `src/mcp_tor_server.py` для archaeology + fallback. | n/a |
| `context7` | stdio | `npx -y @upstash/context7-mcp` | Live API docs lookup для библиотек/SDK | none (rate-limited) |
| `github` | streamable-http | `https://api.githubcopilot.com/mcp/` | Issues / PRs / repos / actions | `${GITHUB_PERSONAL_ACCESS_TOKEN}` (header) |
| `firecrawl` | stdio | `npx -y firecrawl-mcp` | Web scraping/crawl/map | `${FIRECRAWL_API_KEY}` |
| `sentry` | streamable-http | `https://mcp.sentry.dev/mcp` | Sentry issues + analytics | `${SENTRY_AUTH_TOKEN}` |
| `tor-full` | stdio | `~/Antigravity_AGENTS/tor-mcp/server.py` | Полноценный Tor MCP — circuit/identity/dark-web (25 tools) | none |
| `osint-tools` | stdio | `~/Antigravity_AGENTS/osint-tools-mcp-server/src/...` | OSINT свёрстка: sherlock+holehe+maigret+theharvester (7 tools) | none |
| `hexstrike-ai` | stdio | `hexstrike_env/bin/python hexstrike_mcp.py --server :8888` | 151 offensive security tools (manual server start, Wave 49-C) | none (localhost server) |

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

## Skipped (нужен API key)

В Wave 45-B зарегистрировали `context7` (no token) и `github`
(`${GITHUB_PERSONAL_ACCESS_TOKEN}` уже в `~/.zshrc`). Остальные MCP остались
доступны как «кабели» — пропишите токен в `.env`/`~/.zshrc` и вызовите
`scripts/openclaw_mcp_register.py --add <name>`.

```
sentry, linear, supabase, notion, slack, atlassian, asana,
figma, gitlab, firecrawl, stripe, firebase, playwright,
planetscale, huggingface, cloudflare
```

Шаблоны header-ов (`${VAR}`) хранятся в реестре `scripts/mcp_inventory.toml` и
**НЕ** резолвятся скриптом — OpenClaw сам разворачивает их через
`env.shellEnv.enabled=true` в `~/.openclaw/openclaw.json` при чтении конфига.

## Tor MCP (Wave 44-Z `krab-tor` → Wave 45-F `tor-full` → Wave 50-B deprecation)

### Текущий статус (Wave 50-B, 2026-05-10)

Активный Tor MCP — **`tor-full`** (stdio, 25 tools), репо
`/Users/pablito/Antigravity_AGENTS/tor-mcp/`. Зарегистрирован в OpenClaw
через `scripts/openclaw_mcp_register.py --add tor-full` (Wave 45-F).

`krab-tor` (3-tool SSE wrapper, Wave 44-Z) **DEPRECATED**:
- LaunchAgent `com.krab.mcp-tor` unloaded оркестратором.
- OpenClaw запись `krab-tor` снята (`openclaw mcp unset krab-tor`).
- `src/mcp_tor_server.py` + `scripts/launchagents/com.krab.mcp-tor.plist`
  preserved для archaeology + emergency fallback.
- `tests/unit/test_mcp_tor_server.py` (8/8) всё ещё pass — regression safety.

### Архаика: `krab-tor` (Wave 44-Z, deprecated)

**Файл:** `src/mcp_tor_server.py`. **Тесты:** `tests/unit/test_mcp_tor_server.py` (8/8).

Tools (subset; все доступны в `tor-full` + 22 ещё):
- `tor_status()` → `{"available": bool, "exit_ip": str | None, "error": str | None}`.
- `tor_check_exit_ip()` → `{"ip": str | None}`.
- `tor_fetch(url, method, headers, timeout)` → `{"ok": bool, "status": int, "text": str (≤50KB), "url": str}` или `{"ok": False, "error": str}`.

Зависимости:
- Tor daemon: `com.krab.tor-daemon` LaunchAgent (уже работает на :9050).
- `src/integrations/tor_bridge.py` (httpx + SOCKS5 proxy).

**Legal use only.** Tor MCP не делает исключений для запрещённого контента.
Назначение: research, region-blocked docs, IP-rotation для тестов rate-limiter'ов,
.onion-зеркала легальных сервисов.

### Manual fallback (если tor-full недоступен)

```bash
# Вернуть krab-tor временно (до восстановления tor-full):
launchctl load ~/Library/LaunchAgents/com.krab.mcp-tor.plist  # если plist ещё на месте
openclaw mcp set krab-tor '{"transport":"sse","url":"http://127.0.0.1:8014/sse"}'
openclaw gateway  # перечитать конфиг
```

## Как добавить новый MCP

**Рекомендуемый путь — через helper:**

1. Получить API key, записать в `.env` или `~/.zshrc` (export).
2. Если сервера нет в `scripts/mcp_inventory.toml` — добавить туда секцию
   (см. формат в комментариях наверху TOML).
3. `python scripts/openclaw_mcp_register.py --add <name>` (или `--dry-run`,
   чтобы только напечатать команду).
4. Verify: `python scripts/openclaw_mcp_register.py --list`.
5. Restart gateway: `openclaw gateway` (для применения; **не SIGHUP**).
6. При необходимости обновить system prompt suffix в
   `src/userbot/access_control.py` (search `KRAB_EXTERNAL_MCP_HINT_ENABLED`).

**Прямой путь (без helper-а):**
```bash
openclaw mcp set <name> '{"transport":"streamable-http","url":"https://...","headers":{"Authorization":"Bearer ${TOKEN}"}}'
# для stdio:
openclaw mcp set <name> '{"command":"npx","args":["-y","@vendor/mcp"]}'
# для sse:
openclaw mcp set <name> '{"transport":"sse","url":"http://127.0.0.1:PORT/sse"}'
```

OpenClaw принимает только три transport-а: `streamable-http` / `sse` / `stdio`.
Helper маппит canonical `"http"` → `"streamable-http"` автоматически.

## HexStrike-AI MCP (Wave 49-C)

**Repo:** `/Users/pablito/Antigravity_AGENTS/hexstrike-ai/` (внешний, не в Krab tree).
**Isolated venv:** `/Users/pablito/Antigravity_AGENTS/hexstrike-ai/hexstrike_env/` (Python 3.13).
**Heavy deps installed in venv (НЕ в Krab venv):** flask, fastmcp, selenium, mitmproxy,
pwntools, angr, bcrypt==4.0.1, beautifulsoup4, aiohttp.

**Architecture:**
- Flask server `hexstrike_server.py` слушает `:8888` (manual start only).
- MCP client `hexstrike_mcp.py` (stdio, FastMCP) проксирует tool calls в server.
- OpenClaw stdio запись: `command=hexstrike_env/bin/python`, `args=[..., --server, http://127.0.0.1:8888]`.

**Manual start procedure (Hexstrike Toggle.command):**
1. `scripts/Hexstrike Toggle.command` (osascript dialog: Load / Unload / Cancel).
2. Скрипт копирует `scripts/launchagents/com.krab.hexstrike-server.plist` в
   `~/Library/LaunchAgents/`, затем `launchctl load -w`.
3. Server health: `curl http://127.0.0.1:8888/health`.
4. **NOT** `RunAtLoad`/`KeepAlive` — лежит unloaded по умолчанию, активация
   только через явное действие оператора.

**Risk model:**
- HexStrike orchestrates 151 offensive tools (nmap, sqlmap, hydra, nuclei,
  hashcat, gobuster, metasploit-aux, и т.д.) — auto-start был бы major safety
  violation.
- Из CLI baseline уже via brew: `nmap`, `sqlmap`, `nuclei`, `hydra`, `hashcat`,
  `gobuster`, `ffuf`, `nikto`, `subfinder` (9 шт).
- Heavy bin-analysis tools (ghidra, radare2, volatility3, и т.д.) — НЕ
  устанавливаем (legal/operational risk вне явной authorization).
- Operator-gated через `Hexstrike Toggle.command` + dialog с предупреждением.

**Files:**
- `scripts/launchagents/com.krab.hexstrike-server.plist`
- `scripts/Hexstrike Toggle.command`
- `tests/unit/test_hexstrike_setup_wave49c.py`

## codex-cli config

`~/.codex/config.toml` хранит свой список MCP. Wave 9-B отключил там
`krab-telegram*` чтобы избежать дублирования. **Не трогаем** в Wave 44-Z.

## Local custom MCP repos (`~/Antigravity_AGENTS/`)

Локальные форки/самописные MCP серверы из workspace оператора. Все используют
Krab venv (`~/Antigravity_AGENTS/Краб/venv`).

| Repo | Tools | Status | Назначение |
|---|---|---|---|
| `tor-mcp/` | 25 | **Registered** as `tor-full` (Wave 45-F). Replacement для `krab-tor` (Wave 50-B deprecation 2026-05-10). | Полноценный Tor — `tor_status`/`tor_fetch`/`tor_check_exit_ip` + circuit/identity/dark-web search + shodan/censys/virustotal/gpg/port_scan. Superset over deprecated `krab-tor` (3-tool subset). |
| `osint-tools-mcp-server/` | 7 | **Registered** as `osint-tools` (Wave 45-F) | OSINT toolkit — username/email/DNS/subdomain enum через sherlock/holehe/maigret/theharvester. Deps в Krab venv. |
| `hive-crypto-mcp/` | 376 | **Deferred** | Hive Intelligence — crypto/blockchain market data. Требует `HIVE_API_KEY` (https://hiveintelligence.xyz). После получения ключа активировать через `--add hive-crypto`. |
| `hexstrike-ai/` | 151 | **Deferred (HIGH RISK)** | Offensive security — recon/exploit/CTF tools (nmap/sqlmap/metasploit-grade). Требует isolated venv + ACL gate + owner approval перед регистрацией. **НЕ** активировать через `--add-all-with-tokens`. |

**Important — networkx downgrade (Wave 45-F):** установка osint deps понизила
`networkx 3.6.1 → 2.8.8` (constraint от `pyvis` в maigret). Krab src не
импортирует networkx, impact = none. Если в будущем потребуется ≥3.x —
изолировать osint в отдельный venv.

## Связанные файлы

- `src/mcp_tor_server.py` — Tor MCP server (новое).
- `src/integrations/tor_bridge.py` — SOCKS5 httpx обёртка.
- `src/userbot/access_control.py` — `_append_runtime_constraints()` →
  external MCP hint suffix (Wave 44-Z).
- `scripts/launchagents/com.krab.mcp-tor.plist`.
- `scripts/launchagents/com.krab.tor-daemon.plist`.
- `.env.example` — список env vars для будущих регистраций.
