# Role Lanes

## Architect

- Определяет границы задачи, source-of-truth и merge-order.
- Подходит для roadmap, architecture docs, coordination notes и risk framing.

## Runtime Engineer

- Ведёт `src/`, transport, routing, userbot, provider logic и runtime-sensitive scripts.
- Часто комбинируется с:
  - `krab-runtime-doctor`
  - `krab-model-routing-ops`
  - `krab-userbot-acl-governor`

## UI Engineer

- Ведёт owner panel, browser flows, визуальные и DOM-проверки.
- Часто комбинируется с:
  - `krab-owner-ui-smoke`
  - `krab-owner-panel-runtime-ops`

## QA / Release

- Ведёт tests, smoke, evidence, release gate и handoff.
- Часто комбинируется с:
  - `krab-live-smoke-conductor`
  - `krab-release-gate-keeper`
  - `krab-acceptance-artifacts-curator`

## Docs / Artifacts lane

- Обновляет только подтверждённые факты после code и verification lanes.
- Часто комбинируется с:
  - `krab-docs-maintainer`
  - `krab-runtime-snapshot-handoff`
