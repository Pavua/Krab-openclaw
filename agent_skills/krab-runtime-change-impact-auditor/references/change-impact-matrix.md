# Change Impact Matrix

## Уровни воздействия

| Impact level | Что означает | Минимальные действия |
| --- | --- | --- |
| `docs-only` | меняются только docs, handoff, notes, registry | freshness + doc truth check |
| `ui-only` | owner UI или web endpoint presentation без runtime contract drift | UI smoke + endpoint sanity |
| `runtime-risk` | backend/runtime/auth/models/routing | targeted runtime checks + release gate review |
| `transport-risk` | userbot, reserve bot, channel routing, delivery | transport regression + live channel smoke |
| `release-critical` | правка может менять итоговый release verdict или core operational truth | release readiness pack + свежие evidence |

## Быстрые соответствия

- `web_app`, owner UI, runtime endpoints -> `ui-only` или `runtime-risk`
- `openclaw_*`, auth/models/routing -> `runtime-risk`
- `userbot_bridge`, Telegram transport, reserve bot policy -> `transport-risk`
- release/handoff/evidence aggregation -> `docs-only` или `release-critical`
