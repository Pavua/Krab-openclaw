---
name: krab-docs-maintainer
description: "Поддерживать roadmap, handoff, smoke-отчёты и repo-level инженерную документацию проекта `/Users/pablito/Antigravity_AGENTS/Краб` в состоянии, соответствующем фактическому коду и проверкам. Использовать, когда после изменений нужно обновить статус этапа, задокументировать проверку, собрать handoff или вычистить устаревшие утверждения."
---

# Krab Docs Maintainer

Используй этот навык для документации, которая должна совпадать с реальностью. Обновляй только то, что подтверждено кодом, тестом, smoke или live verification.

## Предусловие

```bash
cd /Users/pablito/Antigravity_AGENTS/Краб
```

## Рабочий цикл

1. Понять, какие изменения реально были внесены.
2. Найти документы, где это состояние уже отражено неверно или неполно.
3. Обновить статусы, даты, шаги проверки и остаточные риски.
4. Не трогать unrelated docs ради “косметики”.

## Основные документы

- `/Users/pablito/Antigravity_AGENTS/Краб/docs/OPENCLAW_KRAB_ROADMAP.md`
- `/Users/pablito/Antigravity_AGENTS/Краб/PROJECT_STATE_MIGRATION.md`
- `/Users/pablito/Antigravity_AGENTS/Краб/refactoring_status_report.md`
- `/Users/pablito/Antigravity_AGENTS/Краб/output/reports/R24_SMOKE_REPORT.md`
- `/Users/pablito/Antigravity_AGENTS/Краб/README.md`

## Ограничения

- Не ставить `[x]`, если нет подтверждения.
- Не писать расплывчатые формулировки вроде “вроде стабильно”.
- Использовать точные даты и конкретные проверки.
