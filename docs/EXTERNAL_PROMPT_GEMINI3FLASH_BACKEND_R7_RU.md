# EXTERNAL PROMPT — GEMINI 3.1 FLASH (BACKEND R7)

Работаем строго в backend ownership Antigravity.
Модель: Gemini 3.1 Flash.

## Цель
Усилить эксплуатационную наблюдаемость и диагностику в Telegram-операциях:
1) расширить операторские метаданные в `summaryx`,
2) добавить безопасный debug-срез по AutoMod policy,
3) покрыть тестами регрессии по новым веткам.

## Разрешенные файлы
- `src/handlers/telegram_control.py`
- `src/core/telegram_chat_resolver.py`
- `src/core/telegram_summary_service.py`
- `src/core/group_moderation_engine.py`
- `src/handlers/groups.py`
- `tests/test_telegram_control.py`
- `tests/test_telegram_chat_resolver.py`
- `tests/test_telegram_summary_service.py`
- `tests/test_group_moderation_engine.py`

## Что сделать
1. `summaryx` operator metadata:
- в успешном ответе добавить короткий tech-блок:
  - `target_chat_id`,
  - `limit_applied`,
  - `focus_applied` (или `-`),
  - `provider` (router summary);
- блок сделать компактным, без мусора и без изменения основной логики summary.

2. AutoMod debug snapshot:
- добавить в `group_moderation_engine` метод вида `get_policy_debug_snapshot(chat_id)`:
  - effective policy,
  - active template markers (если есть),
  - краткие пороги/actions;
- в `groups.py` добавить owner-команду:
  - `!group debug policy`
  - вывод компактный, пригодный для оператора.

3. Error consistency:
- проверить, что новые тексты ошибок не ломают существующие якоря тестов;
- не удалять уже внедрённые `CTRL_*`/`VGW_*` коды.

4. Тесты:
- тесты для `summaryx` tech-блока;
- тесты для `!group debug policy` и snapshot;
- регрессия: существующие тесты на `summaryx access denied` должны оставаться зелёными.

## Ограничения
1. Не менять файлы из codex ownership.
2. Не трогать `src/web/*`.
3. Не добавлять внешние зависимости.

## Обязательные проверки
- `python3 scripts/check_workstream_overlap.py`
- `pytest -q tests/test_telegram_control.py tests/test_telegram_chat_resolver.py tests/test_telegram_summary_service.py tests/test_group_moderation_engine.py`

## Формат сдачи
1. Изменённые файлы
2. Что реализовано
3. Команды тестов
4. Результаты тестов
5. Риски/ограничения
