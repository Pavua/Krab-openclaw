# OpenClaw God Mode, Gemini Billing и MCP — итоги сессии 02.04.2026

## Цели сессии

1. Активировать God Mode для Краба (полный exec без подтверждений)
2. Починить "Провайдер временно недоступен" от Gemini
3. Убедиться что MCP серверы не отваливаются после перезапуска

---

## 1. God Mode — что было не так и как починили

### Слоёная проблема (три независимых блокировки)

**Блокировка 1:** Невалидный ключ `tools.exec.allowlist` в `openclaw.json`
- OpenClaw 2026.4.1 не знает этот ключ → `openclaw gateway restart` падал
- Убрали `allowlist` из `tools.exec`

**Блокировка 2:** `approvals.exec.enabled: true`
- Separate от `ask: "off"` — отдельное поле для approval dialog
- Выставили `approvals.exec.enabled: false`

**Блокировка 3 (корневая):** `agents.list[main].tools.profile: "messaging"`
- Профиль `messaging` не включает exec-инструмент вообще
- Даже с `security: "full"` на глобальном уровне — exec не в allowed list агента
- **Аналог:** sudo разрешён на системе, но конкретному user не выдан
- **Исправлено:** `profile: "full"` для main агента

### Дополнительно:
- `tools.exec.host: "gateway"` — убрали sandbox (sandbox блокировал перенаправления `>`, `|` в shell)
- `thinking: "low"` вместо `"medium"` — medium сжигал RPM квоту во время agentic runs, вызывая 429

### Текущее состояние `~/.openclaw/openclaw.json`:

```json
"tools": {
  "exec": {
    "host": "gateway",
    "security": "full",
    "ask": "off"
  }
},
"approvals": {
  "exec": {
    "enabled": false
  }
},
"agents": {
  "list": [{
    "id": "main",
    "tools": {
      "profile": "full",
      "deny": ["sessions_send", "sessions_spawn"]
    }
  }]
}
```

**Тест пройден:** Краб выполнил `id && echo krab_god_mode_active > /tmp/krab_god.txt && cat /tmp/krab_god.txt` без блокировок.

---

## 2. Провайдер временно недоступен — разбор

### Причина 1: Все фоллбэки на одном OAuth

Старая цепочка была `google-gemini-cli/*` — все модели используют одну OAuth-сессию. Когда сессия истекает, вся цепочка падает одновременно.

### Причина 2: Preview модели + thinking:medium = 429

`gemini-3.1-pro-preview` имеет очень низкий RPM даже с paid billing. `thinking: "medium"` умножает token usage на каждый вызов. Agentic run (5-10 calls) быстро сжигает квоту.

### Решение — новая цепочка через API ключ:

```json
"model": {
  "primary": "google/gemini-2.5-flash",
  "fallbacks": [
    "google/gemini-2.5-pro",
    "google/gemini-3.1-pro-preview",
    "google/gemini-3-flash-preview"
  ]
}
```

`gpt-4o-mini` убран (нет баланса на OpenAI ключе).

---

## 3. Billing Gemini — итог проверки

**Проверено в Google Cloud Console:**

- Billing аккаунт `0108D2-B58B7A-206801` с бонусом €228.80 (до мая 2026)
- Привязанные проекты: `krab-488216` и `gen-lang-client-0306839113`
- API ключ `AIzaSyA07...LhPUKY` (он же `GEMINI_API_KEY_FREE` в .env) — из проекта `gen-lang-client`
- Квоты: **paid tier активен** — показано `Request limit per model per minute for a project in the paid tier 1: 1000 RPM`
- Использование gemini-3-flash за сессию: 201K токенов (реально списываются)

**$0 в billing показывало потому что:**
- Preview модели (`gemini-3.1-pro-preview`, `gemini-3-flash-preview`) — **бесплатные**, $0 всегда
- Billing dashboard обновляется с задержкой ~24 часа

**После переключения на `gemini-2.5-flash` как primary** — баланс начнёт расходоваться.

---

## 4. MCP серверы — статус и бэкап

### Конфиг `~/Library/Application Support/Claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "krab-yung-nagato": {
      "command": "/Users/pablito/Antigravity_AGENTS/Краб/.venv/bin/python",
      "args": [
        "/Users/pablito/Antigravity_AGENTS/Краб/mcp-servers/telegram/server.py",
        "--transport", "stdio"
      ]
    },
    "krab-p0lrd": {
      "command": "/Users/pablito/Antigravity_AGENTS/Краб/.venv/bin/python",
      "args": [
        "/Users/pablito/Antigravity_AGENTS/Краб/scripts/run_telegram_mcp_account.py",
        "--session-name", "p0lrd_cc",
        "--transport", "stdio"
      ]
    }
  }
}
```

**Бэкап:** `docs/mcp_config_backup.json` (актуальная копия конфига)

### Проверки

- `mcp-servers/telegram/server.py` — запускается без ошибок ✅
- `scripts/run_telegram_mcp_account.py` — коннектится к Telegram, сессия `p0lrd_cc_mcp.session` валидна ✅
- Ничто в коде Краба не перезаписывает `claude_desktop_config.json` ✅
- SQLite lock при restart: обрабатывается в `telegram_bridge.py` через `_restart_client_locked()` ✅

### Быстрое восстановление если MCP отвалились:
```bash
cp /Users/pablito/Antigravity_AGENTS/Краб/docs/mcp_config_backup.json \
   ~/Library/Application\ Support/Claude/claude_desktop_config.json
# Перезапустить Claude Desktop
```

---

## Итого — текущее состояние системы

| Параметр | Значение |
|----------|---------|
| Primary модель | `google/gemini-2.5-flash` (платная, RPM 1000) |
| Exec profile | `full` (все инструменты) |
| Exec host | `gateway` (без sandbox) |
| Approvals | отключены |
| Thinking | `low` (быстро, не сжигает квоту) |
| Billing | активен, paid tier, бонус €228 до мая 2026 |
| MCP серверы | 2 шт, оба работают |

---

## Дополнение 03.04.2026 — что оказалось реальной причиной повторного `allowlist miss`

После перехода на OpenClaw `2026.4.2` выяснилось, что предыдущая модель "достаточно починить только `openclaw.json`" уже неполная.

### Новый source of truth для host exec

Начиная с `2026.4.2`, итоговая exec-политика собирается из двух слоёв:

1. `~/.openclaw/openclaw.json`
2. `~/.openclaw/exec-approvals.json`

`openclaw approvals get` прямо показывает это в effective policy:

- requested: `tools.exec.security=full`
- host: `defaults.security=allowlist`
- effective: `security=allowlist`

То есть даже при корректном:

```json
"tools": {
  "exec": {
    "host": "gateway",
    "security": "full",
    "ask": "off"
  }
}
```

агент всё равно может ловить `exec denied: allowlist miss`, если wildcard не прописан в `exec-approvals.json`.

### Что теперь считается полноценным God Mode

#### `~/.openclaw/openclaw.json`

```json
"tools": {
  "exec": {
    "host": "gateway",
    "security": "full",
    "ask": "off",
    "notifyOnExit": true,
    "notifyOnExitEmptySuccess": true
  }
},
"approvals": {
  "exec": {
    "enabled": false
  }
},
"agents": {
  "list": [{
    "id": "main",
    "tools": {
      "profile": "full",
      "deny": ["sessions_send", "sessions_spawn"]
    }
  }]
}
```

#### `~/.openclaw/exec-approvals.json`

Нужен wildcard минимум в одном из двух мест, лучше в обоих:

- `agents.main.allowlist += "*"`
- `agents["*"].allowlist += "*"`

### Что сделано в launcher-слое

Чтобы God Mode не слетал после обычного one-click старта:

- добавлен helper `scripts/openclaw_god_mode_sync.py`
- `new start_krab.command` теперь после `doctor --fix` синхронизирует оба файла
- если sync реально поменял exec policy, launcher делает controlled restart gateway

### Дополнительный runtime-хвост, который тоже пришлось чинить

В `foreground/session` режиме gateway не находится под `launchd KeepAlive`.
После config-reload или внешнего `SIGTERM` он мог умереть навсегда, и Краб оставался без `:18789`, хотя сам userbot ещё жил.

Исправление:

- в `new start_krab.command` добавлен `OpenClaw Gateway Watchdog`
- watchdog поднимает gateway обратно, если тот умер в foreground-режиме

### Живая проверка 03.04.2026

- `pytest -q tests/unit/test_openclaw_god_mode_sync.py` → `3 passed`
- controlled restart через:
  - `/Users/pablito/Antigravity_AGENTS/new Stop Krab.command`
  - `/Users/pablito/Antigravity_AGENTS/new start_krab.command`
- kill-test:
  - слушающий PID `18789` был убит вручную
  - watchdog поднял новый PID
  - `/health` снова вернул `{"ok":true,"status":"live"}`

### Важная развязка по Apple Notes

История с `Operation not permitted` для файлов из Apple Notes — это отдельный слой `macOS TCC / Full Disk Access`, а не симптом поломанного God Mode в OpenClaw.

Итог:

- `allowlist miss` = проблема host approvals / OpenClaw exec
- `Operation not permitted` на Notes = проблема macOS privacy/TCC

Их нельзя больше смешивать в отладке.
