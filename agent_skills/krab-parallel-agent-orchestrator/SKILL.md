---
name: krab-parallel-agent-orchestrator
description: "Оркестрировать параллельную разработку в проекте `/Users/pablito/Antigravity_AGENTS/Краб` между несколькими агентами, диалогами и моделями без write-conflict, с разрезанием по ownership, disjoint file sets, merge-order и handoff-артефактами. Использовать, когда одну задачу выгодно распараллелить между backend/UI/docs/QA, между несколькими Codex/Claude/Gemini сессиями или между coding lane и verification lane."
---

# Krab Parallel Agent Orchestrator

Используй этот навык, когда задачу нужно ускорить параллелизмом, но нельзя потерять контроль над truth, runtime и merge-порядком.

## Прочитать перед стартом

- `/Users/pablito/Antigravity_AGENTS/Краб/AGENTS.md`
- `/Users/pablito/Antigravity_AGENTS/Краб/docs/PARALLEL_DIALOG_PROTOCOL_RU.md`
- `/Users/pablito/Antigravity_AGENTS/Краб/docs/AGENT_DELEGATION_STRATEGY.md`
- `/Users/pablito/Antigravity_AGENTS/Краб/docs/MULTI_ACCOUNT_SWITCHOVER_RU.md`, если в работе участвуют `USER2` / `USER3`

## Базовый принцип

Сначала выдели критический путь и только потом распараллеливай sidecar-работу. Не делегируй то, что блокирует следующий локальный шаг.

## Рабочий цикл

1. Раздели задачу на три класса:
   - `single-owner runtime lane` — всё, что меняет live runtime, launcher baseline, `~/.openclaw` truth или общие mutable endpoints;
   - `code lanes` — disjoint file sets в `src/`, `scripts/`, `tests/`, `docs/`;
   - `verification lanes` — unit, smoke, browser, docs, acceptance artifacts.
2. Для каждой lane зафиксируй:
   - цель;
   - владельца;
   - разрешённые файлы;
   - запрещённые файлы;
   - критерий готовности;
   - какой артефакт должен остаться после завершения.
3. Если два lane хотят писать в один файл, это не parallel task. Назначь одному владельца и перенеси второй lane после merge первой правки.
4. Если изменения касаются runtime truth, оставь только одного mutating owner на весь цикл `edit -> test -> smoke -> handoff`.
5. После завершения lane требуй короткий отчёт в формате:
   - что изменено;
   - как проверено;
   - что осталось;
   - влияет ли это на runtime truth.

## Когда параллелизм уместен

- `backend + docs + unit-tests`
- `owner-ui + browser smoke + docs`
- `runtime diagnosis + evidence collection + handoff packaging`
- `feature implementation + независимый code review или read-only audit`

## Когда параллелизм вреден

- два агента одновременно правят один launcher, один markdown handoff или один runtime-sensitive script;
- два live smoke цикла идут с разных учёток;
- один агент меняет код, а второй одновременно обновляет docs на старом понимании состояния;
- несколько агентов пишут в один shared state path.

## Жёсткие ограничения

- Только один live/runtime owner в момент времени.
- Не смешивай write-scope и review-scope: reviewer не должен параллельно переписывать те же файлы.
- Не объявляй lane завершённым без конкретной проверки.
- Если file split не получается сделать чисто, переходи в serial mode.

## Рекомендуемые связки с другими skills

- `krab-role-split-delivery` для первичного разрезания задачи по ролям.
- `krab-multi-account-dev-coordinator`, если в работе участвуют `USER2` / `USER3`.
- `krab-owner-ui-smoke`, `krab-live-smoke-conductor`, `krab-runtime-snapshot-handoff`, `krab-docs-maintainer` для verification lanes.

## Ресурсы

- Подробная матрица конфликтов: `references/conflict-matrix.md`
- Шаблон плана lane ownership: `assets/parallel-task-brief-template.md`
