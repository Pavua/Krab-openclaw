---
name: krab-runtime-change-impact-auditor
description: "Оценивать, какие runtime, smoke, release, handoff и multi-account контуры проекта `/Users/pablito/Antigravity_AGENTS/Краб` потенциально затронет планируемая или уже внесённая правка, чтобы не запускать лишние проверки и не пропускать критичные. Использовать перед изменением backend/runtime/UI/transport кода, перед review fix-pass и перед итоговым release verdict."
---

# Krab Runtime Change Impact Auditor

Используй этот навык до внесения правки или сразу после неё, когда нужно быстро понять радиус воздействия.

Он нужен, чтобы не гадать по памяти, какие smoke, acceptance, release и handoff слои обязательно затронет изменение.

## Что анализировать

- какие файлы и подсистемы меняются;
- это UI-only, runtime-risk или transport-risk;
- затрагивает ли правка owner panel `:8080`, gateway `:18789`, routing, auth, Telegram transport, browser relay или handoff truth;
- нужна ли helper-only проверка или уже финальный `pablito` verdict.

## Базовая карта воздействия

- `src/modules/web_app.py`, owner panel endpoints, frontend owner UI:
  - UI smoke;
  - runtime endpoint truth-check;
  - иногда release gate, если endpoint используется в операционных циклах.
- runtime/auth/models/openclaw config glue:
  - runtime doctor / auth/model checks;
  - release gate;
  - multi-account boundary review.
- Telegram/userbot/reserve bot/transport:
  - Telegram regression pack;
  - live channel smoke;
  - release verdict, если transport менялся заметно.
- browser relay / MCP / photo/browser readiness:
  - owner UI smoke;
  - browser/MCP readiness checks;
  - acceptance только если менялся живой browser-контур.
- docs/handoff/evidence only:
  - freshness audit;
  - acceptance brief;
  - без лишнего live smoke, если код не тронут.

## Рабочий цикл

1. Назови изменённую область и список файлов.
2. Определи impact level:
   - `docs-only`
   - `ui-only`
   - `runtime-risk`
   - `transport-risk`
   - `release-critical`
3. Для каждой правки выдай:
   - какие проверки обязательны;
   - какие проверки advisory;
   - требуется ли live runtime ownership;
   - можно ли делать это на helper-account.
4. Если impact затрагивает несколько слоёв, подключи role split и branch discipline до начала fix-pass.
5. После выполнения проверок не повышай verdict выше реально подтверждённого уровня.

## Красные флаги

- гонять полный live/release цикл на чисто docs-only правке;
- ограничиться unit/UI smoke, если тронут transport или runtime routing;
- считать helper-account acceptance финальным release verdict по критичной правке;
- забывать обновить handoff/evidence после изменения release-critical зоны.

## Рекомендуемые связки с другими skills

- `krab-agent-request-router` для выбора стартового skill-stack.
- `krab-pr-review-triager`, если impact оценивается на review fix-pass.
- `krab-release-readiness-pack`, если правка дошла до merge/release этапа.
- `krab-artifact-freshness-auditor`, если verdict опирается на старые артефакты.

## Ресурсы

- Матрица воздействия по зонам: `references/change-impact-matrix.md`
- Шаблон impact note: `assets/change-impact-note-template.md`
