# TOOLS.md

Этот файл фиксирует инструментальные контуры проекта и их назначение.
Он не является боевой tool-политикой OpenClaw-агента. Боевая tool-конфигурация живёт в runtime OpenClaw.

## Канонические контуры

- `OpenClaw Gateway`: основной backend маршрутизации и channel/runtime truth
- `Telegram userbot bridge`: primary owner transport
- `Telegram bot`: reserve transport для диагностики и recovery
- `LM Studio`: локальный inference-контур
- `Web panel :8080`: owner control layer поверх runtime truth
- `Native OpenClaw dashboard :18789`: системная и диагностическая панель

## Инструменты, которые нельзя дублировать без причины

- `openclaw status`
- `openclaw models status`
- `openclaw gateway probe`
- `openclaw browser ...`
- `openclaw auth ...`
- `openclaw security audit`

## Ожидаемый порядок интеграции

1. Сначала читаем runtime truth из OpenClaw.
2. Затем агрегируем это в коде/панели Краба.
3. Только если upstream чего-то не умеет, добавляем собственный glue-слой.
