---
name: krab-release-readiness-pack
description: "Собирать truthful release readiness verdict для проекта `/Users/pablito/Antigravity_AGENTS/Краб` на основе существующих merge-gate, pre-release smoke, acceptance evidence и свежих ops/handoff артефактов, не подменяя отсутствие проверки оптимистичными формулировками. Использовать перед merge, перед пушем важных веток, перед handoff как release candidate и когда нужно быстро понять, что ещё реально блокирует релиз."
---

# Krab Release Readiness Pack

Используй этот навык, когда нужен не просто список тестов, а честный ответ: можно ли считать ветку release-ready, release-blocked или only-helper-verified.

Он опирается на уже существующие инструменты проекта и не должен дублировать их реализацию.

## Канонические входы

- `/Users/pablito/Antigravity_AGENTS/Краб/Release Gate.command`
- `/Users/pablito/Antigravity_AGENTS/Краб/pre_release_smoke.command`
- `scripts/pre_release_smoke.py`
- `artifacts/ops/pre_release_smoke_latest.json`
- `artifacts/ops/r20_merge_gate_latest.json`
- свежие acceptance/handoff evidence

## Рабочий цикл

1. Сначала проверь свежесть evidence:
   - какие `latest` артефакты реально относятся к текущей ветке и времени;
   - где helper-account evidence, а где финальная проверка.
2. Если нужен новый verdict, запусти:
   - `Release Gate.command` для строгого сценария;
   - или `pre_release_smoke.command`, если сначала нужен более мягкий проход.
3. Сверь обязательные и advisory checks:
   - merge gate;
   - pre-release smoke;
   - runtime owner / environment blockers;
   - acceptance/e2e, если они требуются для этого типа изменения.
4. Выдай итог только в одном из статусов:
   - `release-ready`
   - `release-blocked`
   - `environment-blocked`
   - `helper-verified, final pablito pass required`
5. В verdict всегда отдельно пиши:
   - что подтверждено;
   - что не проверялось;
   - что блокирует;
   - чей следующий шаг.

## Что не считать release-ready

- helper-account smoke без возврата на `pablito`, если это критичный merge/release;
- старый `latest.json`, если рядом есть более новый timestamped run;
- зелёный unit-only результат, если runtime/transport слой изменялся;
- environment-blocked прогон, выданный как кодовый успех.

## Рекомендуемые связки с другими skills

- `krab-artifact-freshness-auditor` перед сборкой verdict.
- `krab-live-acceptance-brief-writer` после verdict, если нужен короткий attach-ready summary.
- `krab-pr-review-triager`, если release-blockers пришли из review findings.

## Ресурсы

- Матрица release-вердиктов: `references/release-readiness-matrix.md`
- Шаблон release summary: `assets/release-readiness-summary-template.md`
