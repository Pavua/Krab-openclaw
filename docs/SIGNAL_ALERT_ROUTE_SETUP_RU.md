# Signal Alert Route Setup (Telegram)

## Зачем
Этот чеклист нужен, чтобы автоалерты `signal_ops_guard` гарантированно доходили до владельца в Telegram.

## Быстрый сценарий

1. Убедись, что бот доступен:
- `openclaw channels status --probe`
- в строке Telegram должен быть `works`.

2. Открой в Telegram диалог с ботом:
- `@mytest_feb2026_bot`
- отправь команду `/start`.

3. Разреши route в `.env`:
- `./scripts/configure_alert_route.command`

4. Зафиксируй chat_id:
- `./scripts/resolve_telegram_alert_target.command`

5. Прогони тест:
- `./scripts/signal_alert_test.command`

6. Проверка guard:
- `./scripts/signal_ops_guard_daemon.command status`
- `./scripts/signal_ops_guard.command --once --verbose --lines 120`

7. Жесткая проверка маршрута (gate):
- `./scripts/check_signal_alert_route.command --strict`
- если команда не проходит, route не готов для production-алертов.

## Типовой сбой и решение

1. Ошибка `chat not found`:
- причина: бот не получил `/start` или указан `@username` без доступного chat_id.
- решение:
  - отправить `/start` боту,
  - повторить `./scripts/resolve_telegram_alert_target.command`,
  - снова `./scripts/signal_alert_test.command`.
