"""
Канонический release-checklist и merge-gate runbook для Krab/OpenClaw.

Нужен, чтобы крупные этапы не закрывались по памяти или “на глаз”, а проходили через
воспроизводимый набор unit/smoke/live-проверок с артефактами в `artifacts/ops`.
"""

# Release Checklist RU

Дата актуализации: 2026-03-12
Канонический scope: крупные этапы, restart-sensitive изменения, runtime/UI/channel routing

## Когда этот checklist обязателен

- После крупного backend/frontend этапа, который меняет runtime truth, owner UI или channel behavior.
- Перед `push -> PR -> merge`, если правка затрагивает:
  - `src/modules/web_app.py`
  - runtime repair / autoswitch / compat probe
  - `.command` lifecycle-скрипты
  - browser/MCP readiness
  - transport / Telegram / reserve-safe policy

## Канонические entrypoints

- One-click:
  - [Release Gate.command](/Users/pablito/Antigravity_AGENTS/Краб/Release%20Gate.command)
  - [pre_release_smoke.command](/Users/pablito/Antigravity_AGENTS/Краб/pre_release_smoke.command)
  - [scripts/r20_merge_gate.command](/Users/pablito/Antigravity_AGENTS/Краб/scripts/r20_merge_gate.command)
- CLI:
  - `./.venv/bin/python scripts/pre_release_smoke.py --full --strict-runtime`
  - `./.venv/bin/python scripts/r20_merge_gate.py`

## Что считается обязательным merge-gate

1. `pre_release_smoke.py --full --strict-runtime`
2. `r20_merge_gate.py`
3. Targeted unit/integration для затронутого блока, если они не полностью покрыты pre-release smoke
4. Browser/live acceptance, если правка меняет live UI, runtime write-операции или transport behavior

## Что считается advisory, но не должно игнорироваться молча

- `live_channel_smoke.py`, если он не required в текущем сценарии
- `e1e3_acceptance.py`, если блок не про restart/lifecycle
- `swarm_live_smoke.py --mode mock`, если текущий этап не затрагивает swarm
- Residual warnings в логах и артефактах, которые не роняют required verdict, но меняют release risk

## Порядок перед merge

1. Проверить `git status` и не тянуть в gate чужие незакоммиченные изменения.
2. Запустить [Release Gate.command](/Users/pablito/Antigravity_AGENTS/Краб/Release%20Gate.command).
3. Если gate красный:
   - сначала исправить required failures;
   - отдельно описать advisory failures и решить, это residual risk или blocker.
4. Если правка меняет live UI/runtime:
   - подтвердить реальный сценарий браузером или live probe;
   - приложить snapshot/screenshot или JSON-артефакт.
5. Только после этого делать `commit -> push -> PR`.

## Источники истины для verdict

- `artifacts/ops/pre_release_smoke_latest.json`
- `artifacts/ops/r20_merge_gate_latest.json`
- свежие browser artifacts в `.playwright-cli/` или `output/playwright/`
- roadmap/status docs:
  - [docs/OPENCLAW_KRAB_ROADMAP.md](/Users/pablito/Antigravity_AGENTS/Краб/docs/OPENCLAW_KRAB_ROADMAP.md)
  - [docs/NEXT_CHAT_CHECKPOINT_RU.md](/Users/pablito/Antigravity_AGENTS/Краб/docs/NEXT_CHAT_CHECKPOINT_RU.md)

## Когда merge запрещён

- Есть хотя бы один required failure в `pre_release_smoke_latest.json`
- Есть required failure в `r20_merge_gate_latest.json`
- Live behavior заявлен как “подтверждён”, но есть только unit без browser/live evidence
- Runtime truth после restart не переподтверждён, хотя изменение restart-sensitive

## Особый случай: временная macOS-учётка

- Если разработка идёт не из того macOS-user, который владеет живым runtime/OpenClaw, strict-runtime часть может краснеть по environment-причине:
  - `~/.openclaw` у текущей учётки пустой или не совпадает с live owner;
  - stale процесс принадлежит другому пользователю и не заменяется restart'ом;
  - CLI smoke видит `token_missing` или `openclaw_json_missing_or_invalid` не из-за регрессии кода, а из-за ownership mismatch.
- В таком случае merge-gate всё равно нужно прогнать, но verdict обязан явно отделять:
  - `code regression`
  - `environment/runtime owner blocker`

## Когда merge допустим

- Все required gate-проверки зелёные
- Для live-sensitive изменений есть живое подтверждение
- Residual risks перечислены явно и не маскируются под “готово”
- Есть ветка `codex/...`, commit и push для точки восстановления

## Короткий release verdict шаблон

```text
Release verdict:
- Blockers: <список или none>
- Residual risks: <список>
- Confirmed: <что подтверждено unit/smoke/live>
- Not verified: <что не прогонялось>
- Artifacts: pre_release_smoke_latest.json, r20_merge_gate_latest.json, <browser artifact если есть>
```
