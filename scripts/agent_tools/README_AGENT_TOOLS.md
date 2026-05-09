# Agent Tools (Wave 44-R-script-tools)

Bash-вызываемые скрипты для codex-cli (gpt-5.5) внутри Krab agent loop.
Контекст: после Wave 9-B (02.05) Telegram MCP отключены в codex
(`/Users/pablito/.codex/config.toml`) из-за hallucinated tool calls.
Эти скрипты — альтернатива: codex вызывает их через нативный bash tool,
а действия выполняются через kraab.session (main userbot session).

## Скрипты

Все возвращают JSON `{"ok": bool, ...}` и логируют в `/tmp/krab_agent_tools.log`.

### 1. `krab_send_to_swarm.py`

Постит сообщение в Krab Swarm group (-1003703978531).

```bash
venv/bin/python scripts/agent_tools/krab_send_to_swarm.py \
    --text "!swarm task create --auto analysts CryptoBot M0: research"
# {"ok": true, "message_id": 4321, "chat_id": -1003703978531, ...}

# С topic_id:
venv/bin/python scripts/agent_tools/krab_send_to_swarm.py \
    --text "!swarm coders loop 2 ..." --topic 5
```

### 2. `krab_send_dm.py`

Сообщение в whitelisted chat (Krab Swarm group или owner DM 312322764).

```bash
venv/bin/python scripts/agent_tools/krab_send_dm.py \
    --chat-id 312322764 --text "Готово"
# Любой другой chat: добавь --allow-any
```

### 3. `krab_screenshot.py`

Скриншот через `screencapture -x` (без shutter). Валидация: > 20KB и
не all-white (если установлен Pillow).

```bash
venv/bin/python scripts/agent_tools/krab_screenshot.py \
    --output /tmp/screen.png
# {"ok": true, "path": "/tmp/screen.png", "size_bytes": 245678, ...}
```

### 4. `krab_run_command.py`

Выполняет !command — сначала через HTTP /api/* (owner panel :8080), при
fallback'е отправляет в owner DM, где работающий Krab userbot её обработает.

```bash
venv/bin/python scripts/agent_tools/krab_run_command.py --command "!status"
# {"ok": true, "mode": "http", "result": {...}}

venv/bin/python scripts/agent_tools/krab_run_command.py --command "!swarm teams"
# {"ok": true, "mode": "dm-delivery", "message_id": ..., note: "..."}
```

## Правила для агента

1. **Всегда** проверяй `ok` поле — если `false`, **не говори "отправил"**;
   репортинь real `error` владельцу.
2. **Идемпотентность**: re-run = новое сообщение. Не повторяй send для одной
   и той же логической задачи без причины.
3. Для arbitrary !команд используй `krab_run_command.py --prefer-dm` —
   результат вернётся в Telegram, не в JSON.
4. Логи всех запусков: `tail -50 /tmp/krab_agent_tools.log`.

### 5. `krab_browser.py` (Wave 44-T-browser-profile)

Bash-вызываемый Chrome через Playwright. Подключается к running Chrome
по CDP (`http://127.0.0.1:9222`) — все логины pavua (Google, GitHub,
Telegram Web и т.д.) доступны. Если Chrome не запущен с
`--remote-debugging-port=9222` — fallback на изолированный профиль
`/tmp/krab_chrome_profile_isolated` (без логинов).

```bash
# Открыть страницу
venv/bin/python scripts/agent_tools/krab_browser.py open \
    --url https://github.com
# {"ok": true, "title": "GitHub", "final_url": "...", ...}

# Скриншот (валидируется через Wave 44-Q image_validator)
venv/bin/python scripts/agent_tools/krab_browser.py screenshot \
    --url https://example.com --output /tmp/x.png --full-page

# Извлечь текст
venv/bin/python scripts/agent_tools/krab_browser.py extract \
    --url https://news.ycombinator.com --selector ".titleline"

# Клик по элементу
venv/bin/python scripts/agent_tools/krab_browser.py click \
    --url https://example.com --selector "button.submit"

# Ввод в input + submit
venv/bin/python scripts/agent_tools/krab_browser.py type \
    --url https://google.com --selector "textarea[name=q]" \
    --text "Krab agent" --submit

# JS execution в DOM (XSS-risk: requires --owner-token)
venv/bin/python scripts/agent_tools/krab_browser.py js_run \
    --url https://example.com --js "document.title" \
    --owner-token "$(cat ~/.openclaw/krab_runtime_state/owner_confirm.token)"
```

**Hard-block hostlist** (HARD BLOCK при navigation):
- Banks: `paypal.com`, `chase.com`, `bbva.com`, `caixabank.es`,
  `revolut.com`, `wise.com`, `n26.com`, `santander.com`, ...
- Crypto exchanges: `binance.com`, `coinbase.com`, `kraken.com`, `bybit.com`, ...
- Government/tax: `*.gov`, `irs.gov`, `agenciatributaria*`

Override: `--allow-financial --owner-token <token>` (token из
`~/.openclaw/krab_runtime_state/owner_confirm.token`).

`js_run` всегда требует валидный `--owner-token`.

## Wave 45-C external API tools

| Скрипт | Назначение | Token env |
|--------|-----------|-----------|
| `krab_github.py` | GitHub repo/issue/pr/actions/release через `gh` CLI | `GITHUB_PERSONAL_ACCESS_TOKEN` (через gh) |
| `krab_cloudflare.py` | Cloudflare zones/dns/kv/workers (read-only) | `CLOUDFLARE_API_TOKEN` |
| `krab_sentry.py` | Sentry issues/events/resolve | `SENTRY_AUTH_TOKEN` (+ `SENTRY_ORG_SLUG`) |
| `krab_brave.py` | Brave Search top-N | `BRAVE_SEARCH_API_KEY` |

```bash
venv/bin/python scripts/agent_tools/krab_github.py issue list \
    --owner pavua --name Krab --limit 10
venv/bin/python scripts/agent_tools/krab_github.py pr create \
    --owner pavua --name Krab --title "T" --body "B" --head feat --base main

venv/bin/python scripts/agent_tools/krab_cloudflare.py zones list
venv/bin/python scripts/agent_tools/krab_cloudflare.py dns list --zone <zone-id>

venv/bin/python scripts/agent_tools/krab_sentry.py issues --project krab
venv/bin/python scripts/agent_tools/krab_sentry.py resolve --issue 12345

venv/bin/python scripts/agent_tools/krab_brave.py search --query "krab agent" --count 5
```

Все 4 скрипта: `0` — ok, `1` — ошибка/HTTP-fail, `2` — отсутствует token / `gh` CLI.
Никаких real API-вызовов в тестах (`tests/unit/test_agent_tools_wave45c.py`).

## Pyrogram session contention

Скрипты открывают `kraab.session` в `no_updates=True` режиме. Параллельный
запуск с running Krab userbot может вызвать SQLite lock race. Если
`{"ok": false, "error": "database is locked"}` — ретрай через 1-2 секунды.
