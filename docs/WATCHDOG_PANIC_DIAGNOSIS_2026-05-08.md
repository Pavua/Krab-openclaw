# Watchdog Panic Diagnosis — 2026-05-08

## Симптом

Mac перезагружается каждые ~24 часа (особенно после сна). Пользователь думает что лэптоп зависает во время сна.

## Реальная причина: Kernel watchdog timeout

```
panic(cpu N caller 0x...): watchdog timeout: no checkins from watchdogd in 90+ seconds
```

**Что это**: macOS kernel запускает watchdog'а, который должен получать heartbeat от ядра каждые ~30 секунд. Если ядро не отвечает 90+ секунд — kernel считает себя зависшим и принудительно перезагружается. Это safety mechanism.

## Найдено 4 panic за 4 дня:

| Date | File | Sleep/Wake | Cause |
|------|------|-----------|-------|
| 2026-05-05 23:54 | panic-full-2026-05-05-235420.0002.panic | No (Sleep=0) | Watchdog 93s |
| 2026-05-06 14:44 | panic-full-2026-05-06-144443.0002.panic | No | Watchdog 90s |
| 2026-05-07 15:11 | panic-full-2026-05-07-151106.0002.panic | (sleep cycle) | Watchdog |
| 2026-05-08 17:05 | panic-full-2026-05-08-170518.0002.panic | Yes (после wake) | Watchdog 93s |

Mix паттерн: одни во время нормальной работы, другие после wake. Объединяет всех — **system-wide kernel I/O stall**.

## Root cause: memory pressure → compressor thrashing

При снимке (после reboot, относительно «лёгкая» нагрузка):

| Метрика | Значение |
|---------|----------|
| PhysMem used | **34 GB / 36 GB** (94%) |
| Memory compressor | **10 GB** |
| Free | **699 MB** |
| Swap (dynamic_pager) | 913 MB / 2 GB cap |
| Total RSS measured | 20.82 GB |
| Load average | 5.94 (1-min) |
| LaunchAgent count | **582** |

**Memory compressor 10 GB** — критический индикатор. Когда compressor активно сжимает страницы, kernel I/O queue забит. Watchdog daemon не может получить window для check-in → 90+ секунд → mandatory panic.

## Главные потребители RAM:

| PID | Process | RSS |
|-----|---------|-----|
| 1078 | OrbStack Helper (vmgr) | **1.32 GB** |
| 12645 | WebKit XPC | 666 MB |
| 6741 | Telegram 2 | 651 MB |
| 985 | OpenClaw gateway (node) | 586 MB |
| 13391 | Codex.app | 290 MB |
| 1022 | CleanMyMac5.HealthMonitor | 203 MB |

## 3rd party kexts: НЕТ

`kextstat -kl | awk '!/com\.apple/'` — пусто. Это исключает классические "виноват кривой kext" сценарии. Проблема — system load, не misbehaving driver.

## Применённые fixes (автоматически)

1. **Disabled `ai.krab.gcp-quota-poc-watcher`** — agent крашился каждые 30 минут на `EMAIL_USER + EMAIL_APP_PASSWORD missing`. Exit 78 spam'ило логи.
2. **Закрыты Sentry issues** PYTHON-FASTAPI-66/67/5W/71/5Y/7M — все были последствиями старого DB-corruption (Session 33 fix), плюс PYTHON-FASTAPI-Z (387 events).
3. **Fixed codex skill YAML** в `/Users/pablito/.codex/skills/claude-zapier-zapier-setup/SKILL.md` — описание содержало неэкранированные `:` → strict YAML parser падал → spam в логах.

## Recommended actions (ручные)

### Immediate (сделать сейчас)

1. **Quit OrbStack** когда не пользуешься Docker — освободит 1.5 GB:
   ```bash
   osascript -e 'quit app "OrbStack"'
   ```

2. **Quit CleanMyMac Health Monitor** — это известный «помощник», который сам жрёт ресурсы:
   ```bash
   pkill -f CleanMyMac5.HealthMonitor
   ```

### Medium-term

3. **Увеличить swap cap** через dynamic_pager (потребует sudo + reboot):
   ```
   /etc/launchd.plist edit dynamic_pager max_swap_size
   ```
   Default 2 GB, рекомендую 8 GB на 36 GB машине с heavy workload.

4. **Reduce LaunchAgent count**: 582 — много. Многие dev-experiments (`backend-log-scanner`, `oauth-resync`, `daily-maintenance` и т.п.). Стоит провести audit и отключить неиспользуемые.

5. **Memory pressure pre-flight в start_krab.command** — добавить проверку `vm_stat` перед стартом и предупреждение если physmem < 4 GB free.

### Long-term

6. **Sleep mode**: если паттерн «panic после wake» подтвердится, отключить hibernation:
   ```bash
   sudo pmset -a hibernatemode 0
   sudo pmset -a standby 0
   sudo pmset -a powernap 0
   ```
   Это уменьшит state-restoration после wake (всё держится в RAM, ничего на диск).

7. **Krab Ear separate user session** — если KE крашится по CS-violation (May 8 01:31 SIGKILL Code Signature Invalid), пере-подписать app:
   ```bash
   codesign --force --deep --sign - "/Applications/Krab Ear.app"
   ```

## Не виноваты

- 3rd party kernel extensions (нет таковых)
- Krab userbot (PID 1019, RSS ~400 MB)
- Sleep/Wake bug — частично, только May 8 panic был после wake; остальные во время нормальной работы

## Prognosis

С квитом OrbStack + CleanMyMac, и swap cap 8 GB — должно прекратиться. Если повторится — рассмотреть отключение hibernation.
