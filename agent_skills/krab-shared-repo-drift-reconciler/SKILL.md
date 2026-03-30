---
name: krab-shared-repo-drift-reconciler
description: "Безопасно сверять и разруливать drift между текущей рабочей копией проекта `/Users/pablito/Antigravity_AGENTS/Краб`, каноническим shared repo `/Users/Shared/Antigravity_AGENTS/Краб` и fast-path worktree `/Users/Shared/Antigravity_AGENTS/Краб-active`, не применяя опасные `reset --hard`, массовый `chown` и не подменяя git truth догадками. Использовать перед switchover между учётками, когда расходятся branch/HEAD, при сомнении в writable-state shared path и перед публикацией изменений в shared worktree."
---

# Krab Shared Repo Drift Reconciler

Используй этот навык, когда неясно, какая копия репозитория сейчас является рабочей, и есть риск перепутать локальную ветку, shared repo и fast-path worktree.

Его задача не «починить всё любой ценой», а выбрать безопасный путь выравнивания и явно пометить, где drift, а где просто разные рабочие контуры.

## Канонические точки

- локальная копия: `/Users/pablito/Antigravity_AGENTS/Краб`
- canonical shared repo: `/Users/Shared/Antigravity_AGENTS/Краб`
- fast-path worktree: `/Users/Shared/Antigravity_AGENTS/Краб-active`
- готовые launchers:
  - `/Users/pablito/Antigravity_AGENTS/Краб/Check Shared Repo Drift.command`
  - `/Users/pablito/Antigravity_AGENTS/Краб/Prepare Shared Repo Switchover.command`

## Рабочий цикл

1. Сними truthful drift/readiness report через `Check Shared Repo Drift.command`.
2. Раздели проблему:
   - branch/HEAD drift;
   - write-permission conflict;
   - active worktree не совпадает с текущей копией;
   - helper-account работает не в том repo path.
3. Если нужен controlled switchover, используй `Prepare Shared Repo Switchover.command`.
4. Лечи только конкретную проблему:
   - права на отдельный path;
   - выбор правильного worktree;
   - перенос работы в отдельную ветку.
5. После решения зафиксируй, какая копия теперь считается рабочей.

## Красные флаги

- `git reset --hard` как первая реакция на drift.
- массовый `chown` по всему shared repo.
- запуск live runtime, пока не понятен owner и рабочая копия.
- попытка «свести» repo, runtime и account-local state одним движением.

## Рекомендуемые связки с другими skills

- `krab-multi-account-dev-coordinator` для выбора режима helper/live.
- `krab-shared-repo-permission-keeper`, если проблема именно в writable-state.
- `krab-branch-handoff-governor`, если drift уже перешёл в веточную путаницу.

## Ресурсы

- Drift decision tree: `references/shared-repo-drift-decision-tree.md`
- Краткий reconcile checklist: `assets/shared-repo-reconcile-checklist.md`
