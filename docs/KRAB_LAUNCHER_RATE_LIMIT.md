# Krab Launcher Cold-Start Rate Limit

## Зачем

В Session 26 был зафиксирован инцидент: **322 fatal_error events за 24 часа**.
Root cause — corrupt `archive.db` ломал bootstrap, launchd с `KeepAlive=true`
немедленно перезапускал процесс, и так циклически 322 раза, пока пользователь
вручную не остановил Krab.

DB-corruption guard в bootstrap (commit `9d44e50`) уже добавляет первый слой
защиты: при detection corrupt DB вызывается `sys.exit(78)` — launchd throttle
exit code, который пресекает дальнейший respawn. Этот документ описывает
**второй слой** (belts-and-suspenders) — guard на уровне launcher-скрипта.

## Где живёт

Launcher (`/Users/pablito/Antigravity_AGENTS/new start_krab.command`) — вне
git-tree (локальный, не tracked). Источник истины — он же.

Канонический референс копии:
- `scripts/launchers/cold_start_rate_limit.sh` — extracted function для audit.
- `docs/KRAB_LAUNCHER_RATE_LIMIT.md` — этот документ.

При рефакторинге launcher'а функция должна остаться синхронизированной
с этой репо-копией.

## Поведение

При каждом запуске Krab:

1. Append `date +%s` в `~/.openclaw/krab_runtime_state/krab_cold_starts.log`.
2. Rotation: если файл >100 строк — оставить последние 50.
3. Считать timestamps в окне **last 300s** (5 минут).
4. Если count ≥ **10** — `ABORT` (exit 1, Krab не запускается).
5. Если count ≥ **5** — `COOLDOWN` 10 минут (`sleep 600`).
6. Иначе — нормальный запуск.

## Параметры

| Параметр | Значение | Смысл |
|---|---|---|
| `threshold_pause` | 5 | ≥5 starts/5min → cooldown |
| `threshold_abort` | 10 | ≥10 starts/5min → abort |
| `window_sec` | 300 | sliding window |
| Cooldown duration | 600 | sleep при pause threshold |
| Log rotation | >100 → keep 50 | предотвращает рост лога |

## Override

Если user уверен что restart-loop ложный (e.g. интенсивная разработка с многими
ручными restart'ами), можно:

```bash
rm ~/.openclaw/krab_runtime_state/krab_cold_starts.log
```

Лог пересоздастся при следующем запуске.

## Операционные сценарии

### Нормальный dev-loop
1-3 restart'а в час → counter в norm, нулевой эффект.

### Подозрительный manual loop
4-9 restarts за 5 минут → cooldown 10 минут. User видит warning,
может Ctrl+C и решить вручную.

### Реальный launchd respawn loop
≥10 restarts за 5 минут (launchd respawn'ит каждые ~10s после crash) →
ABORT. User получает clear message, видит что-то не так, идёт в Sentry/логи.

## Тесты

Проверены вручную через `/tmp/test_cold_start.sh`:
- empty log → normal start
- 6 recent ts → COOLDOWN triggered
- 12 recent ts → ABORT triggered
- 150-line log → rotated to 50
- old timestamps (>5min) → ignored

(Тесты не trigger реальный 600s sleep — отдельный helper script подменяет
функцию `sleep` на `echo` для проверки логики.)

## Связанные коммиты

- Session 26 root-cause: 322 fatal_error events
- DB corruption guard: `9d44e50` (bootstrap-level, exit 78)
- Этот guard: launcher-level, добавлен после Session 26 retrospective

## TODO

- [ ] Optional: при ABORT — отправить notify в Telegram через reserve_bot.
  Сейчас только stdout (видно если launcher запущен интерактивно).
- [ ] Optional: интегрировать с Sentry alert (но Sentry уже polled
  через `ai.krab.sentry-poll` LaunchAgent — fatal_error spike сам поднимет alert).
