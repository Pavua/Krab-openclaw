# Codex-CLI Sandbox Config (Wave 44-R-codex)

**Дата:** 09.05.2026
**Файл:** `~/.codex/config.toml` (личный, НЕ в репо)
**Backup:** `~/.codex/config.toml.bak.wave44r`

## Текущий режим

```toml
approval_policy = "never"        # без human-in-loop подтверждений
sandbox_mode    = "workspace-write"

[sandbox_workspace_write]
network_access  = true
writable_roots  = [
  "/tmp",
  "/Users/pablito/.openclaw",
  "/Users/pablito/Antigravity_AGENTS",
]
```

Verified live (codex 0.125.0):

```
sandbox: workspace-write [workdir, /tmp, $TMPDIR, /tmp,
  /Users/pablito/.openclaw, /Users/pablito/Antigravity_AGENTS,
  /Users/pablito/.codex/memories] (network access enabled)
```

## Почему `workspace-write`, а не `danger-full-access`

Доступные режимы (`codex --help`):

| mode                  | read | write             | network |
|-----------------------|------|-------------------|---------|
| `read-only` (def)     | all  | none              | none    |
| `workspace-write`     | all  | cwd + writable_roots | optional |
| `danger-full-access`  | all  | all               | all     |

**`danger-full-access` отвергнут** из-за инцидента Wave 9-B (02.05.2026):
codex-cli/gpt-5.5 галлюцинировал tool call `telegram_send_message`. Sole Telegram outbound channel — main Krab userbot. Песочница остаётся как defence-in-depth даже если модель снова что-то выдумает.

**`workspace-write`** — золотая середина:
- Запись разрешена в `cwd` (рабочий каталог при запуске Krab agent run) + явные whitelisted roots.
- Чтение всей системы — без ограничений, что нужно для скриншотов / диагностики.
- `network_access = true` снимает блок с localhost (gateway 18789, owner 8080, MCP 8011-8013, LM Studio 1234, x-ui) и outbound (npm/pip install, web search, Vertex AI direct).

## Что разрешено / запрещено

**Разрешено:**
- Чтение любых файлов системы (для скриншотов, AppleScript, диагностики).
- Запись в проект `/Users/pablito/Antigravity_AGENTS/` (включая Краб, Voice Gateway, Krab Ear).
- Запись в `~/.openclaw/` (workspace, agent state, models.json).
- Запись в `/tmp` и `$TMPDIR` (временные артефакты, скриншоты Playwright).
- Сетевые соединения: localhost-сервисы, npm registry, Vertex AI, OpenRouter, Brave Search.
- `osascript` / macOS automation (через subprocess; sandbox не блокирует exec).

**Запрещено:**
- Запись в `~/Library`, `~/Documents`, любые home-папки кроме whitelisted.
- Изменение `~/.codex/config.toml` самим codex-агентом (ironic safety net).
- Системные пути (`/etc`, `/usr`, `/Library`) — read-only.

## Откат при проблемах

```bash
cp ~/.codex/config.toml.bak.wave44r ~/.codex/config.toml
```

После отката сразу проверить:

```bash
codex --version  # 0.125.0 ожидается
codex exec --skip-git-repo-check "echo test" 2>&1 | head -5
```

## Smoke test (выполнено 09.05.2026)

```bash
$ cd /tmp && codex exec --skip-git-repo-check "Print just OK"
sandbox: workspace-write (network access enabled)
OK
tokens used 20,163
```

✅ Config парсится, sandbox активен, output корректен.

## Связь с cli_runner.py

`src/integrations/cli_runner.py` вызывает `codex -q <prompt>` без `--sandbox` flag → используются дефолты из `config.toml` → теперь `workspace-write`. Никаких изменений в коде Krab НЕ требуется.

## История изменений

- **Wave 44-R-codex (09.05.2026)** — initial. До этого `sandbox_mode` не был задан, codex использовал hardcoded default (read-only).
- **Wave 9-B (02.05.2026, см. config.toml)** — отключены telegram MCP servers (causality для текущего решения держать sandbox строгим).
