---
name: krab-shared-repo-permission-keeper
description: "Проверять и безопасно исправлять проблемы с правами, ownership и writable-state в shared repo проекта `/Users/pablito/Antigravity_AGENTS/Краб` без массового `chown`, `reset --hard` и без вмешательства в чужой `~/.openclaw`. Использовать, когда между `pablito`, `USER2`, `USER3` появляется file permission conflict, shared-repo drift, launcher не может писать артефакты или другая учётка не может редактировать конкретный repo-файл."
---

# Krab Shared Repo Permission Keeper

Используй этот навык, когда проблема живёт в shared git tree, а не в account-local runtime слое.

## Прочитать перед стартом

- `/Users/pablito/Antigravity_AGENTS/Краб/docs/MULTI_ACCOUNT_SWITCHOVER_RU.md`
- `/Users/pablito/Antigravity_AGENTS/Краб/docs/SHARED_REPO_STRATEGY_RU.md`
- `/Users/pablito/Antigravity_AGENTS/Краб/docs/PARALLEL_DIALOG_PROTOCOL_RU.md`

## Основные точки входа

- `/Users/pablito/Antigravity_AGENTS/Краб/Check Shared Repo Drift.command`
- `/Users/pablito/Antigravity_AGENTS/Краб/Check New Account Readiness.command`
- `/Users/pablito/Antigravity_AGENTS/Краб/scripts/prepare_shared_repo_switchover.py`
- `/Users/pablito/Antigravity_AGENTS/Краб/scripts/check_second_account_readiness.py`
- `/Users/pablito/Antigravity_AGENTS/Краб/scripts/check_current_account_runtime.py`

## Рабочий цикл

1. Подтверди, что проблема действительно в repo-файлах:
   - другая учётка не может читать или писать конкретный файл;
   - launcher не может создать артефакт в shared tree;
   - shared repo drift или файл принадлежит неправильному owner.
2. Отдели shared-repo проблему от account-local:
   - `~/.openclaw/*`, OAuth, browser profile, session files не лечить этим навыком.
3. Сначала собери truth:
   - `git status --short --branch`
   - readiness/drift report
   - какой именно файл или каталог заблокирован.
4. Чини только минимальный scope:
   - один файл;
   - один служебный каталог;
   - один launcher output path.
5. После фикса повтори исходную операцию и зафиксируй:
   - что мешало;
   - что исправлено;
   - подтверждение, что другая учётка больше не блокируется.

## Красные флаги

- Не делать массовый `chown -R` по всему репо.
- Не использовать `git reset --hard` как способ “снять блокировку”.
- Не трогать чужой `~/.openclaw` ради writable shared repo.
- Не лечить ownership вслепую, пока не понятен текущий runtime owner.

## Рекомендуемые связки с другими skills

- `krab-multi-account-dev-coordinator`, если проблема проявилась при switchover между учётками.
- `krab-runtime-snapshot-handoff`, если после фикса нужно передать truthful state дальше.

## Ресурсы

- Матрица безопасных fixes: `references/ownership-checks.md`
- Чеклист точечного remediation: `assets/safe-fix-checklist.md`
