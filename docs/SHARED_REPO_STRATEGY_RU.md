# Shared Repo Strategy RU

Этот документ фиксирует каноническую стратегию работы с `/Users/Shared/Antigravity_AGENTS/Краб`
и с локальной копией `pablito`, когда разработка идёт с нескольких macOS-учёток.

## Главная идея

У нас есть две разные истины:

- `runtime truth` — кто сейчас реально владеет live runtime, портами, `~/.openclaw`, browser/MCP attach;
- `repo truth` — где сейчас находится актуальный WIP по коду и документации.

Эти истины нельзя смешивать.

## Какой режим считать правильным

Нормальный steady-state для проекта:

- shared repo становится основной общей рабочей копией для кода, docs и artifacts;
- каждая учётка использует свой `HOME`, свой `~/.openclaw`, свой auth/browser state;
- `pablito` остаётся владельцем финального release-safe acceptance и финального merge verdict.

Переходный режим допустим:

- `pablito` временно ведёт WIP в своей локальной копии;
- shared repo отстаёт или расходится;
- published fast-path `Краб-active` используется как временный общий coding-root;
- в этом режиме нельзя вести бездумный coding loop из обеих копий одновременно.

## Стратегия переключения между учётками

### Если нужен только код / docs / tests

1. Не поднимать runtime.
2. Работать только в одной git-копии за раз.
3. Перед переходом на другую учётку запустить:
   - `Prepare Next Account Session.command`
   - `Check New Account Readiness.command`
   - `Check Shared Repo Drift.command`
   - `Prepare Shared Repo Switchover.command`
4. Если legacy shared repo ещё не reconciled, открываем `/Users/Shared/Antigravity_AGENTS/Краб-active` и продолжаем из него.
5. Если `strategy = shared_repo_ready`, можно просто продолжать из shared repo.
6. Если `strategy = carry_pablito_wip_to_shared`, сначала переносим patch, потом продолжаем.
7. Если `strategy = manual_merge_required`, сначала разбираем overlap-файлы и только потом продолжаем coding loop.

### Если нужен live runtime / owner panel / release gate

1. Определить текущего owner runtime.
2. Если runtime сейчас не у этой учётки, сначала сделать controlled freeze/reclaim.
3. Только после этого делать bootstrap/login и старт runtime.
4. После live-цикла остановить runtime и вернуть ownership основной учётке, если это `pablito`.

## Как трактовать shared repo

Shared repo — это:

- место для общего кода;
- место для общей документации;
- место для handoff и acceptance artifacts.

Fast-path `Краб-active` — это:

- одноразово публикуемая shared-копия текущего WIP;
- временный общий coding-root для другой учётки;
- не замена per-account runtime/auth/browser state.

Shared repo — это не:

- место для account-local OAuth;
- место для `~/.openclaw`;
- место для runtime lock/state;
- место для живых browser profiles.

## Что нельзя синхронизировать как обычный код

- `data/scheduler/reminders.json` — mutable runtime-подобный файл, его нельзя считать источником истины для multi-account merge;
- `temp/runtime_snapshot.json` — диагностический хвост, не канонический merge-object;
- любые account-local generated lock/state/log файлы.

## Канон для людей и агентов

- все новые агенты и новые чаты должны сначала читать:
  - `docs/MULTI_ACCOUNT_SWITCHOVER_RU.md`
  - `docs/SHARED_REPO_STRATEGY_RU.md`
  - `artifacts/ops/shared_repo_switchover_latest.md`
- если эти документы противоречат старому handoff-тексту, верить нужно им, а не старому диалогу.
