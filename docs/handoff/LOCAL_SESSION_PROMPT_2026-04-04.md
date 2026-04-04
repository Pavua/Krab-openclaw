# Промпт для локальной Claude Code сессии — 2026-04-04

Скопируй текст ниже в новый локальный диалог Claude Code.

---

```
Продолжаем работу в репозитории /Users/pablito/Antigravity_AGENTS/Краб.

## Что нужно сделать первым делом

1. Смержить облачную ветку в main:
   git fetch origin
   git checkout main
   git merge origin/claude/sweet-chatterjee-r44QI --no-ff -m "merge: сессия 2026-04-04 (#6 #7 swarm-R18)"
   git push origin main

2. Перезапустить Краба если он запущен (Restart Krab.command)

## Что было сделано в облачной сессии (ветка claude/sweet-chatterjee-r44QI)

- #6: _TelegramSendQueue — per-chat async очередь с retry/backoff для всех Telegram API вызовов
- #7: Granular tool narration — "🌐 Открываю браузер..." вместо "🔧 Выполняется: browser"
- Swarm R18: именованные команды (traders/coders/analysts/creative) + SwarmBus межкомандное делегирование

Полный отчёт: docs/handoff/QUICK_START_NEXT_SESSION.md

## Задачи для локальной сессии

### Приоритет 1 — Протестировать swarm live
Подключи Telegram MCP (@p0lrd) и проверь новые команды:
  !swarm teams
  !swarm traders анализируй ETH/BTC
  !swarm coders написать простой price alert скрипт
Убедись что ответы приходят, делегирование [DELEGATE: coders] работает.

### Приоритет 2 — macOS интеграция (только локально)
**#11 ~/Krab_Inbox папка:**
- Создать ~/Krab_Inbox
- Добавить macOS Folder Action: любой файл → отправить Крабу через Telegram MCP
- Скрипт: scripts/krab_inbox_watcher.py (FSEvents / watchdog lib)
- LaunchAgent для автозапуска

**#12 Global Hotkey:**
- Apple Shortcuts: глобальное сочетание клавиш → голосовой/текстовый ввод в Краба
- Использовать существующий src/integrations/macos_automation.py

**#13 Hammerspoon window management:**
- Lua скрипты в ~/.hammerspoon/
- Краб командует расположением окон через src/integrations/hammerspoon_bridge.py

### Приоритет 3 — Mercadona anti-bot (#10)
- src/skills/mercadona.py: добавить puppeteer-extra-plugin-stealth
- Перехватывать XHR/Fetch ответы через page.on('response') вместо DOM-парсинга

### Приоритет 4 — Swarm следующий этап
- Персистентная память агентов (SQLite в artifacts/)
- !swarm scheduler — запуск команды по расписанию (через krab_scheduler)

## Текущий MCP статус
- krab-telegram = @yung_nagato (основной Краб)
- krab-telegram-p0lrd = @p0lrd (тестовый)

Если MCP не виден — перезапусти Codex/Claude Code после git pull.

## Runtime
- Краб: http://127.0.0.1:8080
- OpenClaw: http://127.0.0.1:18789
- Voice Gateway: http://127.0.0.1:8090
- Запуск: Start Full Ecosystem.command
```
