---
name: krab-userbot-acl-governor
description: "Управлять и проверять runtime ACL userbot в проекте `/Users/pablito/Antigravity_AGENTS/Краб`: owner/full/partial, Telegram-команды `!acl`, web endpoints и runtime JSON truth. Использовать, когда нужно выдать или снять доступ, проверить, что owner panel на `:8080` применяет ACL корректно, расследовать ACL-регресс или убедиться, что guest/full/partial права не перепутаны."
---

# Krab Userbot Acl Governor

Используй этот навык для точечного управления доступом userbot, а не для общей диагностики runtime. Главный источник истины для ACL: runtime JSON-файл и API над ним.

## Предусловие

```bash
cd /Users/pablito/Antigravity_AGENTS/Краб
```

## Ключевые файлы и точки входа

- `/Users/pablito/Antigravity_AGENTS/Краб/src/core/access_control.py`
- `/Users/pablito/Antigravity_AGENTS/Краб/src/handlers/command_handlers.py`
- `/Users/pablito/Antigravity_AGENTS/Краб/src/modules/web_app.py`
- `~/.openclaw/krab_userbot_acl.json`
- `GET /api/userbot/acl/status`
- `POST /api/userbot/acl/update`

## Рабочий цикл

1. Считать текущее ACL runtime state.
2. Проверить, относится задача к owner/full/partial, а не к отдельным feature-флагам.
3. Если доступ меняется через UI, подтвердить результат и через API, и через runtime-файл.
4. Если доступ меняется через команду `!acl`, проверить response и итоговый state.
5. Если речь про безопасность, убедиться, что guest не видит owner-only возможности.

## Полезные команды и тесты

```bash
pytest tests/unit/test_access_control.py -q
pytest tests/unit/test_acl_management_commands.py -q
pytest tests/unit/test_web_app_runtime_endpoints.py -q
```

## Ограничения

- Не выдавать `guest` как runtime ACL уровень, если система опирается на `owner/full/partial`.
- Не считать UI-обновление успешным без проверки runtime state.
- Не раскрывать owner-only команды и инструменты в гостевом контуре.
