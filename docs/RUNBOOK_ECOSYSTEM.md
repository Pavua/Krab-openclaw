<!--
Операционный runbook для экосистемы Krab + Krab Ear + Krab Voice Gateway.
-->

# Ecosystem Runbook

## Источники истины по запуску
1. Krab: `/Users/pablito/Antigravity_AGENTS/Краб/start_krab.command`
2. Krab Ear: `/Users/pablito/Antigravity_AGENTS/Krab Ear/Start Krab Ear.command`
3. Krab Voice Gateway: `/Users/pablito/Antigravity_AGENTS/Krab Voice Gateway/scripts/start_gateway.command`
4. Оркестратор (обертка): `/Users/pablito/Antigravity_AGENTS/Краб/Start_Full_Ecosystem.command`

## Базовый запуск
1. Двойной клик `/Users/pablito/Antigravity_AGENTS/Краб/Start_Full_Ecosystem.command`
2. Проверить вывод health-report в конце скрипта.

## Диагностика локальной модели
1. В Telegram: `!model scan`
2. Переключить слот: `!model set chat <model_id>`
3. Проверить статус: `!model`
4. Если local не грузится: проверить LM Studio server + список model_id в `!model scan`.

## Диагностика OpenClaw auth
1. В Telegram: `!openclaw` и `!openclaw auth`
2. Если `status_reason=auth_missing_lmstudio_profile`, запустить:
`/Users/pablito/Antigravity_AGENTS/Краб/repair_openclaw_lmstudio_auth.command`
3. Перезапустить OpenClaw gateway.

## Типовые health endpoints
- OpenClaw: `http://127.0.0.1:18789/health`
- Voice Gateway: `http://127.0.0.1:8090/health`

## Логи
- Krab: `/Users/pablito/Antigravity_AGENTS/Краб/krab.log`
- OpenClaw: `/Users/pablito/Antigravity_AGENTS/Краб/openclaw.log`
- Voice Gateway: `/Users/pablito/Antigravity_AGENTS/Krab Voice Gateway/gateway.log`
- Krab Ear backend: `/Users/pablito/Antigravity_AGENTS/Krab Ear/KrabEar/krab_ear.log`
