# Antigravity Task Pack R20 (2 окна)

## Цель
После стабилизации launchd/liveness добить UX + backend-надежность deep-health без регрессий.

## Окно A (Frontend)
Использовать prompt из файла:
- `/Users/pablito/Antigravity_AGENTS/Краб/docs/EXTERNAL_PROMPT_AG_R20_FRONTEND_RU.md`

## Окно B (Backend)
Использовать prompt из файла:
- `/Users/pablito/Antigravity_AGENTS/Краб/docs/EXTERNAL_PROMPT_AG_R20_BACKEND_RU.md`

## Общие ограничения
1. Работать только в ветке `codex/queue-forward-reactions-policy`.
2. Не трогать unrelated файлы.
3. Все комментарии/docstring в коде на русском.
4. Никаких destructive git-команд.
5. В конце каждого окна дать:
   - список файлов,
   - краткий changelog,
   - команды проверки,
   - фактический вывод тестов/смока.

## Definition of Done
1. Frontend и backend задачи завершены и независимы.
2. Targeted тесты зелёные.
3. UI и API контракты не ломают текущие кнопки/скрипты.
4. Изменения готовы к интеграции без ручной чистки.
