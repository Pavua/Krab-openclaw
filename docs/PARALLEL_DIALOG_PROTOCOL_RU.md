# Parallel Dialog Protocol RU

## Зачем

Если работа идёт параллельно с нескольких учёток, в Codex/Claude и с живым runtime, проекту нужен единый протокол, иначе слишком легко получить race condition в документации и runtime truth.

## Правила

1. Один активный mutating runtime-контур на один момент времени.
2. Параллельные диалоги допустимы для:
   - чтения и анализа;
   - unit/integration тестов без live runtime;
   - документации;
   - отдельных disjoint file sets.
3. Любой live/runtime/release цикл должен завершаться свежим handoff bundle.
4. Перед передачей работы между учётками обязательны:
   - `git status --short --branch`;
   - `Check Current Account Runtime.command`;
   - обновлённый `ATTACH_SUMMARY_RU.md`.

## Формат отчёта после каждой итерации

- что изменено;
- как проверено;
- что осталось;
- влияет ли это на runtime truth или только на код/docs.
