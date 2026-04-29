# VPN ↔ Krab Integration

Документ описывает контракт между Krab userbot и VPN-репо
(`/Users/pablito/Antigravity_AGENTS/VPN/`) после refactor `src/core/vpn_tools.py`
от 29.04.2026.

## Архитектура (before / after)

### Before (commit `cc19f7b`)

```
LLM ──► dispatch_vpn_tool("vpn_list_clients", …)
         │
         └─► VPNToolsAdapter._open_ro()
              └─► sqlite3.connect("file:.../x-ui.db?mode=ro")
                   └─► парсинг inbounds.settings + stream_settings
                        └─► _build_vless_link()  ◄── СВОЯ копия Reality-логики
```

Проблема: `_build_vless_link()` в Krab дублировал `vpn_bot.build_vless_link()`
из VPN-репо. Любое изменение Reality-параметров (publicKey location,
shortIds, fingerprint) требовало синхронизации в двух местах → drift-риск.

### After (this PR)

```
LLM ──► dispatch_vpn_tool("vpn_list_clients", …)
         │
         └─► VPNToolsAdapter._run_helper("list_clients.command")
              └─► subprocess.run(["…/VPN/list_clients.command"], …)
                   └─► [VPN-репо] vpn_bot.build_vless_link()  ◄── single source of truth
                        └─► JSON stdout → Krab
```

`vpn_list_clients` и `vpn_get_config` теперь — тонкие прокси к helper-скриптам
VPN-репо. Reality-логика живёт только в `vpn_bot.py`.

`vpn_panel_health` остался HTTP-probe со стороны Krab (это сетевая проверка,
не логика VPN). `vpn_traffic_stats` остался read-only sqlite read из
`client_traffics` — здесь Reality-параметры не задействованы, drift-риска нет,
а helper-скрипты этих данных не отдают.

## Helper-контракт

| Helper script | CLI | stdout (на успехе) | Используется |
|---|---|---|---|
| `list_clients.command` | без аргументов | `{"ok": true, "count": N, "clients": [...]}` | `vpn_list_clients` |
| `get_client_config.command` | `<email> --json` | `{"ok": true, "email", "vless_link", "port", "uuid", "flow", "inbound", "meta?"}` | `vpn_get_config` |

При ошибке оба пишут JSON `{"ok": false, "error": "..."}` в stdout
(некоторые случаи — exit code != 0; Krab учитывает оба сигнала).

## Конфигурация

`src/core/vpn_tools.py` поддерживает три env-переменных:

| ENV | Default | Назначение |
|---|---|---|
| `KRAB_VPN_HELPERS_DIR` | `/Users/pablito/Antigravity_AGENTS/VPN` | Каталог с `*.command` скриптами |
| `KRAB_VPN_DB_PATH` | `<HELPERS>/config/x-ui.db` | x-ui SQLite (для `vpn_traffic_stats`) |
| `KRAB_VPN_PANEL_URL` | `https://localhost:54321/` | URL панели для `vpn_panel_health` |
| `KRAB_VPN_TOOLS_ENABLED` | `1` | Включить регистрацию VPN-tools в MCP манифесте |

## KRAB_WEB_KEY setup (shared secret)

Для двунаправленной связи (когда VPN-бот пушит алёрты в Krab) обе стороны
должны разделять один и тот же `KRAB_WEB_KEY`.

### Step 1. Сгенерировать ключ (или использовать существующий)

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
# пример: vR3qKJzL5Tx9_aBcDeFgHiJkLmNoPqRsTuVwXyZ01234
```

### Step 2. Добавить в Krab `.env`

```bash
echo "KRAB_WEB_KEY=vR3qKJzL5Tx9_aBcDeFgHiJkLmNoPqRsTuVwXyZ01234" \
  >> /Users/pablito/Antigravity_AGENTS/Краб/.env
```

### Step 3. Добавить в VPN `alerts.env` (тот же самый ключ!)

```bash
echo "KRAB_WEB_KEY=vR3qKJzL5Tx9_aBcDeFgHiJkLmNoPqRsTuVwXyZ01234" \
  >> /Users/pablito/Antigravity_AGENTS/VPN/alerts.env
```

### Step 4. Перезапустить обе стороны

```bash
# Krab — каноническими лаунчерами:
"/Users/pablito/Antigravity_AGENTS/new Stop Krab.command"
sleep 5
"/Users/pablito/Antigravity_AGENTS/new start_krab.command"

# VPN bot — через launchd (имя plist подставить под реальное):
launchctl kickstart -k gui/$UID/ai.krab.vpn-bot
```

### Step 5. Verification

Проверить, что Krab видит helper-скрипты:

```bash
ls -la /Users/pablito/Antigravity_AGENTS/VPN/list_clients.command \
       /Users/pablito/Antigravity_AGENTS/VPN/get_client_config.command
```

Прогнать tool вручную через панель Krab:

```bash
curl -s -X POST http://127.0.0.1:8080/api/assistant/query \
  -H "Authorization: Bearer $KRAB_WEB_KEY" \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Покажи список VPN клиентов"}'
```

Или прямо на shell — то, что вызывает Krab внутри:

```bash
/Users/pablito/Antigravity_AGENTS/VPN/list_clients.command | python3 -m json.tool | head
/Users/pablito/Antigravity_AGENTS/VPN/get_client_config.command alice --json
```

## Setup checklist

- [ ] `ls -la /Users/pablito/Antigravity_AGENTS/VPN/list_clients.command get_client_config.command` — оба `-rwxr-xr-x`.
- [ ] `KRAB_WEB_KEY` одинаков в обоих `.env` файлах (Krab и VPN).
- [ ] `KRAB_VPN_HELPERS_DIR` либо не задан (default подходит), либо указывает на VPN-репо.
- [ ] Krab перезапущен через `new start_krab.command` (не через `Restart Krab.command`).
- [ ] VPN-бот перезапущен через `launchctl kickstart -k`.
- [ ] `/Users/pablito/Antigravity_AGENTS/VPN/list_clients.command` отдаёт valid JSON.
- [ ] `pytest tests/unit/test_vpn_tools.py -q` — 6 passed.

## Тесты

`tests/unit/test_vpn_tools.py` (6 кейсов):

1. `test_list_clients_basic` — мокаем `subprocess.run`, проверяем фильтрацию
   `enabled` и `include_disabled`, проверяем переданный путь скрипта + timeout.
2. `test_get_config_existing_and_missing` — happy path, `not_found` через
   helper output, empty client name.
3. `test_panel_health_mocked` — `urllib.urlopen` mock с 401 → ok=True.
4. `test_traffic_stats_compute_percent` — sqlite фикстура, percent_used,
   безлимит (total=0), missing.
5. `test_missing_helpers_graceful` — нет каталога, нет скриптов,
   `subprocess.TimeoutExpired` — все три ветки graceful.
6. `test_manifest_entries_valid` — sanity по schema/dispatcher.

Запуск:

```bash
cd /Users/pablito/Antigravity_AGENTS/Краб
venv/bin/python -m pytest tests/unit/test_vpn_tools.py -q
```
