---
name: krab-transport-state-persistence
description: "Проверять, сохраняется ли transport/runtime state проекта `/Users/pablito/Antigravity_AGENTS/Краб` после restart и читается ли он единообразно в userbot, owner UI, handoff и health-срезах. Использовать, когда нужно подтвердить controlled restart, проверить post-restart delivery, воспроизвести state drift после перезапуска или доказать, что owner/workspace настройки переживают restart."
---

# Krab Transport State Persistence

Используй этот навык для post-restart truth. Он нужен, когда вопрос не “запустилось ли”, а “сохранилось ли нужное состояние и читается ли оно одинаково в разных контурах”.

## Предусловие

```bash
cd /Users/pablito/Antigravity_AGENTS/Краб
```

## На что смотреть

- `telegram_userbot_state`
- owner/workspace настройки после restart
- post-restart reserve delivery
- handoff/runtime snapshot после restart
- расхождение между docs, UI и реальным transport state

## Полезные файлы

- `/Users/pablito/Antigravity_AGENTS/Краб/docs/NEXT_CHAT_CHECKPOINT_RU.md`
- `/Users/pablito/Antigravity_AGENTS/Краб/docs/OPENCLAW_KRAB_ROADMAP.md`
- `/Users/pablito/Antigravity_AGENTS/Краб/tests/integration/test_roles_persistence.py`
- `/Users/pablito/Antigravity_AGENTS/Краб/src/userbot_bridge.py`

## Рабочий цикл

1. Снять baseline state.
2. Провести controlled restart.
3. Сравнить state до/после по runtime, UI и handoff-срезам.
4. Зафиксировать, что пережило restart, а что сбросилось.

## Ограничения

- Не считать transport state сохранённым по одному health-check.
- Если часть состояния подтягивается лениво, дожидаться фактической инициализации.
- После restart всегда использовать новый snapshot, а не артефакт “до”.
