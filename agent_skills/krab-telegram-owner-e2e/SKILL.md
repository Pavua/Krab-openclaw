---
name: krab-telegram-owner-e2e
description: "Проводить живую E2E-проверку Telegram owner userbot и reserve bot в проекте `/Users/pablito/Antigravity_AGENTS/Краб`. Использовать, когда нужно подтвердить outbound/inbound round-trip, post-restart delivery, reserve-safe режим, ACL-доступ, доставку owner-сообщений или воспроизвести транспортный регресс в Telegram."
---

# Krab Telegram Owner E2e

Используй этот навык для живого подтверждения доставки, а не для теоретической оценки. Сначала прогоняй локальные и интеграционные проверки, затем делай live round-trip.

## Предусловие

```bash
cd /Users/pablito/Antigravity_AGENTS/Краб
```

Убедись, что есть действующие Telegram-сессии и что runtime поднят.

## Рабочий цикл

1. Проверить текущее состояние runtime и transport health.
2. При необходимости сделать controlled restart через `.command`-точки входа.
3. Прогнать unit/integration, если задача про недавний регресс.
4. Запустить live smoke и затем точечный E2E-сценарий.
5. Зафиксировать отдельно outbound, inbound и post-restart результат.

## Основные точки входа

```bash
./live_channel_smoke.command
python3 scripts/live_channel_smoke.py --max-age-minutes 60
python3 scripts/live_ecosystem_e2e.py
python3 scripts/swarm_live_smoke.py
pytest tests/test_live_channel_smoke.py -q
pytest tests/test_live_ecosystem_e2e.py -q
```

## Что считать успехом

- owner-сообщение уходит через нужный контур;
- ответ возвращается без потери контекста;
- после restart сценарий повторяется без ручной реинициализации;
- reserve bot остаётся в reserve-safe режиме;
- ACL не блокирует владельца и не открывает лишние каналы.

## Ограничения

- Не спамить произвольные внешние чаты для проверки.
- Не смешивать outbound delivery и полный round-trip в один статус.
- Если inbound нельзя автоматизировать в текущей среде, явно помечать это как непокрытый участок.
- После любого restart повторно подтверждать доставку, а не переносить старый зелёный статус.
