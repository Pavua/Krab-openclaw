---
name: krab-workspace-truth-sync
description: "Сверять код, UI и repo-level документацию проекта `/Users/pablito/Antigravity_AGENTS/Краб` с реальным runtime source-of-truth в `~/.openclaw/*`. Использовать, когда нужно устранить дрейф между репозиторием и живым runtime, обновить roadmap/status docs по факту, убрать ложные model IDs, выровнять owner UI с runtime truth или подготовить handoff без выдуманных статусов."
---

# Krab Workspace Truth Sync

Используй этот навык для синхронизации документации и интерфейсов с живым runtime. При конфликте между repo и `~/.openclaw/*` источником истины считать runtime.

## Предусловие

```bash
cd /Users/pablito/Antigravity_AGENTS/Краб
```

## Рабочий цикл

1. Снять runtime snapshot.
2. Сопоставить его с roadmap, AGENTS/SKILLS/TOOLS и owner UI.
3. Обновить только те места, где есть фактический drift.
4. Если меняется поведение UI, перепроверить его через браузер.
5. Зафиксировать, что именно подтверждено, а что остаётся планом.

## Ключевые файлы

- `/Users/pablito/Antigravity_AGENTS/Краб/AGENTS.md`
- `/Users/pablito/Antigravity_AGENTS/Краб/SKILLS.md`
- `/Users/pablito/Antigravity_AGENTS/Краб/TOOLS.md`
- `/Users/pablito/Antigravity_AGENTS/Краб/docs/OPENCLAW_KRAB_ROADMAP.md`
- `/Users/pablito/Antigravity_AGENTS/Краб/scripts/export_handoff_bundle.py`

## Ограничения

- Не копировать runtime persona в repo-level docs как будто это одно и то же.
- Не отмечать чекбокс выполненным без проверки.
- Не удерживать устаревшие model IDs ради совместимости текста.
