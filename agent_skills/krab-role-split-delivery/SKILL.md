---
name: krab-role-split-delivery
description: "Разрезать крупные задачи проекта `/Users/pablito/Antigravity_AGENTS/Краб` на роли `Architect`, `Runtime Engineer`, `UI Engineer`, `QA / Release` и вспомогательные lanes `docs` / `artifacts`, с ownership по файлам, merge-порядком и критериями верификации. Использовать, когда feature, bugfix или refactor затрагивает несколько слоёв Краба и нужно заранее выдать чёткий delivery-plan без конфликтов."
---

# Krab Role Split Delivery

Используй этот навык, когда нужно не просто распараллелить работу, а сначала правильно разрезать её по ролям и зонам ответственности.

## Основные роли проекта

- `Architect`
  - truth, архитектурные решения, границы изменения, merge-order, риски;
  - типичные файлы: `AGENTS.md`, roadmap, архитектурные docs, coordination notes.
- `Runtime Engineer`
  - OpenClaw routing, userbot, bridge, transport, provider logic, runtime-sensitive scripts;
  - типичные файлы: `src/`, `scripts/openclaw_*`, `scripts/runtime_*`, `data contracts`.
- `UI Engineer`
  - owner panel `:8080`, browser-facing UX, DOM smoke, launcher UX;
  - типичные файлы: `src/web/`, HTML/CSS/JS, browser smoke helpers.
- `QA / Release`
  - unit/integration/smoke, evidence, acceptance artifacts, release verdict;
  - типичные файлы: `tests/`, `output/reports/`, `artifacts/`, handoff docs.

## Рабочий цикл

1. Выдели затронутые слои: `runtime`, `owner-ui`, `transport`, `docs`, `qa`, `ops`.
2. Если всё изменение живёт в одном слое, не дроби задачу искусственно.
3. Если слоёв несколько, назначь owner по файлам, а не по абстрактным словам.
4. Для каждой роли зафиксируй:
   - цель;
   - допустимый write-scope;
   - входящие зависимости;
   - критерии готовности;
   - нужную верификацию.
5. Определи merge-order:
   - сначала truth и базовый design;
   - затем code lanes;
   - затем verification;
   - затем docs/handoff.

## Типовые разрезы

- `bugfix в transport`:
  - Runtime Engineer: код и тесты;
  - QA / Release: smoke и regression verdict;
  - Docs lane: handoff и changelog только после подтверждения.
- `feature в owner panel`:
  - UI Engineer: интерфейс и browser flow;
  - Runtime Engineer: API или runtime endpoint;
  - QA / Release: smoke + screenshot evidence.
- `multi-account workflow change`:
  - Architect: protocol и ownership;
  - Runtime Engineer: launcher/scripts;
  - QA / Release: readiness/switchover evidence.

## Чего избегать

- Не выдавай двум ролям один и тот же файл “на всякий случай”.
- Не смешивай реализацию и финальный release verdict в одной lane, если можно отделить проверку.
- Не поручай UI-агенту менять runtime truth без явной необходимости.

## Рекомендуемые связки с другими skills

- `krab-parallel-agent-orchestrator` после первичного role split.
- `krab-runtime-doctor`, `krab-model-routing-ops`, `krab-owner-ui-smoke`, `krab-release-gate-keeper` как доменные исполнители.
- `krab-docs-maintainer` и `krab-acceptance-artifacts-curator` для финального замыкания цикла.

## Ресурсы

- Карта ролей и file ownership: `references/role-lanes.md`
- Шаблон delivery brief: `assets/role-split-brief-template.md`
