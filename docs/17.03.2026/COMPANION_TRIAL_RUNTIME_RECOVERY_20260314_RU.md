# Companion Trial Runtime Recovery (2026-03-14)

Этот документ фиксирует отдельный шаг по ветке `codex/companion-runtime-adaptive-fix`.

## Что было сломано

- В `USER3` runtime OpenClaw перестал поднимать `:18789` после reload.
- Причина: в `~/.openclaw/openclaw.json` остался legacy `agents.defaults.thinkingDefault = auto`,
  а OpenClaw `2026.3.11` уже принимает `adaptive` вместо `auto`.

## Что сделано

1. В owner runtime-controls/backend добавлена нормализация `auto -> adaptive`.
2. В owner UI обновлён список thinking-режимов под OpenClaw `2026.3.11`.
3. В `USER3` runtime-config выполнен точечный repair `thinkingDefault=adaptive` с backup и ops-артефактом.
4. Выполнен controlled restart через `new Stop Krab.command` + `Start Full Ecosystem.command`.
5. Через owner panel выполнен клик `Подготовить companion trial`.

## Что подтверждено

- `Check Current Account Runtime.command` теперь показывает `:8080/:18789/:8090` = OK для `USER3`.
- `/api/health` показывает `openclaw = true`, `voice_gateway = true`, `krab_ear = true`.
- Owner panel после trial-prep показывает:
  - `iPhone companion = BOUND`
  - `Delivery matrix = TRIAL READY`
  - `Live trial preflight = READY FOR TRIAL`
- Gateway source-of-truth подтверждает:
  - `session_id = vs_0b93dc247b1d`
  - `device_id = iphone-dev-1`
  - `active_session = true`
  - `bound_session_id = vs_0b93dc247b1d`
- Важная оговорка: `current device binding status = pending`, пока реальный iPhone ещё не подключил live audio/session stream.

## Артефакты

- Runtime repair: `/Users/Shared/Antigravity_AGENTS/Краб/artifacts/ops/openclaw_runtime_thinking_alias_fix_user3_latest.json`
- Trial-ready snapshot: `/Users/Shared/Antigravity_AGENTS/Краб/artifacts/ops/translator_mobile_trial_ready_user3_latest.json`
- Owner panel screenshot: `/Users/Shared/Antigravity_AGENTS/Краб/output/playwright/translator-mobile-trial-ready-user3-20260314.png`
- Handoff copy: `/Users/Shared/Antigravity_AGENTS/Краб/artifacts/handoff_20260314_183113/krab-owner-panel-companion-trial-ready-2026-03-14.png`

## Следующий шаг

- Пройти реальный `Xcode Free Signing` на iPhone.
- В приложении проверить `Health-check` до `http://<IP Mac>:8090`.
- Зафиксировать first live subtitles/timeline на устройстве.
