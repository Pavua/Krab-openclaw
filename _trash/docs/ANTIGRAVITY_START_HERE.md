# Antigravity: Быстрый Старт (без лишней возни)

## Что сделать тебе (1-2 минуты)
1. Открой проект: `/Users/pablito/Antigravity_AGENTS/Краб` в Antigravity.
2. Дай Antigravity прочитать:
   - `AGENTS.md`
   - `task.md`
   - `HANDOVER.md`
   - `docs/parallel_execution_split_v8.md`
   - `docs/ANTIGRAVITY_WORKSTREAM_PROMPT.md`
   - `docs/ANTIGRAVITY_NEXT_SPRINTS_V8.md`
3. Скажи Antigravity:
   **«Работай строго по ANTIGRAVITY_WORKSTREAM_PROMPT.md, не выходя за ownership-зону.»**

## Что уже подготовлено
- Ownership split 50/50:
  - `config/workstreams/codex_paths.txt`
  - `config/workstreams/antigravity_paths.txt`
- Протокол параллельной разработки:
  - `docs/parallel_execution_split_v8.md`
- Проверка пересечений:
  - `scripts/check_workstream_overlap.command`

## Правило работы в 1 строку
Antigravity меняет только файлы из `config/workstreams/antigravity_paths.txt`, Codex — только из `config/workstreams/codex_paths.txt`.

## Контроль перед каждым большим merge
Запуск:
`scripts/check_workstream_overlap.command`

Если overlap > 0 — merge стоп до разведения конфликтов.
