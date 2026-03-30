# Review Triage Matrix

## Приоритеты

| Категория | Что это | Действие |
| --- | --- | --- |
| `must-fix before merge` | регрессия, safety bug, broken test contract, ложный verdict | чинить первым, отдельно проверять |
| `should-fix in current pass` | заметное улучшение надёжности или ясности без сильного риска | включать в текущий fix-pass |
| `follow-up / non-blocking` | косметика, naming, будущий cleanup | не смешивать с blocker-fixes |

## Сигналы blocker-замечания

- меняет observable behaviour;
- ломает multi-account boundary;
- убирает truthful reporting;
- оставляет дыру в тестовом покрытии для уже найденной ошибки.
