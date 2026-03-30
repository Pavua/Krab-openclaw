---
name: krab-photo-route-acceptance
description: "Проверять photo/vision route проекта `/Users/pablito/Antigravity_AGENTS/Краб`, включая `/api/openclaw/photo-smoke`, cloud/local vision выбор, browser relay и acceptance-сценарий `channels_photo_chrome_acceptance.py`. Использовать, когда нужно подтвердить, что фото-путь не застрял, local vision не подменяет cloud policy ложно, browser+photo acceptance зелёный после restart или нужно локализовать регресс в vision/photo маршруте."
---

# Krab Photo Route Acceptance

Используй этот навык для vision/photo контура, когда нужно доказать, что фото-маршрут реально готов. Главный критерий: зелёный `photo-smoke` и acceptance-сценарий, а не только наличие vision-capable модели на бумаге.

## Предусловие

```bash
cd /Users/pablito/Antigravity_AGENTS/Краб
```

## Ключевые точки входа

- `GET /api/openclaw/photo-smoke`
- `/Users/pablito/Antigravity_AGENTS/Краб/scripts/channels_photo_chrome_acceptance.py`
- `/Users/pablito/Antigravity_AGENTS/Краб/tests/test_channels_photo_chrome_acceptance.py`
- `/Users/pablito/Antigravity_AGENTS/Краб/tests/unit/test_web_app_runtime_endpoints.py`

## Рабочий цикл

1. Проверить `photo-smoke` endpoint.
2. Проверить, какой маршрут выбран для `has_photo=True`.
3. Если контур затрагивает browser relay, прогнать channels+photo+chrome acceptance.
4. При регрессе развести причину: local vision, cloud fallback, browser readiness или timeouts.

## Ограничения

- Не считать “есть vision-модель” доказательством готового photo-route.
- Не путать policy `cloud-photo` с фактическим local selection.
- Если photo acceptance красный, отдельно показать, упал photo endpoint или browser часть.
