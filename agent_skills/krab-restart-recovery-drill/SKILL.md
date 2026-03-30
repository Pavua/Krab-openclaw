---
name: krab-restart-recovery-drill
description: "Проводить controlled restart и post-restart recovery drill для проекта `/Users/pablito/Antigravity_AGENTS/Краб`, включая launchers, runtime, owner panel и transport-проверки после перезапуска. Использовать, когда нужно доказать, что restart не ломает `:8080`, `:18789`, userbot, reserve delivery, browser readiness или связанный operational контур."
---

# Krab Restart Recovery Drill

Используй этот навык для контролируемого restart-цикла с обязательной проверкой после запуска. Главное: restart считается успешным только если контур восстановился, а не если процессы просто перезапустились.

## Предусловие

```bash
cd /Users/pablito/Antigravity_AGENTS/Краб
```

## Основные точки входа

- `/Users/pablito/Antigravity_AGENTS/Краб/Restart Krab.command`
- `/Users/pablito/Antigravity_AGENTS/Краб/start_krab.command`
- `/Users/pablito/Antigravity_AGENTS/Краб/new Stop Krab.command`
- `/Users/pablito/Antigravity_AGENTS/Краб/scripts/e1e3_acceptance.py`

## Рабочий цикл

1. Снять baseline health до restart.
2. Сделать controlled stop/start через launcher.
3. Проверить `:8080`, `:18789`, runtime status и userbot state.
4. Проверить хотя бы один transport- или UI-сценарий после restart.
5. Если restart нужен в нескольких циклах, использовать acceptance-скрипт с restart loops.

## Полезные команды

```bash
python3 scripts/e1e3_acceptance.py --restart-cycles 1
python3 scripts/live_channel_smoke.py --max-age-minutes 60
```

## Ограничения

- Не считать restart успешным по одному только exit code launcher-а.
- Не использовать старый smoke-результат до restart как post-restart подтверждение.
- Если после restart появился новый degraded state, фиксировать его как новый результат, а не скрывать.
