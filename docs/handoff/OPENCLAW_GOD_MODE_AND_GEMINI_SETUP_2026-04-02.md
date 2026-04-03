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

## Дополнение 03.04.2026 — почему approval modal всё равно всплывал в Control UI

После фикса wildcard allowlist выяснилась ещё одна ловушка OpenClaw `2026.4.2`.

### Симптом

Даже при уже корректных:

- `tools.exec.security = "full"`
- `tools.exec.ask = "off"`
- `approvals.exec.enabled = false`
- wildcard `*` в `~/.openclaw/exec-approvals.json`

в dashboard OpenClaw иногда всё равно всплывал modal:

- `Security: allowlist`
- `Ask: on-miss`
- `Exec approval needed`

и пользователю снова приходилось жать `Always allow`.

### Что оказалось правдой

1. Надпись `Security: allowlist` сама по себе в `2026.4.2` нормальна.
   Host approvals слой остаётся stricter, а unrestricted поведение достигается через wildcard allowlist.

2. Реальная проблема была не в file access и не в `openclaw.json`.
   `openclaw approvals get` показывал корректный effective policy:

   - `security=allowlist`
   - `ask=off`

3. Корневой баг сидел в разрыве между on-disk approval-store и live gateway apply.

### Реальная причина

`scripts/openclaw_god_mode_sync.py` раньше только переписывал:

- `~/.openclaw/openclaw.json`
- `~/.openclaw/exec-approvals.json`

но не отправлял approval-store в live gateway отдельным штатным вызовом.

Когда была предпринята такая отправка через:

```bash
openclaw approvals set --gateway --file ~/.openclaw/exec-approvals.json --json
```

выяснилось, что gateway upload API отвергает локальный JSON из-за лишнего поля:

- `source`

Ошибка была такой:

```text
invalid exec.approvals.set params:
at /file/agents/main/allowlist/0: unexpected property 'source';
at /file/agents/*/allowlist/0: unexpected property 'source'
```

То есть:

- локальный файл на диске жил и использовался;
- но live gateway apply падал на валидации;
- из-за этого host/session слой мог остаться stale и продолжать заводить approval modal в UI.

### Что исправлено

В `scripts/openclaw_god_mode_sync.py` добавлен второй этап:

1. читается `exec-approvals.json`;
2. строится sanitизированный временный payload для gateway upload;
3. из allowlist-записей убираются поля, которые gateway schema не принимает
   (на практике ключевой offender — `source`);
4. вызывается:

```bash
openclaw approvals set --gateway --file <temp-json> --json
```

Теперь sync не только чинит файлы на диске, но и принудительно приводит live host approvals
в то же состояние без ручного клика в dashboard.

### Живая проверка 03.04.2026 (вторая волна)

- `python -m py_compile scripts/openclaw_god_mode_sync.py tests/unit/test_openclaw_god_mode_sync.py` → OK
- `pytest -q tests/unit/test_openclaw_god_mode_sync.py` → `6 passed`
- ручной прогон:

```bash
/Users/pablito/Antigravity_AGENTS/Краб/venv/bin/python \
  /Users/pablito/Antigravity_AGENTS/Краб/scripts/openclaw_god_mode_sync.py \
  --openclaw-bin /opt/homebrew/bin/openclaw
```

дал:

- `gateway_apply.attempted = true`
- `gateway_apply.applied = true`

- повторный `openclaw approvals get` показал:
  - allowlist только из двух wildcard записей (`main` и `*`)
  - `Effective Policy -> ask=off`

### Практический вывод для следующего чата / другой учётки

Если пользователь снова увидит approval modal в OpenClaw Control UI:

1. сначала не винить Full Disk Access и не винить workspace;
2. проверить `openclaw approvals get`;
3. прогнать `scripts/openclaw_god_mode_sync.py`;
4. убедиться, что `gateway_apply.applied = true`;
5. только если modal остаётся после этого, считать проблему уже session/UI drift, а не god-mode sync drift.

## Дополнение 03.04.2026 — подтверждённый регресс `Always allow` в Control UI

После live-fix approval-store остался ещё один отдельный баг, уже вне слоя Краба.

### Что удалось доказать

#### 1. Gateway policy на самом деле корректная

Проверка:

```bash
openclaw approvals get --gateway --json
```

показывает:

- `effectivePolicy.scopes[0].ask.effective = "off"`
- `allowedDecisions = ["allow-once", "allow-always", "deny"]`
- wildcard `*` действительно присутствует для `main` и `*`

То есть host/gateway слой сам говорит, что `allow-always` допустим.

#### 2. Но modal Control UI врёт о live policy

В реальной webchat-session `agent:main:openai:aa0097ca-4624-4179-bbfd-ad3343c79b82`
в dashboard всплыл modal с такими признаками:

- `Security: allowlist`
- `Ask: always`
- красная ошибка:
  `allow-always is unavailable because the effective policy requires approval every time`

Это противоречит живому `openclaw approvals get --gateway --json`.

#### 3. `Allow once` работает, `Always allow` — нет

Поведение подтверждено live:

- нажатие `Allow once` реально пропускает конкретный exec;
- задача продолжает выполняться;
- Краб доходит до следующей инженерной ошибки внутри `diarize_test.py`;
- нажатие `Always allow` не закрепляет разрешение и в ряде случаев вообще отвергается modal'ом.

### Итоговый диагноз

На текущий момент это **не**:

- проблема Full Disk Access;
- проблема workspace-гранцы;
- проблема отсутствующего wildcard allowlist;
- проблема `openclaw.json`.

Это **отдельный регресс OpenClaw Control UI / session approval semantics**:

- CLI/gateway host policy и approval modal в webchat видят разные effective decisions;
- persistent approval path (`allow-always`) сломан;
- transient approval path (`allow-once`) остаётся рабочим.

### Рабочий обход на сейчас

Пока upstream-баг не исправлен, для owner-задач в Control UI использовать такой порядок:

1. если modal всплыл, не тратить время на `Always allow`;
2. жать `Allow once`;
3. доводить текущую задачу до конца;
4. считать повторное всплытие modal уже известным багом OpenClaw, а не регрессом настроек Краба.

### Что важно помнить в следующем чате

Если новый агент увидит approval modal, а `openclaw approvals get --gateway --json`
по-прежнему показывает:

- `ask=off`
- `allowedDecisions` включает `allow-always`

то нужно сразу квалифицировать это как **UI/session bug**, а не снова пересобирать
`openclaw.json`, `exec-approvals.json`, FDA или file access.
  - `/health` снова вернул `{"ok":true,"status":"live"}`

## Дополнение 03.04.2026 — repo-side hotfix против per-call `ask=always`

После разбора установленного OpenClaw dist выяснилось, что approval modal может всплывать
ещё глубже:

- global policy уже даёт `tools.exec.ask = "off"`;
- `openclaw approvals get --gateway --json` это подтверждает;
- но сам `exec` runtime всё равно считает:

```js
let ask = maxAsk(configuredAsk, normalizeExecAsk(params.ask) ?? configuredAsk);
```

Из-за этого любой per-call `params.ask=always` снова переэскалирует approvals, даже когда
owner уже намеренно выключил их глобально.

### Что это означает practically

Это уже не только UI drift, а ещё и runtime-gap:

- host truth = `ask=off`;
- но конкретный tool call может притащить `ask=always`;
- результатом снова становится approval modal.

### Что добавлено в репозиторий

- patcher: [reapply_openclaw_exec_ask_off_hotfix.py](/Users/pablito/Antigravity_AGENTS/Краб/scripts/reapply_openclaw_exec_ask_off_hotfix.py)
- one-click launcher: [Apply OpenClaw Exec Ask Off Hotfix.command](/Users/pablito/Antigravity_AGENTS/Краб/Apply%20OpenClaw%20Exec%20Ask%20Off%20Hotfix.command)

### Что делает hotfix

Патч меняет runtime-логику так, чтобы при явном глобальном:

- `tools.exec.ask = "off"`

per-call `params.ask` больше не мог обратно включать approvals.

Новая логика:

```js
const requestedAsk = normalizeExecAsk(params.ask);
let ask = configuredAsk === "off" ? "off" : maxAsk(configuredAsk, requestedAsk ?? configuredAsk);
```

### Truthful scope

- это локальный workaround поверх upstream OpenClaw;
- после обновления OpenClaw dist hotfix нужно пере-применять;
- после применения нужен restart gateway.

## Когда менять диалог или учётку

### Оставаться в этом диалоге

Оставайся здесь, пока:

- нужно добить текущую задачу и есть свежий handoff-контекст;
- проблема уже локализована до одного слоя, например `exec approvals`, `ask=always` или `Control UI`;
- мы только что подтвердили live fix и нужен короткий ретест без потери истории.

### Открывать новый диалог

Новый чат лучше открыть, если:

- текущий диалог упирается в квоту или разрастается длиннее, чем полезно для новой итерации;
- нужно проверить уже исправленный state без старого шума;
- после фикса хочешь провести чистый live retest без старых approval modal / stale session артефактов.

### Менять учётку

На другую учётку имеет смысл переходить только если:

- проблема явно account-local, а не repo/runtime-level;
- `~/.openclaw`, session store или runtime ownership жёстко привязаны к конкретному macOS user;
- нужен независимый контур проверки, а не просто новый chat.

### Практическое правило

Для текущего кейса:

- сначала добивай задачу в текущем `Control UI` через `Allow once`, если modal ещё всплывает;
- после этого для чистой проверки открывай новый диалог;
- если на этой же macOS-учётке продолжаешь через Claude, тоже начинай с нового диалога и этого же handoff;
- учётку Codex меняй только если в новом диалоге проблема повторится уже без старого session drift или если нужен независимый quota-контур.

### Важная развязка по Apple Notes

История с `Operation not permitted` для файлов из Apple Notes — это отдельный слой `macOS TCC / Full Disk Access`, а не симптом поломанного God Mode в OpenClaw.

Итог:

- `allowlist miss` = проблема host approvals / OpenClaw exec
- `Operation not permitted` на Notes = проблема macOS privacy/TCC

Их нельзя больше смешивать в отладке.

---

## Дополнение 03.04.2026 — финальный фикс approval modal (exec-approvals.json agents.ask)

### Проблема, которая оставалась после всех предыдущих фиксов

Даже с правильным `openclaw.json`, wildcard allowlist и hotfix строки 3623 — approval modal
в Control UI продолжал появляться при каждом новом exec-вызове.

### Корневая причина

В OpenClaw есть **два независимых кода пути**:

```
Строка 2108 dist (решение о показе модала UI):
  hostAsk = approvals.agent.ask === "off" ? "off" : maxAsk(params.ask, approvals.agent.ask)
                                               ↑ без ask в exec-approvals.json → undefined
  → maxAsk("always", undefined) = "always" → модал показывается

Строка 3623 dist (runtime выполнение exec):
  ask = configuredAsk === "off" ? "off" : ...   ← hotfix уже патчил это
```

То есть: hotfix на строке 3623 не давал exec быть заблокированным, но строка 2108 всё равно
показывала модал, потому что `exec-approvals.json` не имел `ask: "off"` в секции agents.

### Решение

Добавить `ask: "off"` в три места `~/.openclaw/exec-approvals.json`:

```json
{
  "defaults": {
    "security": "allowlist",
    "autoAllowSkills": true,
    "ask": "off"
  },
  "agents": {
    "main": {
      "ask": "off",
      "allowlist": [{ "pattern": "*", ... }]
    },
    "*": {
      "ask": "off",
      "allowlist": [{ "pattern": "*", ... }]
    }
  }
}
```

Применить к live gateway через node напрямую (openclaw CLI не работает через shebang в некоторых окружениях):

```bash
/opt/homebrew/bin/node /opt/homebrew/lib/node_modules/openclaw/openclaw.mjs \
  approvals set --gateway --file <sanitized-temp.json> --json
```

### Подтверждение работы

После применения `openclaw approvals get --gateway --json` показывает:

```json
"ask": {
  "requested": "off",
  "host": "off",
  "hostSource": "exec-approvals.json agents.main.ask",
  "effective": "off"
}
```

Approval modal больше не появляется ни для каких exec-вызовов включая `ask=always` из per-call params.

### Обновления в коде

`scripts/openclaw_god_mode_sync.py` обновлён:
1. `sync_exec_approvals()` теперь гарантирует `ask: "off"` в `defaults` и каждом агенте
2. `apply_exec_approvals_to_gateway()` теперь использует `node + openclaw.mjs` как fallback
   когда `#!/usr/bin/env node` shebang не находит node в ограниченном PATH launcher-среды

Тесты: `pytest tests/unit/test_openclaw_god_mode_sync.py` → `6 passed`

---

## Дополнение 03.04.2026 — фикс MCP SQLite kill-loop (Desktop vs Claude Code конфликт)

### Причина постоянных MCP-отвалов

Claude Desktop и Claude Code используют **разные конфиги** MCP, но ранее оба указывали
на один и тот же session-файл `p0lrd_cc_mcp.session`:

- Desktop (`claude_desktop_config.json`): `--session-name p0lrd_cc` → `p0lrd_cc_mcp.session`
- Code (`~/.claude.json`): `--session-name p0lrd_cc` → тот же файл

Встроенный `_release_stale_session_lock` убивал процесс другого клиента → kill-loop без остановки.

### Решение

1. Скопировали `p0lrd_cc_mcp.session` → `p0lrd_desktop_mcp.session` (авторизация переносится)
2. Обновили Desktop конфиг на `--session-name p0lrd_desktop` + `.venv` вместо `venv`

Итоговое разделение:
| Клиент | Нагато | p0lrd |
|--------|--------|-------|
| Claude Desktop (Chat) | `kraab_mcp.session` | `p0lrd_desktop_mcp.session` |
| Claude Code | `kraab_cc_mcp.session` | `p0lrd_cc_mcp.session` |

Бэкап Desktop конфига: `claude_desktop_config.json.bak_20260403`
