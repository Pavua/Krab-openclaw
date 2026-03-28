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
- `dashboardStats`

На старте вкладки вызывается `applyOwnerPanelBootstrapCache()` до `refreshAll()`.

Дополнительно owner panel теперь не перетирает уже поднятый cache в `ERR/FAIL`, если live fetch временно недоступен.
Это касается `openclawCatalog`, `openclawAutoswitch`, `openclawChannels` и `localLifecycle`.

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

### Cache-only acceptance

1. В `localStorage` предзаписан `krab:owner-panel-bootstrap:v1` с truthful snapshot.
2. Все запросы к `/api/*` принудительно оборваны.
3. Открыта owner panel.
4. Проверен первый кадр до какого-либо успешного network roundtrip.

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

### First paint при полностью недоступных `/api/*`

- `degradation = stable`
- `bb_total = 12`
- `rag_total = 34`
- `local_model = LM Studio idle`
- `recommended_model = codex-cli/gpt-5.4`
- `rtChannelState = LOCAL`
- `rtBreakerState = CLOSED`
- `checks = owner-ui cache`
- `ocForceMode = auto`
- `ocAutoswitchStatus = OK`
- `ocVoiceDeliveryValue = text+voice`
- `ocLocalLifecycleStatus = WARN`
- `ocMeta = ⚠️ Ошибка [Local Status]: Failed to fetch`

Ключевой инвариант: даже при полностью сломанной live-сети first-paint больше не деградирует обратно в пустые `—` по high-value карточкам.

## Verdict

- Fixed: ключевые owner runtime-блоки больше не возвращаются в пустые `—` на cold reload.
- Fixed: transient fetch-failure больше не стирает уже применённый last-good cache в `ERR/FAIL` поверх первого кадра.
- Intentionally unchanged: `Browser / MCP Readiness` остаётся в честном `LOADING`, потому что это volatile probe и fake cached-ready здесь хуже пустоты.

## Артефакты

- Screenshot: `/Users/pablito/Antigravity_AGENTS/Краб/output/playwright/owner-panel-cached-first-paint-20260328-1514.png`
- Screenshot (cache-only acceptance): `/Users/pablito/Antigravity_AGENTS/Краб/output/playwright/owner-panel-cache-first-paint-20260328-1619.png`
