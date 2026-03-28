# Krab Ear Launcher Readiness — 2026-03-28

## Контекст
- Цель: убрать ложный warning `Krab Ear пока не подтвердил запуск` из one-click launcher при штатном старте Ear.
- Контур: внешний launcher `/Users/pablito/Antigravity_AGENTS/new start_krab.command`, repo launcher `/Users/pablito/Antigravity_AGENTS/Краб/new start_krab.command`, экосистемный launcher `/Users/pablito/Antigravity_AGENTS/Краб/Start Full Ecosystem.command`.

## Причина
- Старый verdict строился по схеме `sleep 1 -> pgrep`.
- Реальный Ear публикует IPC сокет не мгновенно: watchdog лог регулярно показывает `socket_missing` на первом цикле и `backend снова healthy` примерно через 8 секунд.
- Из-за этого one-click старт ложно сообщал о непрогретом Ear, хотя backend был жив и успешно выходил в healthy shortly after.

## Что изменено
- Добавлены shell-хелперы `probe_krab_ear_ready()` и `wait_krab_ear_ready()`.
- Основной источник истины: `scripts/krab_ear_watchdog.py --probe --ear-dir ...`.
- `pgrep` оставлен только как fallback, если probe временно недоступен.
- Timeout ожидания readiness: `12` секунд.

## Живая проверка
1. Остановлен стек через `/Users/pablito/Antigravity_AGENTS/new Stop Krab.command`.
2. Поднят стек через `/Users/pablito/Antigravity_AGENTS/new start_krab.command`.
3. В стартовом trace подтверждено:
   - `🦻 Запускаю Krab Ear Agent...`
   - `✅ Krab Ear Agent запущен.`
   - далее launcher дошёл до `🚀 Starting Krab...`
4. Truth probe после старта:
   - `python3 /Users/pablito/Antigravity_AGENTS/Краб/scripts/krab_ear_watchdog.py --probe --ear-dir "/Users/pablito/Antigravity_AGENTS/Krab Ear"`
   - результат: `{"ok": true, "status": "ok", ...}`
5. Runtime health после старта:
   - `curl -sf http://127.0.0.1:8080/api/health/lite`
   - результат: `ok=true`, `status=up`, `telegram_userbot_state=running`
6. Owner panel после того же рестарта открылась штатно и показала `Core Liveness = Online`, `Ecosystem Deep Health = OK`, `ON Krab Ear`.

## Вывод
- Launcher больше не врёт про Ear readiness при штатном старте.
- Проверка теперь совпадает с тем, что реально считает healthy сам watchdog/IPC контур.

## Артефакты
- `/Users/pablito/Antigravity_AGENTS/Краб/output/playwright/owner-panel-post-ear-launcher-fix-20260328-1430.png`
