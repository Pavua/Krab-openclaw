---
name: krab-acceptance-artifacts-curator
description: "Собирать, проверять и поддерживать acceptance artifacts, smoke reports, ops JSON и handoff evidence в проекте `/Users/pablito/Antigravity_AGENTS/Краб`. Использовать, когда нужно понять, какие артефакты актуальны, собрать доказательную базу после проверок, очистить устаревшие ссылки в отчётах или убедиться, что release/handoff опирается на свежие evidence-файлы."
---

# Krab Acceptance Artifacts Curator

Используй этот навык для evidence hygiene после проверок. Его задача: отделить свежие и полезные артефакты от хвостов, которые только создают ложную уверенность.

## Предусловие

```bash
cd /Users/pablito/Antigravity_AGENTS/Краб
```

## Основные места

- `/Users/pablito/Antigravity_AGENTS/Краб/artifacts/ops/`
- `/Users/pablito/Antigravity_AGENTS/Краб/output/reports/`
- `/Users/pablito/Antigravity_AGENTS/Краб/artifacts/handoff_*/`
- `/Users/pablito/Antigravity_AGENTS/Краб/temp/runtime_snapshot.json`
- `/Users/pablito/Antigravity_AGENTS/Краб/scripts/export_handoff_bundle.py`

## Рабочий цикл

1. Определить, какие проверки реально запускались.
2. Найти соответствующие JSON/MD evidence-файлы.
3. Проверить их свежесть и соответствие текущей ветке/состоянию.
4. Сослаться на актуальные артефакты в отчётах и handoff.

## Полезные источники

- `/Users/pablito/Antigravity_AGENTS/Краб/docs/ops_incident_runbook.md`
- `/Users/pablito/Antigravity_AGENTS/Краб/docs/MIGRATION_HANDOFF_2026-03-02.md`
- `/Users/pablito/Antigravity_AGENTS/Краб/scripts/pre_release_smoke.py`
- `/Users/pablito/Antigravity_AGENTS/Краб/scripts/live_ecosystem_e2e.py`

## Ограничения

- Не подсовывать старый `latest.json`, если он не относится к текущей проверке.
- Не строить release verdict на артефактах без даты или контекста.
- Если evidence неполный, явно говорить, чего не хватает.
