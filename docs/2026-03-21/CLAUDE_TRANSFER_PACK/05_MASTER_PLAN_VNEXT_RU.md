# Master Plan vNext RU

## Канонический приоритет

1. `Stability Kernel`
2. `Ordinary Call Translator`
3. `Capability / Proactivity Expansion`
4. `Monetization Layer`
5. `Product Teams / Swarm / Controlled Autonomy`

## Что является целью проекта

Краб развивается как owner-first экосистема вокруг `OpenClaw + Telegram userbot + owner panel :8080 + Voice Gateway + Krab Ear`.

Главная цель на текущем цикле:

- сделать OpenClaw максимально стабильным, предсказуемым и прозрачным;
- довести ordinary-call translator до личного daily-use сценария;
- затем расширять capability surface и проактивность;
- денежные контуры запускать только после stable core.

## Ближние execution-фазы

### Фаза 0. Truth Reset

- единый source-of-truth для roadmap, handoff и export bundle;
- канонический shared repo path: `/Users/Shared/Antigravity_AGENTS/Краб`;
- явные runtime modes: `personal-runtime`, `release-safe-runtime`, `lab-runtime`.

### Фаза 1. OpenClaw Stability Kernel

- truthful routing/auth/restart/panel states;
- identity envelope: `operator_id`, `account_id`, `channel_id`, `team_id`, `trace_id`, `approval_scope`;
- multi-account one-click flows без конфликтов между учётками;
- FinOps и cost/routing telemetry;
- event-driven runtime checks вместо heartbeat ради heartbeat.

### Фаза 2. Channel Reliability и Proactive Core

- parity между `userbot`, `reserve bot`, owner panel и related owner-facing каналами;
- `Inbox / Escalation` как центральная шина проактивности;
- deterministic memory / handoff discipline;
- owner-visible trace для любых proactive actions.

### Фаза 3. System / Browser / Capability Expansion

- browser relay, MCP truth, окна, приложения, clipboard, screenshots/OCR, файловые операции, notifications;
- все capability подключаются только через `Capability Registry + Policy Matrix`;
- сначала закрываются owner-critical и translator-critical системные действия.

### Фаза 4. Multimodal + Voice Foundation

- `Krab Ear` как deterministic ingest/STT/perception pipeline;
- `Krab Voice Gateway` как реальный control-plane;
- никаких stub-статусов в translator readiness/onboarding/preflight;
- чёткое разделение `ordinary-call` и `internet-call` треков.

### Фаза 5. Ordinary Call Translator MVP

- iPhone companion для обычных звонков;
- subtitles/timeline/summary/why-report/owner escalation;
- delivery path: `Xcode free signing` + `AltStore/SideStore`;
- paid Apple Developer не является prerequisite.

### Фаза 6. Translator Daily-Use Hardening

- repeated-call stability;
- session recovery и restart recovery;
- route fallback и diagnostics;
- понятный personal runbook для использования в реальной жизни.

## Поздние фазы

### Фаза 7. Monetization Layer

- сначала service automation и recurring value workflows;
- `Trading Lab` только как поздний `paper -> shadow -> gated real` трек;
- любые денежные действия только с caps, audit trail и owner approvals.

### Фаза 8. Product Teams / Swarm / Controlled Autonomy

- `Swarm v2` только после зрелости identity/inbox/artifacts/cost telemetry;
- потом `coding`, `research`, `content`, `life`, `shopping`;
- controlled autonomy только после KPI, kill-switch и доказанной дисциплины.

## Acceptance gates

- Фаза 1: `10` controlled restart cycles, `50` owner round-trips без silent-drop, `3` freeze/reclaim multi-account цикла.
- Фаза 4: translator surfaces больше не живут на fake/stub-слое.
- Фаза 5: `10` реальных ordinary-call сессий на `ES<->RU`, `ES<->EN`, `EN->RU`.
- Фаза 6: translator реально пригоден для личного регулярного использования без постоянного ручного шаманства.

## Проценты и baseline

- Общий baseline проекта: `~31%`.
- Старые `52/68/77%` не использовать как общий project progress.
- Любое обновление процентов сначала проходит через
  [docs/handoff/MASTER_PLAN_SOURCE_OF_TRUTH.md](/Users/pablito/Antigravity_AGENTS/Краб/docs/handoff/MASTER_PLAN_SOURCE_OF_TRUTH.md).

## Важные defaults

- `Stability > ширина`.
- `Translator > monetization`.
- aggressive subscription use допускается в `personal-runtime`, но не как единственная опора `release-safe-runtime`.
- repo/docs/artifacts shared между учётками, runtime/auth/browser/secrets — строго per-account.
