---
name: krab-agent-request-router
description: "Быстро маршрутизировать пользовательский запрос по проекту `/Users/pablito/Antigravity_AGENTS/Краб`: выбрать подходящий repo-level skill, правильную macOS-учётку, режим работы (`code-only`, `dev-admin`, `runtime-admin`) и необходимость параллельных lanes. Использовать в начале новой задачи, когда непонятно, кому и чем её лучше делать, или когда запрос одновременно затрагивает код, runtime, docs, smoke и multi-account контур."
---

# Krab Agent Request Router

Используй этот навык как короткий triage-слой перед началом работы.

Он нужен не для реализации самой задачи, а для правильного выбора исполнителя, учётки и следующего skills stack.

## Что нужно определить

- задача про код, docs, runtime, transport, auth или install/bootstrap;
- нужна ли живая runtime ownership;
- можно ли работать с helper-account;
- нужен ли один агент или несколько disjoint lanes.

## Базовая маршрутизация

- install / setup / новая учётка:
  - `krab-dev-session-bootstrapper`
  - `krab-dev-env-doctor`
- multi-account / switchover / shared repo:
  - `krab-multi-account-dev-coordinator`
  - `krab-shared-repo-drift-reconciler`
  - `krab-shared-repo-permission-keeper`
- большая feature-задача:
  - `krab-role-split-delivery`
  - `krab-parallel-agent-orchestrator`
  - `krab-branch-handoff-governor`
- оценка радиуса правки:
  - `krab-runtime-change-impact-auditor`
- runtime prompt/auth:
  - `krab-openclaw-workspace-prompt-curator`
  - `krab-provider-auth-relogin-coordinator`
- smoke/evidence/handoff:
  - `krab-artifact-freshness-auditor`
  - `krab-live-acceptance-brief-writer`
  - предметный runtime/ui/transport skill по ситуации

## Как выбирать учётку

- `USER2` / `USER3`
  - хороший выбор для code-only, docs, skills, tests, branch prep, helper smoke.
- `pablito`
  - лучший выбор для final acceptance, release verdict, runtime-sensitive операций и финального merge verdict.
- если сомневаешься
  - начинай с helper `code-only`, а не с live runtime.

## Красные флаги

- сразу тащить helper-учётку в live runtime без необходимости;
- смешивать install/bootstrap задачу с runtime prompt edits;
- запускать параллельных агентов без явного write-scope.

## Ресурсы

- Матрица маршрутизации: `references/request-routing-matrix.md`
- Шаблон triage note: `assets/request-router-template.md`
