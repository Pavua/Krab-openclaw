---
name: krab-live-acceptance-brief-writer
description: "Собирать краткий truthful acceptance brief для проекта `/Users/pablito/Antigravity_AGENTS/Краб` из smoke-артефактов, handoff bundle, runtime snapshot и release evidence. Использовать после live smoke, e2e, restart drill, transport regression или перед передачей работы между `USER2`, `USER3` и `pablito`, когда нужно быстро получить attach-ready summary без расплывчатых формулировок."
---

# Krab Live Acceptance Brief Writer

Используй этот навык, когда артефакты уже есть, но следующий человек или агент не должен перечитывать весь bundle ради одного truthful verdict.

## Основные источники

- `artifacts/handoff_<timestamp>/ATTACH_SUMMARY_RU.md`
- `artifacts/handoff_<timestamp>/START_NEXT_CHAT.md`
- `artifacts/handoff_<timestamp>/HANDOFF_MANIFEST.json`
- `scripts/export_handoff_bundle.py`
- `artifacts/ops/*.json`
- `temp/*acceptance*.json`

## Рабочий цикл

1. Собери свежие источники:
   - runtime snapshot;
   - smoke/e2e/report JSON;
   - handoff bundle, если он уже есть.
2. Раздели brief на четыре части:
   - что подтверждено;
   - что не проверялось;
   - остаточные риски;
   - кому принадлежит следующий шаг.
3. Если evidence собрано на helper-учётке, явно пометь это как `helper-account evidence`.
4. Не повышай статус до `release-ready`, если нет финального цикла на `pablito`.

## Формат good brief

- 5-12 строк по делу;
- конкретные даты и проверочные артефакты;
- без “вроде работает” и без неявных процентов;
- отдельно указано, затрагивает ли это runtime truth.

## Рекомендуемые связки с другими skills

- `krab-runtime-snapshot-handoff` для сборки fresh bundle.
- `krab-acceptance-artifacts-curator` для отбора актуальных evidence-файлов.
- `krab-docs-maintainer`, если brief нужно превратить в repo-level update.

## Ресурсы

- Сводка источников для brief: `references/brief-sources.md`
- Шаблон acceptance summary: `assets/acceptance-brief-template.md`
