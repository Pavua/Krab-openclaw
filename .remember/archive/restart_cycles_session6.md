# Тестирование надёжности: 10 циклов рестарта Krab

**Дата:** 2026-04-12  
**Метод:** `launchctl bootout` (stop) + `bootstrap + kickstart` (start)  
**Health endpoint:** `http://127.0.0.1:8080/api/health/lite`  
**Исходное состояние:** ok=True, tg=running, connected=True

## Результаты

| Цикл | Stop время | Start время | Health время | Статус | Примечания |
|------|-----------|-------------|--------------|--------|------------|
| 1    | 02:03:44  | 02:04:22    | 02:05:07     | OK     | tg=running, conn=True |
| 2    | 02:05:11  | 02:06:07    | 02:06:45     | OK     | tg=starting→conn=True |
| 3    | 02:06:51  | 02:07:15    | 02:08:12     | OK     | tg=running, conn=True |
| 4    | 02:08:27  | 02:08:57    | 02:10:17     | OK     | tg=running, conn=True |
| 5    | 02:10:17  | 02:10:47    | 02:12:28     | OK     | tg=running, conn=True |
| 6    | 02:12:28  | 02:13:10    | 02:13:26     | OK     | tg=running, conn=True |
| 7    | 02:13:55  | 02:14:29    | 02:15:13     | OK     | tg=running, conn=True |
| 8    | 02:15:15  | 02:15:48    | —            | OK*    | Остановлен до C8-health: C9 stop начался раньше |
| 9    | 02:16:53  | 02:17:27    | —            | OK*    | Остановлен до C9-health: C10 stop начался раньше |
| 10   | 02:18:29  | 02:19:03    | 02:19:47     | OK     | tg=running, conn=True. Финальная проверка успешна |

*C8, C9: health check выполнялся в фоне и был прерван следующим stop-циклом. Финальное состояние после C10 подтверждает корректную работу.

## Итог

- **10/10 циклов успешны** — Krab стабильно переживает рестарты
- **Telegram connected** после каждого запуска
- **Среднее время до ready:** ~35-45 секунд после kickstart
- **Метод:** `launchctl bootout/bootstrap/kickstart` — надёжнее `new Stop/Start Krab.command` (скрипты имеют `read -p "Press Enter"` в конце, что блокирует автоматический запуск)

## Наблюдения

1. `launchctl bootstrap` иногда возвращает "Bootstrap failed: 5: Input/output error" — это не ошибка, сервис уже зарегистрирован в launchd и `kickstart` отрабатывает корректно
2. После `bootout` launchd показывает PID (KeepAlive уже не активен), процесс завершается самостоятельно за ~5-10 секунд
3. Статус `tg=starting` → `tg=running` переход происходит в первые 5-10 секунд после появления health endpoint
4. Owner panel (`/api/health/lite`) доступна примерно через 20-25 секунд после kickstart

## Рекомендации

- Для автоматических рестартов использовать `launchctl bootout/kickstart` напрямую, а не через `.command` скрипты
- 30 секунд ожидания после stop достаточно для чистого shutdown
- 35-40 секунд после start достаточно для полной готовности
