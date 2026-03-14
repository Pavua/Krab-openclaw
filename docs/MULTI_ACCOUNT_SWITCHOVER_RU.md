# Multi-Account Switchover для Краба на одном Mac

Дата фиксации: `2026-03-12`

Этот документ описывает, как безопасно продолжать разработку Краба:

- на том же MacBook;
- но из другой macOS-учётки;
- и при этом не ломать текущий runtime, auth и browser state.

## Каноническое решение

Для multi-account на одном Mac принимается:

- `shared repo/docs/artifacts`
- `split runtime/auth/secrets/browser state per macOS account`

Это новый default для проекта.

### Канонический shared repo path

С 2026-03-13 канонической общей рабочей копией считается:

- `/Users/Shared/Antigravity_AGENTS/Краб`

Старая копия в `/Users/pablito/Antigravity_AGENTS/Краб` может ещё существовать как переходный хвост,
но для повседневой разработки, запуска `.command`-файлов и открытия проекта в Codex/IDE нужно использовать именно shared path.

## Что можно шарить между учётками

Разрешено шарить:

- сам репозиторий `/Users/Shared/Antigravity_AGENTS/Краб`
- документацию в `docs/`
- handoff bundle в `artifacts/`
- acceptance artifacts и отчёты
- утверждённые roadmap/master-plan документы

## Что нельзя шарить напрямую

Каждая macOS-учётка должна иметь свой отдельный слой для:

- `~/.openclaw/*`
- OAuth/auth profiles
- browser profiles
- runtime PID/state files
- secrets и `.env`-связанные локальные значения
- traces/approvals

Это нужно потому, что:

- у учёток могут быть разные квоты Codex/OpenAI/Google;
- нельзя допустить взаимное перетирание auth;
- browser attach/readiness должен отражать именно активную учётку;
- handoff должен быть truthful, а не смешанным.

## Как продолжать разработку в соседней учётке

### Минимальный порядок

1. Открыть тот же репозиторий.
2. Проверить, что runtime и `~/.openclaw` принадлежат текущей macOS-учётке.
3. Прочитать handoff bundle:
   - `START_NEXT_CHAT.md`
   - `ATTACH_SUMMARY_RU.md`
   - `HANDOFF_MANIFEST.json`
   - `MASTER_PLAN_VNEXT_RU.md`
   - `CALL_TRANSLATOR_AUDIT_RU.md`
4. Запустить readiness-проверку для текущей учётки.
5. Только после этого продолжать мутирующие изменения.

### Режим разработки без runtime (рекомендуемый по умолчанию)

Этот режим безопасен для долгой работы в другой учётке и не трогает `pablito`:

1. Проверить `Check Current Account Runtime.command` и убедиться, что `:8080/:18789` не слушают.
2. Работать с кодом, тестами, документацией и scripts без запуска runtime.
3. Не запускать `new start_krab.command` и `Start Full Ecosystem.command`, если на `pablito` живой runtime.

Почему так:
- порты `:8080` и `:18789` общие для машины;
- старт‑скрипт очищает порт `:8080` и может убить чужой runtime, даже если он от другой учётки.

### Когда можно поднимать runtime в соседней учётке

Поднимать runtime здесь можно, если действительно нужен live‑UI/acceptance на этой учётке.
Чтобы не сломать `pablito`, делаем так:

1. На `pablito` остановить runtime (если он жив).
2. На соседней учётке выполнить `Bootstrap Current Account OpenClaw.command`.
3. Пройти нужные `Login *.command` (OAuth/keys) именно в этой учётке.
4. Запустить `new start_krab.command` и сделать проверки.
5. После работы — `new Stop Krab.command` (или `Stop Full Ecosystem.command`).

Важно:
- `Voice Gateway` может быть запущен из другой учётки; не останавливай его из соседней,
  если он нужен `pablito`.

## Практический one-click набор

Для повседневного переключения между учётками в репозитории есть такой минимальный набор:

- [Check Current Account Runtime.command](/Users/pablito/Antigravity_AGENTS/Краб/Check%20Current%20Account%20Runtime.command)
  - показывает, кому реально принадлежат `:8080`, `:18789`, `:8090` и чей `inbox_state` сейчас светится в health.
- [Freeze USER2 Runtime.command](/Users/pablito/Antigravity_AGENTS/Краб/Freeze%20USER2%20Runtime.command)
  - запускать из `USER2`, когда разработка с квотной учётки закончена и нужно отдать live runtime обратно.
- [Reclaim Runtime For Pablito.command](/Users/pablito/Antigravity_AGENTS/Краб/Reclaim%20Runtime%20For%20Pablito.command)
  - запускать из `pablito` после возврата на основную учётку.
  - helper не должен верить просто живому `health`; он обязан отбрасывать чужой runtime по `listener owner` и `inbox_state`.
  - после controlled restart helper пишет `artifacts/ops/pablito_return_preflight_latest.json` и оставляет на `pablito` только relogin + release gate.
- [docs/PABLITO_RETURN_CHECKLIST_RU.md](/Users/pablito/Antigravity_AGENTS/Краб/docs/PABLITO_RETURN_CHECKLIST_RU.md)
  - короткий checklist именно для финального возврата на основную учётку.

## Что обязательно должно подтверждаться перед работой

Нужно подтвердить:

- кто текущий `operator/account`;
- какой `HOME`;
- какой `~/.openclaw`;
- какой runtime API доступен;
- какой Python/venv используется;
- не унаследован ли чужой browser profile;
- живы ли `:8080`, `:18789`, `:8090`, если они вообще должны быть подняты.

## Возврат на эту учётку

При возврате на текущую учётку нельзя считать состояние “само собой восстановленным”.

Нужно:

1. снова снять truthful runtime snapshot;
2. проверить runtime profile текущей учётки;
3. перепроверить auth/runtime truth;
4. только потом продолжать live/smoke/release проверки.

Практически это значит:

1. На уходящей учётке нажать `Freeze USER2 Runtime.command`.
2. На возвращающейся учётке нажать `Check Current Account Runtime.command`.
3. Затем выполнить `Reclaim Runtime For Pablito.command`.
4. Пройти только account-local relogin, который helper покажет в owner panel.
5. После этого запускать `Release Gate.command` как финальный verdict.

## Как оформлять handoff между учётками

Handoff должен опираться на артефакты, а не на пересказ.

Минимальный пакет:

- `runtime_snapshot.json`
- `HANDOFF_MANIFEST.json`
- `ATTACH_SUMMARY_RU.md`
- `START_NEXT_CHAT.md`
- `MASTER_PLAN_VNEXT_RU.md`
- `CALL_TRANSLATOR_AUDIT_RU.md`
- `PARALLEL_DIALOG_PROTOCOL_RU.md`

## Важное правило

Если в соседней учётке будет вестись отдельный диалог Codex, эта учётка должна:

- видеть тот же repo и docs;
- но работать со своим runtime/auth/secrets контуром.

Именно это позволяет параллельную разработку без опасного пересечения состояний.
