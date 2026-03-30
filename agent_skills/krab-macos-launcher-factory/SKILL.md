---
name: krab-macos-launcher-factory
description: "Создавать, чинить и унифицировать macOS `.command`-лаунчеры для проекта `/Users/pablito/Antigravity_AGENTS/Краб`. Использовать, когда нужно сделать one-click запуск, остановку, restart, status check, bootstrap или repair-действие для пользователя macOS без ручного ввода команд в терминале."
---

# Krab Macos Launcher Factory

Используй этот навык для пользовательских точек входа. Если действие предполагается запускать руками с Mac, оформляй его как `.command`, а не как `.sh`.

## Предусловие

```bash
cd /Users/pablito/Antigravity_AGENTS/Краб
```

## Рабочий цикл

1. Найти ближайший существующий `.command` с похожим поведением.
2. Сохранить единый стиль: shebang, переход в корень проекта, понятные русские сообщения, bounded wait.
3. Если launcher стартует сервис, добавить проверку успеха.
4. Если launcher останавливает сервис, сделать его идемпотентным и безопасным к повторному запуску.
5. Выдать `chmod +x` и реально запустить launcher для проверки.

## Полезные шаблоны

- `/Users/pablito/Antigravity_AGENTS/Краб/start_krab.command`
- `/Users/pablito/Antigravity_AGENTS/Краб/Restart Krab.command`
- `/Users/pablito/Antigravity_AGENTS/Краб/Start Full Ecosystem.command`
- `/Users/pablito/Antigravity_AGENTS/Краб/Check Full Ecosystem.command`
- `/Users/pablito/Antigravity_AGENTS/Краб/scripts/openclaw_model_autoswitch.command`

## Ограничения

- Не оставлять пользователю только “вот команда, выполни сам”.
- Не делать launcher, который может зависнуть бесконечно на stop.
- Не писать логику в несколько разбросанных shell-файлов без причины.
- После создания launcher обязательно сделать его исполняемым и проверить живым запуском.
