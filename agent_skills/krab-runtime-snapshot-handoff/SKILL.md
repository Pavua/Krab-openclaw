---
name: krab-runtime-snapshot-handoff
description: "Снимать truthful runtime snapshot и собирать handoff bundle для проекта `/Users/pablito/Antigravity_AGENTS/Краб`, включая runtime state, git-срез, known issues и acceptance artifacts. Использовать, когда нужно передать работу в новый чат, зафиксировать точное состояние среды перед рискованными изменениями или оформить handoff без пересказа по памяти."
---

# Krab Runtime Snapshot Handoff

Используй этот навык для фиксации состояния перед handoff или рискованным изменением. Его цель: дать следующему окну или агенту не рассказ, а артефакты.

## Предусловие

```bash
cd /Users/pablito/Antigravity_AGENTS/Краб
```

## Основные точки входа

- `/Users/pablito/Antigravity_AGENTS/Краб/scripts/runtime_snapshot.py`
- `/Users/pablito/Antigravity_AGENTS/Краб/scripts/export_handoff_bundle.py`
- `/Users/pablito/Antigravity_AGENTS/Краб/Export Handoff Bundle.command`
- `GET http://127.0.0.1:8080/api/runtime/handoff`

## Рабочий цикл

1. Снять лёгкий runtime snapshot.
2. Если нужен полноценный handoff, собрать bundle.
3. Проверить, что в bundle попали runtime snapshot, known issues и актуальные acceptance artifacts.
4. Не пересказывать содержимое bundle по памяти, если можно сослаться на сам артефакт.

## Ограничения

- Не собирать handoff из устаревших артефактов, если можно получить свежие.
- Не подменять runtime snapshot “ручным summary”.
- Если runtime API недоступен, явно помечать bundle как частичный.
