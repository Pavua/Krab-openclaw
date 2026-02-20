# CODEX INTERNAL PLAN — R6

## Цель
Пока внешние окна выполняют текущий раунд, продвинуть ядро по Codex ownership без конфликтов.

## Мои задачи

1. **Signal Alert Delivery Finish**
- довести доставку автоалертов до рабочего Telegram `chat_id`;
- закрепить маршрут: `configure_alert_route -> resolve_telegram_alert_target -> signal_alert_test`.

2. **Ops Guard Runtime**
- поддерживать `signal_ops_guard_daemon` в активном состоянии;
- мониторить и разбирать `artifacts/ops/signal_guard_alerts.jsonl`;
- подготовить минимальный recovery playbook при повторе `signal_sse_instability`.

3. **Web/API Model Explainability**
- довести интеграционный уровень `/api/model/explain` (готово на уровне API и тестов);
- добавить краткую операционную заметку в docs для команды.

4. **Acceptance Discipline**
- принимать внешние поставки только через gate-скрипты;
- не продвигать изменения при красных тестах или overlap;
- фиксировать результат в статус-борде.

5. **Runtime Hygiene**
- контролировать memory hotspots;
- при утечках от внешних helper-процессов (например `pyrefly`) выполнять мягкую очистку.

## Контрольные команды

1. `./scripts/signal_ops_guard_daemon.command status`
2. `./scripts/signal_ops_guard.command --once --verbose --lines 120`
3. `./scripts/memory_hotspots.command`
4. `./review_external_agent_delivery.command`
5. `./scripts/accept_backend_delivery.command`
6. `./scripts/accept_and_promote_frontend.command`
