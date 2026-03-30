---
name: krab-live-smoke-conductor
description: "Оркестрировать live smoke, acceptance и частично live e2e-проверки для проекта `/Users/pablito/Antigravity_AGENTS/Краб`. Использовать, когда нужно быстро прогнать живые сценарии после правки, подтвердить работоспособность transport/runtime/UI, собрать артефакты и отделить реальные блокеры от шумовых или внешних сбоев."
---

# Krab Live Smoke Conductor

Используй этот навык, когда нужно собрать быстрый, но не поверхностный сигнал о состоянии системы после правки. Подбирай минимальный достаточный набор live smoke, а не запускай всё подряд без причины.

## Предусловие

```bash
cd /Users/pablito/Antigravity_AGENTS/Краб
```

## Рабочий цикл

1. Определить, что именно изменилось: transport, UI, routing, runtime repair, LM Studio, ecosystem e2e.
2. Выбрать набор smoke/acceptance под это изменение.
3. Запустить скрипты и собрать артефакты.
4. Кратко сформулировать: что прошло, что упало, что шумовое.

## Основные точки входа

```bash
python3 scripts/live_channel_smoke.py --max-age-minutes 60
./live_channel_smoke.command
python3 scripts/live_ecosystem_e2e.py
python3 scripts/swarm_live_smoke.py
python3 scripts/channels_photo_chrome_acceptance.py
python3 scripts/pre_release_smoke.py --full
```

## Ограничения

- Не считать unit-test заменой live smoke.
- Не делать вывод о регрессе, пока не исключены внешние нестабильности.
- Если сценарий частично manual-only, явно отделять автоматизированную часть от неподтверждённой.
