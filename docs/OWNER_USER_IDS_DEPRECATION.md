# OWNER_USER_IDS — Migration Guide

## Статус: DEPRECATED (начиная с Session 15)

Env var `OWNER_USER_IDS` устарел в пользу unified ACL json.
Запланировано удаление в **Session 20**.

До Session 20 `OWNER_USER_IDS` продолжает работать (backward compat),
но при старте Краба будет эмитироваться `DeprecationWarning` и запись в лог
(`owner_user_ids_env_deprecated`).

## Как мигрировать

### Шаг 1: Добавить owner в ACL json

Файл ACL: `~/.openclaw/krab_userbot_acl.json`

Формат:
```json
{
  "owner": ["123456789"],
  "full": [],
  "partial": []
}
```

Числовой Telegram user ID добавляем в список `owner`.
Узнать свой ID: отправить `!id` в чат с Крабом или проверить через `@userinfobot`.

### Шаг 2: Через Telegram команду !acl

```
!acl owner 123456789
```

Краб обновит ACL файл автоматически.

### Шаг 3: Убрать OWNER_USER_IDS из .env

Когда ACL json настроен, строку `OWNER_USER_IDS=...` из `.env` можно удалить.
Краб при следующем старте не будет эмитировать deprecation warning.

## Почему меняем

`OWNER_USER_IDS` — статический env var, требует рестарта при изменении.
ACL json обновляется на лету через `!acl` команду, без рестарта.

Единая точка истины: `src/core/access_control.is_owner_user_id()` читает сначала
ACL json, затем (как fallback) `OWNER_USER_IDS`.

## Детали реализации

- `src/config.py::emit_deprecation_warnings()` — вызывается один раз при старте
- `src/core/access_control.py::is_owner_user_id()` — unified owner check (Wave 29-KK)
- Тесты: `tests/unit/test_owner_user_ids_deprecation.py`
