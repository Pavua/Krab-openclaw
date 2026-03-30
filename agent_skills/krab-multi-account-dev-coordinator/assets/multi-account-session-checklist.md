# Multi-Account Session Checklist

## Перед переходом на другую учётку

- Текущая ветка и `git status` зафиксированы
- Понятно, кто владеет runtime
- Подготовлен handoff или минимум truthful summary
- Запущен `Prepare Next Account Session.command`

## На новой учётке

- Запущен `Check New Account Readiness.command`
- Проверен `Check Current Account Runtime.command`
- Подтверждён режим: `code-only` или `controlled live`
- Нет чужого runtime owner на `:8080` и `:18789`

## После live работы

- Собраны артефакты
- Выполнен freeze или reclaim при необходимости
- Обновлён handoff bundle
- Явно отмечено, была ли acceptance helper-only или final
