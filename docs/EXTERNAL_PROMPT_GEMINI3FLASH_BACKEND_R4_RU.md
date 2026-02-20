# EXTERNAL PROMPT — GEMINI 3.1 FLASH (BACKEND R4)

Работаем в backend ownership Antigravity.
Модель: Gemini 3.1 Flash.

## Цель
Закрыть устойчивость Telegram Summary/Resolver без выхода за ownership:
1) ужесточить негативные кейсы в summary/resolver,
2) улучшить диагностику ошибок для пользователя,
3) добавить тесты edge-case.

## Разрешенные файлы
- src/handlers/telegram_control.py
- src/core/telegram_chat_resolver.py
- src/core/telegram_summary_service.py
- tests/test_telegram_control.py
- tests/test_telegram_chat_resolver.py
- tests/test_telegram_summary_service.py

## Что сделать
1. Telegram Chat Resolver:
- обработать неоднозначные/битые input (пробелы, @@@, короткие числа, смешанные форматы);
- возвращать предсказуемые ошибки с actionable next-step.
2. Summary Service:
- усилить обработку пустой истории/частичных фейлов/timeout роутера;
- не терять диагностический контекст в ответе.
3. Telegram Control UX:
- унифицировать текст ошибок и подсказки;
- убрать дублирование шаблонов ответов через helper (в пределах файла).
4. Тесты:
- добавить/обновить edge-тесты под кейсы выше.

## Обязательные проверки
- python3 scripts/check_workstream_overlap.py
- pytest -q tests/test_telegram_control.py tests/test_telegram_chat_resolver.py tests/test_telegram_summary_service.py

## Формат сдачи
- Изменённые файлы
- Что улучшено
- Команды тестов
- Результаты
- Остаточные риски
