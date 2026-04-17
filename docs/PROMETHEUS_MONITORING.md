# Prometheus Monitoring для Krab

Документация по настройке внешнего мониторинга Krab через Prometheus + Grafana.

## Endpoint

`GET http://127.0.0.1:8080/metrics` — Prometheus text format (`version=0.0.4`), без аутентификации.

Реализация: `src/core/prometheus_metrics.py` — без зависимости от `prometheus_client`,
ручная сборка text-exposition. Роут: `src/modules/web_app.py` → `@self.app.get("/metrics")`.

Optional модули (memory_validator, reminders_queue, auto_restart_manager) завёрнуты
в `try/except` — отсутствие модуля НЕ ломает endpoint.

## Available metrics

| Name | Type | Description |
|------|------|-------------|
| `krab_memory_validator_safe_total` | counter | Safe memory writes (injection не detected) |
| `krab_memory_validator_injection_blocked_total` | counter | Injection attempts blocked |
| `krab_memory_validator_confirmed_total` | counter | Pending writes owner confirmed |
| `krab_memory_validator_confirm_failed_total` | counter | Failed confirm attempts (wrong hash, expired) |
| `krab_memory_validator_pending` | gauge | Currently pending confirmations |
| `krab_archive_messages_total` | gauge | Messages в archive.db |
| `krab_archive_chats_total` | gauge | Chats в archive.db |
| `krab_archive_chunks_total` | gauge | Chunks в archive.db |
| `krab_archive_chunks_embedded_total` | gauge | Chunks с Model2Vec embeddings |
| `krab_archive_db_size_bytes` | gauge | archive.db file size (bytes) |
| `krab_llm_route_ok{provider,model}` | gauge | Last LLM route status (1=ok, 0=error) |
| `krab_reminders_pending_total` | gauge | Pending reminders |
| `krab_auto_restart_attempts_total{service}` | counter | Auto-restart attempts за последний час |
| `krab_metrics_generated_at` | gauge | Unix timestamp когда metrics были сгенерированы |

## Scrape config (prometheus.yml)

Готовый конфиг в `docs/prometheus.yml`. Минимум:

```yaml
scrape_configs:
  - job_name: 'krab'
    scrape_interval: 30s
    metrics_path: /metrics
    static_configs:
      - targets: ['127.0.0.1:8080']
        labels:
          instance: 'krab-main'
```

## Alert rules

Готовые правила в `docs/krab_alerts.yml`. Разбиты на три группы.

### Critical alerts

```yaml
groups:
  - name: krab_critical
    interval: 30s
    rules:
      - alert: KrabLLMRouteDown
        expr: krab_llm_route_ok == 0
        for: 2m
        labels:
          severity: critical
        annotations:
          summary: "Krab LLM route failing for {{ $labels.provider }}/{{ $labels.model }}"
          description: "`krab_llm_route_ok` = 0 for 2 min. Check Gateway + provider OAuth."

      - alert: KrabMemoryValidatorOverload
        expr: krab_memory_validator_pending > 15
        for: 10m
        labels:
          severity: warning
        annotations:
          summary: "Memory validator pending queue > 15 для 10+ мин"
          description: "Возможно injection attack или много legitimate рекуррентных запросов."

      - alert: KrabMetricsStale
        expr: (time() - krab_metrics_generated_at) > 120
        for: 3m
        labels:
          severity: critical
        annotations:
          summary: "Krab /metrics endpoint stale (>2min)"
          description: "Krab возможно down или endpoint не отвечает."
```

### Capacity alerts

```yaml
  - name: krab_capacity
    interval: 5m
    rules:
      - alert: KrabArchiveGrowingFast
        expr: rate(krab_archive_messages_total[1h]) > 10000
        for: 30m
        labels:
          severity: info
        annotations:
          summary: "archive.db растёт > 10k msgs/час"

      - alert: KrabArchiveDBLarge
        expr: krab_archive_db_size_bytes > 1073741824  # 1 GB
        labels:
          severity: warning
        annotations:
          summary: "archive.db > 1 GB — рассмотри partitioning/pruning"

      - alert: KrabAutoRestartSpiking
        expr: rate(krab_auto_restart_attempts_total[10m]) > 0.1
        for: 10m
        labels:
          severity: warning
        annotations:
          summary: "Auto-restart attempts > 0.1/sec за 10 мин"
          description: "Service {{ $labels.service }} нестабилен — нужен manual investigation."
```

### Engagement alerts

```yaml
  - name: krab_engagement
    rules:
      - alert: KrabInjectionSpike
        expr: rate(krab_memory_validator_injection_blocked_total[1h]) > 0.01
        labels:
          severity: info
        annotations:
          summary: "Injection attempts > 36 per hour"
          description: "Под atak'ом или нужна tune паттернов validator."
```

## Grafana dashboard

Шаблон: `docs/grafana/krab_dashboard.json` (см. отдельный файл — опционально).

Key panels:
1. **LLM Route Health** — Gauge: `krab_llm_route_ok`
2. **Memory Archive Growth** — Graph: `krab_archive_messages_total` over time
3. **Validator Rate** — Stat: `rate(krab_memory_validator_injection_blocked_total[1h])`
4. **Auto-restart Heatmap** — по `service` label
5. **Reminders Pending** — Stat gauge: `krab_reminders_pending_total`

## Scraping локально (для тестов)

```bash
# Start local Prometheus via Docker
docker run -p 9090:9090 \
  -v $(pwd)/docs/prometheus.yml:/etc/prometheus/prometheus.yml \
  -v $(pwd)/docs/krab_alerts.yml:/etc/prometheus/krab_alerts.yml \
  prom/prometheus

# Open http://localhost:9090 → Status → Targets → 'krab' should be UP
# Alerts: http://localhost:9090/alerts
```

## Testing

Smoke-тест: `scripts/prometheus_metrics_smoke.py` — парсит `/metrics`, проверяет
что все ожидаемые имена присутствуют.

```bash
/Users/pablito/Antigravity_AGENTS/Краб/venv/bin/python scripts/prometheus_metrics_smoke.py
```

Expected output:
```
✅ All 5 expected metrics present
Size: <N> bytes, lines: <M>
```

## Troubleshooting

- **`/metrics` 500 error** → check `logger.error("metrics_collect_failed", ...)` в логах Krab.
- **Нет archive метрик** → `~/.openclaw/krab_memory/archive.db` отсутствует или нет read-only доступа.
- **Нет validator метрик** → `src.core.memory_validator` не загружен (feature-flag?).
- **`krab_llm_route_ok` отсутствует** → ни одного запроса к LLM ещё не было или `openclaw_client.last_runtime_route` пуст.

## Связанные документы

- `src/core/prometheus_metrics.py` — реализация коллектора
- `tests/unit/test_prometheus_metrics.py` — 18 unit-тестов
- `docs/ops_incident_runbook.md` — общий ops-runbook
