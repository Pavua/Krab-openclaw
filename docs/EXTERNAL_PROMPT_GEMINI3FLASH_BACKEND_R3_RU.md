# EXTERNAL PROMPT — GEMINI 3.1 FLASH (BACKEND R3)

Работаем в backend-контуре.
Модель: Gemini 3.1 Flash.

## Контекст
В /Users/pablito/Antigravity_AGENTS/Краб/src/handlers/tools.py уже добавлен helper для Voice Gateway ошибок, но применение неполное и остаются дубли/неоднородность.

## Цель R3
Довести рефакторинг до единообразного production-уровня:
1) единый helper используется во всех !call* командах для unavailable/no_session/error;
2) безопасное форматирование details (экранировать backticks и потенциально ломающий markdown);
3) усилить тесты.

## Жесткие ограничения
1. Не менять бизнес-логику команд и параметры API-вызовов.
2. Не трогать frontend.
3. Не трогать OpenClaw routing/model manager.

## Что сделать
1. Внести правки в:
- /Users/pablito/Antigravity_AGENTS/Краб/src/handlers/tools.py

2. Добавить/обновить тесты:
- /Users/pablito/Antigravity_AGENTS/Краб/tests/test_tools_voice_gateway_errors.py
- /Users/pablito/Antigravity_AGENTS/Краб/tests/test_voice_gateway_hardening.py

3. В helper добавить безопасный formatter для `details`:
- экранировать "`" минимум до безопасного отображения в markdown-тексте ответа.

4. Применить helper последовательно по всем !call* хендлерам:
- unavailable
- no_session
- generic/update_fail branches

## Обязательные проверки
pytest -q /Users/pablito/Antigravity_AGENTS/Краб/tests/test_tools_voice_gateway_errors.py /Users/pablito/Antigravity_AGENTS/Краб/tests/test_voice_gateway_hardening.py

## Формат сдачи
- Изменённые файлы
- Что именно унифицировано
- Команды тестов
- Результаты тестов
- Остаточные риски
