---
name: krab-telegram-transport-regression-pack
description: "Прогонять focused regression pack для Telegram transport проекта `/Users/pablito/Antigravity_AGENTS/Краб`, включая owner channel smoke, reserve bot roundtrip, transport evidence, live channel smoke и остаточные риски. Использовать, когда менялись `userbot_bridge`, transport/runtime routing, Telegram session handling, reserve bot policy или нужен быстрый truthful verdict по доставке сообщений и roundtrip-контру."
---

# Krab Telegram Transport Regression Pack

Используй этот навык, когда нужно быстро подтвердить, что Telegram-контур не сломался, и собрать evidence без ручного чтения логов.

## Основные точки входа

- `/Users/pablito/Antigravity_AGENTS/Краб/scripts/live_channel_smoke.py`
- `/Users/pablito/Antigravity_AGENTS/Краб/scripts/live_reserve_telegram_roundtrip.py`
- `/Users/pablito/Antigravity_AGENTS/Краб/scripts/telegram_transport_evidence.py`
- `/Users/pablito/Antigravity_AGENTS/Краб/scripts/e1e3_acceptance.py`
- `/Users/pablito/Antigravity_AGENTS/Краб/scripts/pre_release_smoke.py`

## Рабочий цикл

1. Сначала определи глубину прогона:
   - `fast regression` — evidence + live channel smoke;
   - `transport + reserve` — добавить reserve roundtrip;
   - `extended acceptance` — добавить `e1e3_acceptance` или `pre_release_smoke`.
2. Перед live-пробами проверь ownership runtime и `telegram_session_state`.
3. Прогони smoke в порядке от дешёвого к дорогому.
4. Не объявляй transport зелёным только по одному успешному script run.
5. Итог формулируй отдельно:
   - что точно подтверждено;
   - какие проверки не запускались;
   - какие риски остались.

## Рекомендуемый порядок

1. `telegram_transport_evidence.py`
2. `live_channel_smoke.py`
3. `live_reserve_telegram_roundtrip.py`, если менялся reserve bot или transport delivery
4. `e1e3_acceptance.py`, если нужно более широкий live verdict
5. `pre_release_smoke.py`, если задача претендует на merge/release gate

## Чего избегать

- Не считать старый JSON-артефакт свежим подтверждением.
- Не смешивать helper-account smoke с финальным release verdict.
- Не пропускать ownership check перед live roundtrip.

## Рекомендуемые связки с другими skills

- `krab-live-acceptance-brief-writer` для финального concise summary.
- `krab-telegram-owner-e2e` для более узкого owner-focused transport сценария.
- `krab-release-gate-keeper`, если после regression нужен merge verdict.

## Ресурсы

- Порядок и смысл прогонов: `references/regression-order.md`
- Шаблон короткого regression verdict: `assets/transport-brief-template.md`
