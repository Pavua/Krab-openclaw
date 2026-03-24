# Claude Transfer Pack — README

## Что это

Этот файл описывает готовый пакет для передачи проекта в новый чат, особенно в Claude.

Цель:

- не собирать файлы вручную;
- не думать, что именно приложить;
- не потерять контекст, который уже подтверждён;
- не повторять в новом чате уже закрытые ложные гипотезы.

## Что входит в пакет

Файлы расположены в рекомендуемом порядке чтения:

1. `00_START_HERE_RU.md`
2. `01_HANDOFF_PORTABLE_RU.md`
3. `02_KNOWN_AND_DISCUSSED_RU.md`
4. `03_CLAUDE_READY_FIRST_MESSAGE_RU.md`
5. `04_MASTER_PLAN_SOURCE_OF_TRUTH.md`
6. `05_MASTER_PLAN_VNEXT_RU.md`
7. `06_LM_STUDIO_MCP_SETUP_RU.md`

## Как использовать

### Самый удобный вариант

1. Прикрепи всю папку целиком или zip рядом с ней.
2. В новый чат вставь содержимое файла `03_CLAUDE_READY_FIRST_MESSAGE_RU.md`.
3. Не добавляй длинный ручной пересказ поверх этого.

### Если нужно минимально

Достаточно первых четырёх файлов.

## Ключевая truth пакета

- проект живой, web/openclaw/voice/ear подняты;
- warmup truthful через `google-gemini-cli/gemini-3.1-pro-preview`, `active_tier=paid`;
- live progress около `91%`, baseline около `31%`;
- главный confirmed blocker текущей фазы:
  ordinary Chrome attach к default profile на Chrome `146.0.7680.154`
  блокируется политикой самого Chrome.
