---
name: krab-lmstudio-guard
description: "Диагностировать и стабилизировать LM Studio контур проекта `/Users/pablito/Antigravity_AGENTS/Краб`: loaded/idle/offline состояние, токены, idle guard, загрузку/выгрузку моделей и лимиты. Использовать, когда локальные модели не отвечают, UI показывает неверный LM Studio state, требуется safe load/unload или нужно понять, почему local-first режим не работает."
---

# Krab Lmstudio Guard

Используй этот навык для local-model контура. Сначала проверяй реальное состояние LM Studio и загруженных моделей, затем вмешивайся.

## Предусловие

```bash
cd /Users/pablito/Antigravity_AGENTS/Краб
```

## Рабочий цикл

1. Проверить статус LM Studio и loaded models.
2. Проверить idle guard и последние отчёты.
3. Если local route сломан, сопоставить это с runtime routing status.
4. При необходимости безопасно загрузить или выгрузить модели.
5. После изменения перепроверить UI и runtime snapshots.

## Основные точки входа

```bash
./LM Studio Status.command
python3 scripts/lmstudio_control.py --help
python3 scripts/lmstudio_idle_guard.py
./scripts/lmstudio_idle_guard.command
python3 scripts/verify_lm_studio_limits.py
```

## Ограничения

- Не выгружать модели вслепую, если они могут быть активны в текущем runtime.
- Перед изменением фиксировать, какие модели были загружены.
- Любой вывод `offline/idle/loaded` подтверждать не только UI, но и CLI/логами.
