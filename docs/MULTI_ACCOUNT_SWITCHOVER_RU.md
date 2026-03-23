# Multi-Account Switchover RU

## Каноническая схема

- shared: `/Users/Shared/Antigravity_AGENTS/Краб`
- split per-account: `~/.openclaw`, OAuth, browser profiles, secrets, PID/state files

Это обязательное правило для разработки на одном Mac с нескольких macOS-учёток.

## Матрица владения и прав

### Что разрешено шарить

- общий git-репозиторий;
- `docs/`;
- `artifacts/`;
- `.command`-launcher'ы и repo-level scripts;
- test fixtures и repo-level temp-артефакты, если они не привязаны к конкретному `HOME`.

### Что запрещено шарить

- `~/.openclaw/*`;
- OAuth/session/login state любого провайдера;
- browser profile, remote-debugging state, MCP attach state;
- `.env` и любые account-local secrets;
- runtime lock/PID/socket/state files, если они указывают на конкретный `HOME`.

### Кто владелец какого контура

- `pablito`: live runtime truth, финальный release gate, финальная acceptance, финальный merge/handoff verdict.
- `USER2` / `USER3` и другие вспомогательные macOS-учётки: код, тесты, документация, handoff bundle, controlled bootstrap/relogin, ограниченные live-циклы только после reclaim/freeze.
- другие нейронки в новых чатах: обязаны считать каноном только bundle + `MASTER_PLAN_VNEXT_RU.md` + этот runbook, а не старые проценты и не память прошлого диалога.

## Практика прав файлов в shared repo

- shared repo должен быть доступен на чтение и запись всем рабочим macOS-учёткам, которые реально участвуют в разработке;
- account-local артефакты нельзя писать в shared repo, если они потом блокируют вторую учётку из-за owner-only прав;
- если helper, лог или generated-файл нужен только одной учётке, он должен жить в её `HOME`, а не в shared path;
- если в shared repo появился файл, который другая учётка не может редактировать, чинить нужно только права на repo-файл, а не трогать чужой `~/.openclaw`;
- нельзя массово менять владельца всего дерева без понимания последствий; правим только конкретный shared path, который реально мешает работе.

## Operating protocol для человека

1. Перед началом сессии убедиться, что работа идёт либо в shared repo, либо в явно временной копии, но не в двух боевых копиях одновременно.
2. Перед live-циклом понять, какая учётка сейчас владеет runtime и портами `:8080/:18789`.
3. Если runtime принадлежит не текущей учётке, сначала выполнить reclaim/freeze, а не запускать второй runtime поверх первого.
4. После каждого существенного изменения обновлять bundle или минимум `ATTACH_SUMMARY_RU.md`-эквивалент через export.
5. Если shared repo отстал от текущей рабочей копии, сначала осознанно синхронизировать ветку/HEAD, а не начинать новый coding loop в drift-состоянии.

## Operating protocol для других нейронок

1. Не считать `shared repo` и `current runtime owner` одним и тем же фактом; это разные истины.
2. Не использовать чужой `HOME` как рабочую среду и не писать в `~/.openclaw` другой учётки.
3. Не запускать live runtime, если `Check New Account Readiness.command` или runtime snapshot показывают чужой active owner.
4. Не исправлять конфликты через агрессивные reset/chown по всему дереву.
5. Не доверять старым handoff-фразам, если свежий bundle показывает другой branch/HEAD или другой shared-repo drift.
6. После правок в docs/runtime workflow обновлять handoff export, чтобы следующий агент видел ту же operational truth.

## Базовый режим работы

### Основная учётка `pablito`

- live runtime truth;
- release-safe acceptance;
- финальные smoke/release/handoff циклы.

### Другие учётки

- основной coding loop;
- unit/integration tests;
- документация;
- controlled live-проверки только после `check -> freeze/reclaim -> bootstrap`.
- `USER2` и `USER3` считаются равноправными вспомогательными coding-контурами:
  нельзя вшивать в docs/process предположение, что worktree или handoff пригодны
  только для одной из них.

## Safe workflow без конфликтов

1. На текущей учётке перед переходом запустить `Prepare Next Account Session.command`.
2. На другой учётке открыть `/Users/Shared/Antigravity_AGENTS/Краб-active`, если нужен быстрый coding loop без legacy shared drift.
3. Запустить `Check New Account Readiness.command`.
4. Если есть сомнение по ветке/HEAD или правам записи в shared path, отдельно запустить `Check Shared Repo Drift.command`.
5. Если нужно разобрать старый drift между `pablito` и legacy shared repo, запустить `Prepare Shared Repo Switchover.command` и прочитать `artifacts/ops/shared_repo_switchover_latest.md`.
6. Если live runtime сейчас принадлежит другой учётке, не запускать локальный start.
7. Работать в code/doc/test режиме без runtime, если live acceptance не нужен.
8. Перед live-циклом:
   - зафиксировать handoff;
   - остановить чужой runtime из исходной учётки;
   - выполнить bootstrap/relogin в текущем HOME;
   - только потом запускать `Start Full Ecosystem.command` или `start_krab.command`.
9. После live-работы:
   - сохранить артефакты;
   - выполнить freeze/reclaim сценарий;
   - обновить handoff bundle.

## User-friendly launchers

- `Runtime Switch Status.command` — безопасный status assistant без побочных эффектов.
- `Prepare Next Account Session.command` — одним запуском собирает readiness, switchover, `Краб-active`, runtime status и свежий handoff bundle.
- `Switch Runtime To Current Account.command` — поднимает runtime только если foreign runtime не найден.
- `Freeze Current Account Runtime.command` — останавливает runtime текущей учётки и сразу собирает fresh handoff/switchover артефакты.
- `Return Runtime To Pablito.command` — reclaim helper для основной учётки `pablito`.
- `Prepare Shared Repo Switchover.command` — готовит merge/switchover report и patch-артефакты до запуска runtime.
- `Publish Active Shared Worktree.command` — публикует быстрый coding-root `/Users/Shared/Antigravity_AGENTS/Краб-active` из текущего WIP.
- `Open Active Shared Worktree.command` — открывает `Краб-active` в Finder без ручной навигации.
- `Open Latest Handoff Bundle.command` — открывает свежую attach-ready handoff-папку в Finder.

## Что нельзя делать

- нельзя шарить `auth-profiles.json` между учётками как общий truth;
- нельзя поднимать runtime на второй учётке, если первая всё ещё держит `:8080/:18789`;
- нельзя считать quota-arbitrage полноценной архитектурой проекта;
- нельзя продолжать live-работу, если `Check Current Account Runtime.command` показывает чужой `HOME` или `inbox_state`.
- нельзя менять owner/permissions у всего shared repo только ради одного проблемного файла;
- нельзя писать account-local OAuth/runtime state в `/Users/Shared/Antigravity_AGENTS/Краб`;
- нельзя для merge/release опираться на acceptance другой учётки без возврата на `pablito`.

## Какие документы открыть на новой учётке

1. `docs/MASTER_PLAN_VNEXT_RU.md`
2. `docs/handoff/MASTER_PLAN_SOURCE_OF_TRUTH.md`
3. `docs/NEXT_CHAT_CHECKPOINT_RU.md`
4. `docs/NEW_CHAT_BOOTSTRAP_PROMPT.md`
5. `docs/CALL_TRANSLATOR_AUDIT_RU.md`
6. свежий `artifacts/handoff_<timestamp>/`

## Минимальный attach-ready пакет

- `runtime_snapshot.json`
- `HANDOFF_MANIFEST.json`
- `ATTACH_SUMMARY_RU.md`
- `START_NEXT_CHAT.md`
- `MASTER_PLAN_VNEXT_RU.md`
- `MULTI_ACCOUNT_SWITCHOVER_RU.md`
- `CALL_TRANSLATOR_AUDIT_RU.md`

## Требование к handoff для USER2 / USER3

- В каждом свежем handoff должно быть явно написано:
  - какая ветка является рабочей;
  - где лежит shared repo;
  - какие launcher/runtime fix'ы уже перенесены в repo, а какие ещё account-local;
  - какие live-smoke результаты подтверждены именно в текущей учётке, а какие
    относятся к `pablito`.
- Если handoff этого не содержит, перед переключением учётки нужно сначала
  обновить docs, а уже потом переносить работу дальше.
