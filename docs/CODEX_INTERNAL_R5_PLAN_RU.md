# CODEX INTERNAL PLAN — R5

## Цель
Пока внешние окна выполняют R5, закрыть критичный ops-контур и не блокировать интеграцию.

## Мои задачи (Codex)

1. **Signal Alert Delivery Finish**
- довести доставку алертов до рабочего Telegram `chat_id`;
- проверить сценарий: `configure_alert_route` -> `resolve_telegram_alert_target` -> `signal_alert_test`.

2. **Signal Runtime Stability**
- держать запущенным `signal_ops_guard_daemon`;
- мониторить `artifacts/ops/signal_guard_alerts.jsonl` на повторяющиеся инциденты;
- при рецидиве подготовить минимальный recovery-runbook.

3. **Acceptance & Integration Gate**
- принять R5 поставки по командам приёмки;
- не интегрировать ничего при overlap/красных тестах;
- зафиксировать только green-результат в handover.

4. **Операционная гигиена**
- контролировать memory hotspots (особенно `pyrefly`/language-server процессы);
- не допускать деградации локального рантайма из-за фоновых утечек памяти.

## Контрольные команды

1. `./scripts/signal_ops_guard_daemon.command status`
2. `./scripts/signal_ops_guard.command --once --verbose --lines 120`
3. `./review_external_agent_delivery.command`
4. `./scripts/accept_backend_delivery.command`
