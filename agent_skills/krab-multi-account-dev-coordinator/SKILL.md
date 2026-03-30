---
name: krab-multi-account-dev-coordinator
description: "Координировать разработку и проверку проекта `/Users/pablito/Antigravity_AGENTS/Краб` между `pablito`, `USER2`, `USER3` и shared repo, не смешивая `~/.openclaw`, OAuth, browser/MCP state, runtime ownership и account-local secrets. Использовать при смене учётки, live bootstrap/reclaim, readiness check, freeze/handoff и перед любым запуском runtime вне основной учётки."
---

# Krab Multi Account Dev Coordinator

Используй этот навык, когда shared repo один, а runtime, OAuth, browser state и `~/.openclaw` у каждой macOS-учётки свои.

## Truth split

- Общее между учётками:
  - `/Users/Shared/Antigravity_AGENTS/Краб`
  - repo-level `docs/`, `artifacts/`, `.command`, `scripts/`
- Строго account-local:
  - `~/.openclaw/*`
  - OAuth и login state
  - browser profile и remote-debugging state
  - `.env`, токены, session files, lock/PID/socket/state

## Режимы работы

1. `code-only helper mode`
   - для `USER2` / `USER3`, когда нужны код, тесты, docs и локальная аналитика без live runtime ownership.
2. `controlled live helper mode`
   - только после readiness check и явного reclaim/freeze текущего live owner.
3. `return-to-pablito mode`
   - для финальной acceptance, release gate и окончательного merge verdict.

## Рабочий цикл

1. Перед началом проверь:
   - `/Users/pablito/Antigravity_AGENTS/Краб/Prepare Next Account Session.command`
   - `/Users/pablito/Antigravity_AGENTS/Краб/Check New Account Readiness.command`
   - `/Users/pablito/Antigravity_AGENTS/Краб/Check Current Account Runtime.command`
   - `/Users/pablito/Antigravity_AGENTS/Краб/Check Shared Repo Drift.command`, если есть сомнение по ветке или правам.
2. Определи режим работы: `code-only`, `controlled live`, `return-to-pablito`.
3. Если текущая учётка не владеет runtime на `:8080/:18789`, не запускай второй runtime поверх первого.
4. После каждого существенного изменения собери attach-ready handoff или минимум truthful summary с branch, owner и verification.
5. Если live acceptance выполнена не на `pablito`, явно пометь её как helper-account evidence, а не final release verdict.

## Красные флаги

- Нельзя писать в `~/.openclaw` другой учётки.
- Нельзя шарить OAuth/browser/MCP state между `USER2`, `USER3` и `pablito`.
- Нельзя считать helper-account smoke полноценным релизным подтверждением.
- Нельзя лечить конфликт массовым `chown` или общим reset по всему shared repo.

## Рекомендуемые связки с другими skills

- `krab-runtime-snapshot-handoff` перед switchover и после live цикла.
- `krab-workspace-truth-sync` для проверки, что docs и repo не врут относительно runtime.
- `krab-parallel-agent-orchestrator`, если несколько агентов работают одновременно из разных диалогов.

## Ресурсы

- Матрица режимов и ownership: `references/account-mode-matrix.md`
- Чеклист перехода между учётками: `assets/multi-account-session-checklist.md`
