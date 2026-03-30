---
name: krab-provisioning-drafts-operator
description: "Работать с provisioning drafts в проекте `/Users/pablito/Antigravity_AGENTS/Краб`: templates, list/create/preview/apply flow и idempotent write-endpoints owner UI. Использовать, когда нужно проверить provisioning API, создать draft, сравнить preview diff, безопасно применить изменения или локализовать проблему в provisioning_service и web endpoints."
---

# Krab Provisioning Drafts Operator

Используй этот навык для provisioning API и draft flow. Это отдельный write-контур, поэтому каждое create/apply действие нужно подтверждать preview и фактическим результатом.

## Предусловие

```bash
cd /Users/pablito/Antigravity_AGENTS/Краб
```

## Ключевые endpoints

- `GET /api/provisioning/templates`
- `GET /api/provisioning/drafts`
- `POST /api/provisioning/drafts`
- `GET /api/provisioning/preview/{draft_id}`
- `POST /api/provisioning/apply/{draft_id}`

## Ключевые файлы

- `/Users/pablito/Antigravity_AGENTS/Краб/src/modules/web_app.py`
- `/Users/pablito/Antigravity_AGENTS/Краб/src/core/provisioning_service.py`
- `/Users/pablito/Antigravity_AGENTS/Краб/artifacts/provisioning_drafts/`

## Рабочий цикл

1. Проверить, что provisioning service реально сконфигурирован.
2. Считать templates и текущие drafts.
3. Перед apply всегда смотреть preview diff.
4. После apply подтвердить фактическое изменение и idempotency write-flow.

## Ограничения

- Не применять draft без preview, если задача не требует срочного горячего восстановления.
- Не игнорировать `provisioning_service_not_configured`.
- Если write endpoint идемпотентен, проверять повторный вызов отдельно.
