<!--
Что это:
Truthful evidence по owner panel после добавления last-good bootstrap cache.

Зачем:
Подтверждает, что cold reload больше не возвращает ключевые runtime-карточки в пустые `—`,
а volatile Browser/MCP-блок остаётся в честном `LOADING`.
-->

# Owner Panel Cached First Paint — 2026-03-28

## Изменение

В `/Users/pablito/Antigravity_AGENTS/Краб/src/web/index.html` добавлен client-side bootstrap cache в `localStorage`:

- `krab:owner-panel-bootstrap:v1`
- last-good sections:
  - `openclawCatalog`
  - `openclawAutoswitch`
  - `openclawChannels`
  - `runtimeConfig`
  - `controlCompat`
  - `effectiveRouting`
  - `voiceRuntime`
  - `translatorBootstrap`
  - `cronStatus`
  - `localLifecycle`

На старте вкладки вызывается `applyOwnerPanelBootstrapCache()` до `refreshAll()`.

## Проверка

### Static

- `node --check /tmp/krab_index_script.js`
- `./venv/bin/pytest -q tests/unit/test_web_panel_bootstrap_order.py tests/unit/test_web_app_runtime_endpoints.py -q`

Результат:

- `138 passed, 1 warning`

### Live UI

1. Открыта owner panel `http://127.0.0.1:8080/`
2. Дождались одного полного live refresh, чтобы новый код заселил cache.
3. Выполнен reload страницы.
4. Сразу после reload проверены ключевые поля через DevTools MCP.

## Live evidence

### Before reload

- `ocForceMode = auto`
- `ocLocalModel = idle`
- `ocCloudSlots = 1 active`
- `ocAutoswitchStatus = OK`
- `ocCompatRuntime = OK`
- `ocrouteReq = auto`
- `ocVoiceEnabledBadge = ON`
- `ocTranslatorReadinessBadge = READY`
- `ocCronSchedulerBadge = ACTIVE`
- `ocLocalLifecycleStatus = WARN`

### First paint after second cold reload

- `ocForceMode = auto`
- `ocLocalModel = idle`
- `ocCloudSlots = 1 active`
- `ocAutoswitchStatus = OK`
- `ocLastSwitch = 2026-03-12T00:13:33+00:00`
- `ocSwitchReason = profile_applied`
- `ocCompatRuntime = OK`
- `ocCompatWarn = Нет`
- `ocrouteReq = auto`
- `ocrouteEff = auto`
- `ocVoiceEnabledBadge = ON`
- `ocVoiceDeliveryValue = text+voice`
- `ocVoiceSpeedValue = 1.50x`
- `ocTranslatorReadinessBadge = READY`
- `ocTranslatorAccountValue = pablito / yung_nagato / split_runtime_per_account`
- `ocTranslatorRouteValue = codex-cli/gpt-5.4 (openclaw_cloud)`
- `ocCronSchedulerBadge = ACTIVE`
- `ocCronJobsValue = 0 active / 4 paused / 4 total`
- `ocLocalLifecycleStatus = WARN`
- `ocBrowserStage = LOADING`

## Verdict

- Fixed: ключевые owner runtime-блоки больше не возвращаются в пустые `—` на cold reload.
- Intentionally unchanged: `Browser / MCP Readiness` остаётся в честном `LOADING`, потому что это volatile probe и fake cached-ready здесь хуже пустоты.

## Артефакты

- Screenshot: `/Users/pablito/Antigravity_AGENTS/Краб/output/playwright/owner-panel-cached-first-paint-20260328-1514.png`
