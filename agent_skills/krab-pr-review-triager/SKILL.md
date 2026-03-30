---
name: krab-pr-review-triager
description: "Триажить review-комментарии, change requests и найденные дефекты по проекту `/Users/pablito/Antigravity_AGENTS/Краб`, отделяя реальные блокеры от косметики, группируя правки по риску и выстраивая безопасный порядок исправлений без merge-chaos. Использовать при разборе PR review, после code review с несколькими замечаниями, перед новым fix-pass и когда нужно быстро понять, что чинить первым."
---

# Krab PR Review Triager

Используй этот навык, когда замечаний много и опасно чинить их в случайном порядке.

Он нужен, чтобы перевести review-поток в рабочую очередь: что блокирует merge, что можно объединить в один fix-pass, а что лучше оставить отдельным follow-up.

## Что считать входом

- inline review comments;
- summary review;
- локальный code review findings list;
- замечания из handoff или acceptance, которые фактически эквивалентны review.

## Рабочий цикл

1. Собери все findings в один список без потери контекста.
2. Для каждого пункта определи:
   - это реальный behavioural risk или cosmetic note;
   - затрагивает ли runtime, transport, release gate или только локальный refactor;
   - требует ли немедленного исправления до merge.
3. Сгруппируй findings в три корзины:
   - `must-fix before merge`
   - `should-fix in current pass`
   - `follow-up / non-blocking`
4. Построй fix-order:
   - shared contracts и safety fixes;
   - runtime / transport regressions;
   - tests и evidence;
   - docs / cleanup.
5. Если замечания пересекаются по файлам, сначала выдай ownership plan, потом чини.

## Что считать блокером

- поведенческая регрессия;
- нарушение multi-account / runtime boundary;
- отсутствие теста на уже найденный риск;
- ложный release-ready verdict;
- фиксация stale evidence как будто это свежая проверка.

## Красные флаги

- чинить косметику раньше safety/regression fixes;
- смешивать несколько конфликтующих замечаний в один непроверяемый mega-patch;
- отвечать на review без фактической проверки кода и тестов;
- считать helper-account verification полноценным merge-вердиктом.

## Рекомендуемые связки с другими skills

- `krab-branch-handoff-governor` если fix-pass затрагивает несколько агентов или веток.
- `krab-role-split-delivery` если review findings расходятся по runtime/UI/docs.
- `krab-release-readiness-pack` после исправлений, если нужен новый release verdict.

## Ресурсы

- Матрица triage-приоритетов: `references/review-triage-matrix.md`
- Шаблон fix-pass summary: `assets/review-fix-summary-template.md`
