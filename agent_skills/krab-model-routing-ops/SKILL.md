---
name: krab-model-routing-ops
description: "Управлять primary/fallback model routing, compat probe, registry sync и autoswitch-профилями OpenClaw/Krab. Использовать, когда нужно сменить primary-модель, проверить совместимость `GPT-5.4`, восстановить честную fallback-цепочку, синхронизировать runtime registry, отладить cloud/local приоритет или объяснить, почему UI и runtime показывают разное состояние маршрутизации."
---

# Krab Model Routing Ops

Используй этот навык как безопасный оператор маршрутизации моделей. Сначала проверяй факт через dry-run и compat probe, потом применяй изменения.

## Предусловие

```bash
cd /Users/pablito/Antigravity_AGENTS/Краб
```

## Рабочий цикл

1. Снять текущий routing status через UI endpoint или CLI-утилиты.
2. Запустить compat probe для целевой модели.
3. Запустить autoswitch в dry-run.
4. При необходимости синхронизировать registry и auth/runtime каталоги.
5. Только после этого применять изменение primary/fallback.
6. Проверить итог через live status и, если затронут UI, через `:8080`.

## Основные инструменты

```bash
python3 scripts/openclaw_model_compat_probe.py
python3 scripts/openclaw_model_autoswitch.py --dry-run --profile current
./scripts/openclaw_model_autoswitch.command
python3 scripts/openclaw_model_registry_sync.py
python3 scripts/sync_openclaw_models.py
python3 scripts/check_cloud_chain.py
pytest tests/unit/test_openclaw_model_autoswitch.py -q
pytest tests/unit/test_openclaw_model_compat_probe.py -q
```

## На что обращать внимание

- реально ли target model присутствует в runtime registry;
- не врёт ли UI про `READY/BLOCKED`;
- не тянется ли в fallback legacy-путь, который уже признан нежелательным;
- не меняется ли локальный `lmstudio/*` provider на облачный случайно;
- совпадает ли routing policy между web и runtime.

## Ограничения

- Не переключать primary по одному только статическому конфигу.
- Не считать Codex-совместимость доказательством OpenClaw-совместимости.
- Любое write-действие сопровождать статусом до/после.
- Если можно обойтись dry-run и объяснением, не менять runtime без необходимости.
