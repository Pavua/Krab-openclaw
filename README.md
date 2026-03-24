# Krab / OpenClaw

Krab — это owner-first экосистема вокруг `Telegram userbot + OpenClaw + owner panel :8080 + Voice Gateway + Krab Ear`, а не просто Telegram-бот.

## Текущий приоритет

1. `OpenClaw Stability Kernel`
2. `Ordinary Call Translator`
3. `Capability / Proactivity Expansion`
4. `Monetization Layer`
5. `Product Teams / Swarm / Controlled Autonomy`

## Что читать первым

1. [AGENTS.md](/Users/pablito/Antigravity_AGENTS/Краб/AGENTS.md)
2. [docs/MASTER_PLAN_VNEXT_RU.md](/Users/pablito/Antigravity_AGENTS/Краб/docs/MASTER_PLAN_VNEXT_RU.md)
3. [docs/handoff/MASTER_PLAN_SOURCE_OF_TRUTH.md](/Users/pablito/Antigravity_AGENTS/Краб/docs/handoff/MASTER_PLAN_SOURCE_OF_TRUTH.md)
4. [docs/MULTI_ACCOUNT_SWITCHOVER_RU.md](/Users/pablito/Antigravity_AGENTS/Краб/docs/MULTI_ACCOUNT_SWITCHOVER_RU.md)
5. [docs/SHARED_REPO_STRATEGY_RU.md](/Users/pablito/Antigravity_AGENTS/Краб/docs/SHARED_REPO_STRATEGY_RU.md)

## Канонический рабочий путь

Для multi-account разработки на одном Mac общей рабочей копией считается:

- `/Users/Shared/Antigravity_AGENTS/Краб`

Практический fast-path на текущем этапе, пока legacy shared repo ещё не reconciled:

- публиковать готовую рабочую копию в `/Users/Shared/Antigravity_AGENTS/Краб-active`
- через `Publish Active Shared Worktree.command`
- и уже её открывать с других учёток для продолжения текущего WIP
- самый удобный orchestration path перед уходом на другую учётку: `Prepare Next Account Session.command`

Shared между учётками:

- сам repo;
- `docs/`;
- `artifacts/`.

Строго per-account:

- `~/.openclaw`;
- OAuth/auth profiles;
- browser state;
- secrets и `.env`;
- runtime PID/state files.

Правила владения и прав для людей и других агентов:

- owner этого протокола: [docs/MULTI_ACCOUNT_SWITCHOVER_RU.md](/Users/pablito/Antigravity_AGENTS/Краб/docs/MULTI_ACCOUNT_SWITCHOVER_RU.md)
- не писать account-local auth/runtime/browser state в shared repo;
- не запускать второй live runtime поверх уже занятого `:8080/:18789`;
- не чинить конфликты массовым `chown/chmod` по всему дереву.

## One-click entrypoints

- [Start Full Ecosystem.command](/Users/pablito/Antigravity_AGENTS/Краб/Start%20Full%20Ecosystem.command)
- [Stop Full Ecosystem.command](/Users/pablito/Antigravity_AGENTS/Краб/Stop%20Full%20Ecosystem.command)
- [Check Full Ecosystem.command](/Users/pablito/Antigravity_AGENTS/Краб/Check%20Full%20Ecosystem.command)
- [Release Gate.command](/Users/pablito/Antigravity_AGENTS/Краб/Release%20Gate.command)
- [Export Handoff Bundle.command](/Users/pablito/Antigravity_AGENTS/Краб/Export%20Handoff%20Bundle.command)
- [Check New Account Readiness.command](/Users/pablito/Antigravity_AGENTS/Краб/Check%20New%20Account%20Readiness.command)
- [Check Shared Repo Drift.command](/Users/pablito/Antigravity_AGENTS/Краб/Check%20Shared%20Repo%20Drift.command)
- [Prepare Shared Repo Switchover.command](/Users/pablito/Antigravity_AGENTS/Краб/Prepare%20Shared%20Repo%20Switchover.command)
- [Prepare Next Account Session.command](/Users/pablito/Antigravity_AGENTS/Краб/Prepare%20Next%20Account%20Session.command)
- [Publish Active Shared Worktree.command](/Users/pablito/Antigravity_AGENTS/Краб/Publish%20Active%20Shared%20Worktree.command)
- [Open Active Shared Worktree.command](/Users/pablito/Antigravity_AGENTS/Краб/Open%20Active%20Shared%20Worktree.command)
- [Open Latest Handoff Bundle.command](/Users/pablito/Antigravity_AGENTS/Краб/Open%20Latest%20Handoff%20Bundle.command)
- [Runtime Switch Status.command](/Users/pablito/Antigravity_AGENTS/Краб/Runtime%20Switch%20Status.command)
- [Switch Runtime To Current Account.command](/Users/pablito/Antigravity_AGENTS/Краб/Switch%20Runtime%20To%20Current%20Account.command)
- [Freeze Current Account Runtime.command](/Users/pablito/Antigravity_AGENTS/Краб/Freeze%20Current%20Account%20Runtime.command)
- [Return Runtime To Pablito.command](/Users/pablito/Antigravity_AGENTS/Краб/Return%20Runtime%20To%20Pablito.command)
- [Build Translator Mobile Onboarding Packet.command](/Users/pablito/Antigravity_AGENTS/Краб/Build%20Translator%20Mobile%20Onboarding%20Packet.command)

## Runtime modes

- `personal-runtime` — допускает aggressive subscription use и personal-primary routing.
- `release-safe-runtime` — не должен зависеть от одного хрупкого подписочного primary.
- `lab-runtime` — для рискованных и экспериментальных сценариев.

## Handoff и новая учётка

Если нужно продолжить работу в новом чате или на другой учётке:

1. на текущей учётке запусти `Prepare Next Account Session.command`;
2. приложи свежую папку `artifacts/handoff_<timestamp>/`;
3. на новой учётке открой `/Users/Shared/Antigravity_AGENTS/Краб-active`, если нужен быстрый coding loop без legacy shared drift;
4. на новой учётке сначала запусти `Check New Account Readiness.command`;
5. если нужен live runtime, сначала проверь ownership через `Runtime Switch Status.command`;
6. только потом продолжай live/runtime работу.
