# Multi-account Codex setup для Krab

Документ описывает безопасный запуск разработки Krab на нескольких macOS-учётках (`pablito`, `USER2`, `USER3`) с разными ChatGPT Plus/OAuth аккаунтами.

## Главное правило

Общий перенос разрешён только для dev-layer:

- `~/.codex/skills`
- `~/.codex/plugins/cache`
- `~/.codex/vendor_imports`
- `~/.codex/AGENTS.md`
- переносимый `~/.codex/config.toml`

Нельзя копировать между учётками:

- `~/.codex/auth.json`
- OAuth/browser profiles
- Telegram session files
- `~/.openclaw`
- runtime locks, PID/socket/state

Причина: эти файлы account-local. Их копирование создаёт конфликты токенов, ломает MCP/OAuth и может запустить второй runtime поверх live owner.

## Подготовка USER2/USER3

Из нужной macOS-учётки запусти двойным кликом:

```text
/Users/Shared/Antigravity_AGENTS/Install Krab Codex Dev Layer.command
```

Installer берёт безопасный source snapshot из:

```text
/Users/Shared/Antigravity_AGENTS/codex_dev_layer_source
```

Альтернатива из repo:

```text
/Users/pablito/Antigravity_AGENTS/Краб/Prepare Next Account Session.command
```

После этого:

1. Открой Codex из этой учётки.
2. Выполни `codex login`, если текущий ChatGPT Plus аккаунт ещё не авторизован.
3. Запусти проверку:

```text
/Users/pablito/Antigravity_AGENTS/Краб/Check New Account Readiness.command
```

## Режимы

### `dev-tools`

Режим по умолчанию для `USER2`/`USER3`.

Можно:

- писать код;
- запускать unit tests;
- править docs;
- использовать skills/plugins/MCP;
- делать code review и анализ.

Нельзя:

- запускать второй live Krab runtime;
- писать в чужой `~/.openclaw`;
- переносить OAuth/session state;
- считать helper-account smoke финальным release verdict.

### `full`

Только для основной инженерной учётки или явно подготовленного helper-owner. Включает `krab-telegram` MCP и требует account-local Telegram session.

## MCP baseline

В переносимый `config.toml` входят:

- `chrome-devtools`
- `playwright`
- `openclaw-browser`
- `context7`
- `notion`
- `github-copilot`
- `linear`
- `supabase`
- `sentry`
- `figma`
- `gitlab`
- `huggingface-skills`
- `intercom`
- `slack`
- `vercel-mcp`
- `zapier`

`krab-telegram` и `krab-telegram-test` включаются только в `full` профиль.

## OAuth и права

Каждая учётка подтверждает OAuth сама. Это нормально и обязательно.

Уже настроено на `pablito`:

- Sentry MCP OAuth (`https://mcp.sentry.dev/mcp`)
- Linear OAuth
- Notion OAuth
- Context7 OAuth
- GitHub Copilot MCP через `GITHUB_PERSONAL_ACCESS_TOKEN`

Не добавлено по умолчанию:

- Planetscale MCP. Он запросил широкие write/delete scopes, поэтому его не включаем без конкретной задачи.
- Discord/Telegram Claude external plugins. Они требуют отдельный `bun` runtime и отдельную авторизацию; для Krab используется собственный `krab-telegram` MCP.
- Greptile. Нужен `GREPTILE_API_KEY`.

## Проверки

Проверить текущую учётку:

```text
Check New Account Readiness.command
```

Проверить, безопасно ли трогать runtime:

```text
Check Current Account Runtime.command
```

Проверить ветку и drift:

```text
Check Shared Repo Drift.command
```

## Конфликты

Перед параллельной работой:

1. Убедись, что все работают на понятной ветке.
2. Делай изменения в непересекающихся файлах.
3. Для live acceptance возвращайся на `pablito`, если helper-учётка не была явно назначена runtime owner.

Если возникли конфликты прав, не делай массовый `chown` всего репозитория. Сначала запусти drift/readiness check и фиксируй только конкретный путь.
