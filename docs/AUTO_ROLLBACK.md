# Auto-Rollback Watchdog

Safety net для production commits: откатывает последний commit если за 5 минут
после него в Sentry появился spike >10 новых issues.

**Default: DISABLED.** Включается явно через env.

## Как работает

Каждую минуту `launchd` вызывает `scripts/sentry_auto_rollback.sh`:

1. Если `KRAB_AUTO_ROLLBACK_ENABLED != 1` → exit 0 (тихо).
2. Проверка rate-limit (`/tmp/krab_rollback_last.ts`) — не чаще 1 revert/час.
3. Проверка последнего commit:
   - Возраст ≤ `MAX_COMMIT_AGE_MIN` (10 мин) — прошло окно опасности.
   - Не merge commit (parents > 1).
   - Нет `[skip-autorevert]` в message.
4. Запрос в Sentry API: кол-во новых issues за `WINDOW_MIN` (5 мин).
5. Если `count > THRESHOLD` (10):
   - Alert в Telegram owner (`TELEGRAM_OWNER_CHAT_ID`).
   - Ждём `ALERT_WAIT_SEC` (120 сек).
   - Если пользователь сделал `touch /tmp/krab_rollback_abort` — отмена.
   - Иначе: `git revert HEAD --no-edit` + `git push` + финальный TG.
   - Записываем `/tmp/krab_rollback_last.ts`.

## Env переменные

| Var | Default | Описание |
|-----|---------|----------|
| `KRAB_AUTO_ROLLBACK_ENABLED` | `0` | **Master switch.** 1 = active. |
| `KRAB_AUTO_ROLLBACK_THRESHOLD` | `10` | Trigger при >N новых issues. |
| `KRAB_AUTO_ROLLBACK_WINDOW_MIN` | `5` | Окно анализа Sentry (мин). |
| `KRAB_AUTO_ROLLBACK_MAX_COMMIT_AGE_MIN` | `10` | Не revert'ить старше этого. |
| `KRAB_AUTO_ROLLBACK_ALERT_WAIT_SEC` | `120` | Окно для owner-intervention. |
| `KRAB_AUTO_ROLLBACK_RATE_LIMIT_SEC` | `3600` | Min между revert'ами. |
| `SENTRY_AUTH_TOKEN` | — | Sentry API token (обязателен). |
| `SENTRY_ORG` / `SENTRY_PROJECT` | `krab`/`krab-userbot` | Sentry проект. |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_OWNER_CHAT_ID` | — | Для alerts. |

## Активация

```bash
# 1) В .env добавь:
echo 'KRAB_AUTO_ROLLBACK_ENABLED=1' >> ~/Antigravity_AGENTS/Краб/.env

# 2) Скопируй plist и загрузи:
cp ~/Antigravity_AGENTS/Краб/scripts/launchagents/ai.krab.auto-rollback-watchdog.plist \
   ~/Library/LaunchAgents/
launchctl load -w ~/Library/LaunchAgents/ai.krab.auto-rollback-watchdog.plist
```

## Отключение / отмена в процессе

```bash
# Отменить ongoing revert (во время 120s окна):
touch /tmp/krab_rollback_abort

# Полностью выключить:
launchctl unload ~/Library/LaunchAgents/ai.krab.auto-rollback-watchdog.plist
# ИЛИ в .env:
sed -i '' 's/KRAB_AUTO_ROLLBACK_ENABLED=1/KRAB_AUTO_ROLLBACK_ENABLED=0/' .env

# Пометить конкретный commit как "не откатывать":
git commit -m "hotfix thing [skip-autorevert]"
```

## Лог

`/tmp/krab_auto_rollback.log` — все проверки и действия.

## Тесты

```bash
bash scripts/test_auto_rollback.sh
```

Покрывает: spike → revert, low → noop, abort flag, rate limit,
`[skip-autorevert]`, disabled env.

## Known limitations

- **Sentry API полагается на `age:-Nm`.** Если часы host'а расходятся с
  Sentry серверами — window может быть неточным.
- **Revert делает новый commit (не force-push).** Хорошо для совместной
  работы, но history становится длиннее.
- **Не учитывает severity/type Sentry issues** — любые 10+ новых триггерят.
  Если нужна фильтрация, расширь `query` в скрипте.
- **Rate limit в файле `/tmp/`** — переживёт reboot только если `/tmp`
  не очищен системой (на macOS очищается при boot → после reboot revert
  может сработать снова).
- **Нет coverage для multi-branch** — работает на текущем HEAD текущей
  ветки. Если в CI несколько веток — нужна доп. логика.
- **Push без проверки CI.** Revert push идёт напрямую в origin/HEAD ветку.
