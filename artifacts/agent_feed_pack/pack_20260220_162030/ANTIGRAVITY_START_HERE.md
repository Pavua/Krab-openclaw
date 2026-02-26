<!--
Этот документ — быстрый старт для внешней нейронки (Antigravity/другой агент).
Нужен, чтобы исполнитель сразу вошел в правильный workflow и не ломал ownership-границы.
-->

# Antigravity / External Agent: Быстрый Старт

## Что открыть в первую очередь

1. `AGENTS.md`
2. `docs/NEURAL_PARALLEL_MASTER_PLAN_RU.md`
3. `docs/parallel_execution_split_v8.md`
4. `docs/ANTIGRAVITY_WORKSTREAM_PROMPT.md`
5. `docs/ANTIGRAVITY_BACKLOG_V8.md`
6. `docs/ANTIGRAVITY_NEXT_SPRINTS_V8.md`

## Обязательные ограничения

1. Работай только в зоне ownership:
   - `config/workstreams/antigravity_paths.txt`
2. Не меняй файлы из зоны Codex:
   - `config/workstreams/codex_paths.txt`
3. Перед сдачей запускай:
   - `python3 scripts/check_workstream_overlap.py`
   - релевантные тесты по своей задаче.

## Формат сдачи

1. Список файлов.
2. Кратко: что и зачем изменено.
3. Команды тестов и результат.
4. Оставшиеся риски.

## Важно

Финальный merge и интеграцию выполняет только Codex после acceptance-гейта.

