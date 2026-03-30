---
name: krab-artifact-freshness-auditor
description: "Проверять свежесть smoke, acceptance, handoff и ops-артефактов проекта `/Users/pablito/Antigravity_AGENTS/Краб`, отделяя актуальные evidence-файлы от stale или небезопасных для trust результатов. Использовать, когда перед handoff, merge, release или новым чатом нужно понять, какие JSON/PNG/MD артефакты ещё валидны относительно текущей ветки, runtime owner и времени последнего цикла."
---

# Krab Artifact Freshness Auditor

Используй этот навык, когда артефактов много и нужно понять, на что ещё можно опираться, а что уже устарело.

## Основные источники

- `artifacts/ops/*latest.json`
- `artifacts/handoff_*/`
- `temp/*acceptance*.json`
- `output/playwright/*`
- `scripts/export_handoff_bundle.py`
- `scripts/pre_release_smoke.py`

## Рабочий цикл

1. Сначала определи целевой вопрос:
   - handoff между учётками;
   - merge/release verdict;
   - восстановление контекста в новом чате;
   - проверка конкретного контура.
2. Проверь у каждого артефакта:
   - timestamp;
   - связанную ветку и commit, если есть;
   - на какой учётке собран;
   - helper-only это evidence или final `pablito` confirmation.
3. Если есть более свежий артефакт того же типа, старый не использовать как primary truth.
4. В итоговом выводе дели артефакты на:
   - `fresh`;
   - `stale but informative`;
   - `unsafe to trust`.

## Признаки stale артефакта

- собран до последних code changes;
- собран на другой учётке и выдаётся за final verdict;
- `latest` файл старый, а рядом уже есть более новый timestamped run;
- runtime owner или route уже поменялись.

## Рекомендуемые связки с другими skills

- `krab-acceptance-artifacts-curator` для отбора актуальных evidence-файлов.
- `krab-live-acceptance-brief-writer` для финального короткого summary.
- `krab-runtime-snapshot-handoff`, если после аудита нужно собрать новый bundle.

## Ресурсы

- Сигналы свежести и stale-state: `references/freshness-signals.md`
- Шаблон freshness report: `assets/freshness-report-template.md`
