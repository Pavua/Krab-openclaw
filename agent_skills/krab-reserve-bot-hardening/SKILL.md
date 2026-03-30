---
name: krab-reserve-bot-hardening
description: "Ужесточать и перепроверять reserve Telegram Bot контур проекта `/Users/pablito/Antigravity_AGENTS/Краб`: `dmPolicy`, `groupPolicy`, allowlist, external tool guards и post-change delivery. Использовать, когда нужно перевести резервный бот в reserve-safe режим, убрать опасный `open`, подтвердить безопасную деградацию или проверить, что резервный transport не получил лишние права."
---

# Krab Reserve Bot Hardening

Используй этот навык, когда надо проверить именно резервный Telegram контур. Его цель не “дать доставку любой ценой”, а удержать reserve bot в безопасной конфигурации.

## Предусловие

```bash
cd /Users/pablito/Antigravity_AGENTS/Краб
```

## Основные точки входа

- `/Users/pablito/Antigravity_AGENTS/Краб/Apply Reserve Telegram Policy.command`
- `/Users/pablito/Antigravity_AGENTS/Краб/scripts/openclaw_runtime_repair.py`
- `/Users/pablito/Antigravity_AGENTS/Краб/scripts/openclaw_ops_guard.py`
- `/Users/pablito/Antigravity_AGENTS/Краб/docs/OPENCLAW_KRAB_ROADMAP.md`

## Рабочий цикл

1. Прочитать текущие `dmPolicy`, `groupPolicy`, `allowFrom`, `groupAllowFrom`.
2. Проверить, нет ли wildcard, пустого allowlist или ложного `allowlist` без sender-списка.
3. Применить reserve-safe repair.
4. Подтвердить, что конфиг реально изменился.
5. Отдельно проверить post-change delivery, не открывая лишние права.

## Полезные тесты

```bash
pytest tests/unit/test_openclaw_runtime_repair.py -q
```

## Ограничения

- Не открывать reserve bot до `open`, если задача про hardening.
- Не считать доставку оправданием для небезопасной политики.
- После repair всегда читать итоговый runtime config и только потом докладывать результат.
