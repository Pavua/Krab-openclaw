---
name: krab-owner-ui-smoke
description: "Проводить браузерный smoke owner-oriented web UI Краба на `:8080` с реальными кликами, DOM-проверкой и визуальной валидацией. Использовать, когда нужно проверить `Userbot ACL`, `Browser / MCP Readiness`, autoswitch-профили, runtime status endpoints или подтвердить, что UI отражает живую runtime truth после изменений."
---

# Krab Owner Ui Smoke

Используй этот навык, когда нужно не только открыть UI, но и подтвердить пользовательский сценарий действием. Основной критерий успеха: нужный элемент прокликан, DOM обновился, побочный эффект подтверждён.

## Предусловие

```bash
cd /Users/pablito/Antigravity_AGENTS/Краб
```

Подними runtime и убедись, что `http://127.0.0.1:8080` отвечает.

## Рабочий цикл

1. Проверить, что backend UI поднят и health endpoint отвечает.
2. Открыть `:8080` браузерным инструментом.
3. Пройтись по ключевому сценарию: открыть страницу, снять snapshot, кликнуть, переснять DOM, проверить результат.
4. Если сценарий пишет в runtime, подтвердить эффект отдельной проверкой API или CLI.
5. Сохранить screenshot или хотя бы текстовый snapshot, если задача визуально чувствительная.

## Приоритетные сценарии

- `Userbot ACL`
- `Browser / MCP Readiness`
- autoswitch-профили и compat probe
- truthful health/runtime status
- кнопки `Refresh / Grant / Revoke / Start Relay`, если задача касается их поведения

## Полезные артефакты

- `/Users/pablito/Antigravity_AGENTS/Краб/scripts/channels_photo_chrome_acceptance.py`
- `/Users/pablito/Antigravity_AGENTS/Краб/tests/e2e/test_web_panel_openclaw_health.py`
- `/Users/pablito/Antigravity_AGENTS/Краб/output/playwright/`

## Ограничения

- Не ограничиваться чтением HTML, если задача просит проверку сценария.
- После клика, меняющего состояние, всегда переснимать DOM.
- Если действие мутирует runtime, сначала проверить, можно ли использовать dry-run или изолированный контур.
- Не считать визуальный успех достаточным без подтверждения runtime-side эффекта.
