# Prometheus setup для Krab

Krab экспонирует Prometheus-метрики на `http://127.0.0.1:8080/metrics`
(Owner panel FastAPI). Этот каталог содержит готовые alert rules и рекомендации
по скрапингу.

## Scrape config

Добавь в `prometheus.yml`:

```yaml
scrape_configs:
  - job_name: krab
    scrape_interval: 30s
    metrics_path: /metrics
    static_configs:
      - targets: ["127.0.0.1:8080"]
        labels:
          service: krab
          env: local
```

## Загрузка alert rules

```yaml
rule_files:
  - /path/to/Краб/ops/prometheus/krab_alerts.yml
```

Валидация перед reload:

```bash
brew install prometheus            # для promtool (один раз)
promtool check rules ops/prometheus/krab_alerts.yml
curl -X POST http://127.0.0.1:9090/-/reload
```

## Alert rules (8 шт.)

| Alert | Severity | Trigger |
|-------|----------|---------|
| `KrabDown` | critical | `/metrics` недоступен >2m |
| `ArchiveDbSizeWarning` | warning | archive.db > 500 MB |
| `ArchiveDbSizeCritical` | critical | archive.db > 1 GB |
| `MemoryQueryLatencyHigh` | warning | p95 memory query > 2s |
| `MessageBatcherBackpressure` | warning | batcher queue > 500 |
| `LLMErrorRateHigh` | warning | LLM errors > 0.1/s |
| `CommandHandlerErrors` | warning | command errors > 0.05/s |
| `TelegramRateLimited` | warning | FLOOD_WAIT > 5 за 15m |

## Recommended Grafana queries

1. **Uptime**: `up{job="krab"}`
2. **Archive DB size (MB)**: `krab_archive_db_size_bytes / 1024 / 1024`
3. **Memory query p95 (s)**: `histogram_quantile(0.95, rate(krab_memory_query_duration_seconds_bucket[5m]))`
4. **LLM error rate**: `rate(krab_llm_errors_total[5m])`
5. **Command throughput**: `rate(krab_commands_total[5m])`
6. **Telegram flood-waits (15m)**: `increase(krab_telegram_flood_wait_total[15m])`

Дашборд-спека: при необходимости сгенерировать через Gemini 3.1 Pro
(`docs/DASHBOARD_REDESIGN_SPEC.md` стиль).
