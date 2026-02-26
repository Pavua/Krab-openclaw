# Baseline до рефакторинга (Фаза 0)

**Дата:** 2026-02-26  
**Цель:** Зафиксировать текущее состояние качества кода и тестов для сравнения до/после рефакторинга (Krab Refactoring Master Plan).

---

## Команды проверки (воспроизведение)

```bash
# Активация окружения
source .venv/bin/activate

# Линтер (ruff)
ruff check src tests
ruff format --check src tests

# Тесты
pytest -q
# или с покрытием:
# pytest -q --cov=src --cov-report=term-missing
```

---

## Метрики кодовой базы

| Метрика | Значение |
|--------|----------|
| Строк кода в `src/` (wc -l) | ~33 474 |
| Файлов Python в `src/` | — |
| Тестовых файлов (test_*.py в tests/) | 122 |
| Собрано тестов (pytest --co) | 629 |

---

## Pytest — текущее состояние

| Результат | Количество |
|-----------|------------|
| **Passed** | 625 |
| **Skipped** | 4 |
| **Failed** | 0 |
| **Время** | ~76 с |
| **Предупреждения** | 1 (DeprecationWarning: Pyrogram `asyncio.get_event_loop()`) |

Итог: **все запускаемые тесты проходят.** Регрессия после рефакторинга = появление хотя бы одного падающего теста или уменьшение числа passed при том же наборе тестов.

---

## Ruff check — текущее состояние

- **Всего ошибок:** 1687  
- **Файлов с замечаниями:** 177  
- **Исправимо автоматически (`--fix`):** 1520  
- **Дополнительно с `--unsafe-fixes`:** 45  

### Распределение по кодам (топ)

| Код | Описание | Кол-во |
|-----|----------|--------|
| W293 | Blank line contains whitespace | ~1125 |
| I001 | Import block is un-sorted or un-formatted | 182 |
| W291 | Trailing whitespace | ~81 |
| E701 | Multiple statements on one line (colon) | 45 |
| E402 | Module level import not at top of file | 37 |
| F401 | Imported but unused | много (asyncio, pytest, os, Optional, etc.) |
| F541 | f-string without any placeholders | 9 |
| E722 | Do not use bare `except` | 9 |
| invalid-syntax | См. ниже | 8 |
| F821 | Undefined name | 5 (в т.ч. `asyncio`, `config`) |
| F841 | Local variable assigned but never used | 15+ |
| E702 | Multiple statements on one line (semicolon) | 2 |
| N806 | Variable naming (lowercase) | несколько |
| E731 | Do not assign a lambda, use def | 1 |
| F811 | Redefinition of unused name | несколько |
| F402 | Import shadowed by loop variable | 1 |
| N818 | Exception name should end with Error | 1 |

### Критичные: синтаксис (Python 3.11)

Ruff сообщает **invalid-syntax** в одном файле — использование escape-последовательностей в f-строках (поддержано с Python 3.12), при целевом `py311`:

- **Файл:** `src/handlers/commands.py`
- **Строки:** 404, 411, 413, 414 (по 2 ошибки на строку — 8 сообщений)
- **Причина:** Cannot use an escape sequence (backslash) in f-strings on Python 3.11 (syntax was added in Python 3.12)

Исправление: вынести выражение с `\` в переменную или использовать обычную строку/format.

---

## Ruff format — текущее состояние

- **Файлов, которые нужно отформатировать:** 199  
- **Уже отформатировано:** 23  

После приведения к единому стилю: `ruff format src tests` (изменения только форматирование, логику не трогать).

---

## SLO для рефакторинга (напоминание)

- Нет тихих сбоев fallback.
- Таски очереди не зависают сверх таймаута.
- Детерминированные и полезные сообщения пользователю при сбоях local/cloud.

---

## Рекомендации по Фазе 0

1. **Не менять логику** — только зафиксировать baseline (этот файл) и при необходимости прогнать форматтер.
2. **Форматтер:** при необходимости выполнить `ruff format src tests` и зафиксировать результат в репозитории; после этого обновить здесь число «файлов, которые нужно отформатировать» на 0.
3. **Ruff check:** не править в рамках Фазы 0; использовать этот отчёт как план для последующих фаз (в т.ч. P3 lint-cleanup).
4. **Синтаксис в `commands.py`:** исправить до перехода на py312 или оставить target 3.11 и заменить f-string с `\` на совместимый вариант.

---

*Документ создан автоматически по плану krab_refactoring_master_plan.plan.md (Фаза 0).*
