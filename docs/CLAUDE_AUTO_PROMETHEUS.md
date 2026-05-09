# Auto-generated Prometheus (13 алертов, 27 метрик)

Обновлено: Wave 50-E (10.05.2026). Конфиг: `deploy/monitoring/rules/krab_alerts.yml`
(прежняя ссылка на `scripts/prometheus/` из Session 38 была устаревшей; список из 11
имён в Session 38-snapshot отражал план, а не реальный YAML — actual file всегда
имел 7 правил до Wave 50-E).

## Alerts (13)

Existing (7):
`KrabLLMRouteDown`, `KrabMemoryValidatorOverload`, `KrabMetricsStale`,
`KrabArchiveGrowingFast`, `KrabArchiveDBLarge`, `KrabAutoRestartSpiking`,
`KrabInjectionSpike`

Wave 50-E (6 new, для Wave 47-49 features):
`FallbackChainExhaustedAlert` (warning, > 5/h),
`CodexQuotaExhaustedAlert` (info, > 0/24h),
`MultiChatCatchupFailedAlert` (warning, > 3/h),
`StateSnapshotFailedAlert` (critical, > 0/24h),
`ProviderTimeoutHighAlert` (warning, > 10/min for 5m),
`RouteSwitchHighFrequencyAlert` (warning, > 20/h).

### Pending exporters (Wave 50-E)

YAML-правила landed, но соответствующие counter-метрики ещё не экспортируются
из `src/core/prometheus_metrics.py`. До этого момента alerts смотрят на
"no-data" series и не сработают (Prometheus поведение — алерты не триггерятся
при отсутствии данных). Pending counters:

* `krab_model_fallback_engaged_total{from_model,to_model,reason}` — wire
  через `src/integrations/route_switch_log.py:append_switch`.
* `krab_codex_disabled_transition_total` — wire через
  `src/integrations/codex_quota_state.py`.
* `krab_startup_catchup_chat_failed_total` — wire через
  `src/userbot/message_catchup.py`.
* `krab_state_snapshot_failed_total` — wire через `src/core/state_snapshots.py`.
* `krab_provider_timeout_total{provider,model}` — wire через
  `src/openclaw_client.py` (provider_timeout error code).

## Metrics (27)

`krab_archive_chunks_embedded_total`, `krab_archive_db_size_bytes`,
`krab_auto_restart_attempts_total`, `krab_chat_filter_modes_total`,
`krab_chat_windows_active`, `krab_chat_windows_capacity`,
`krab_chat_windows_evicted_total`, `krab_chat_windows_total_messages`,
`krab_command_invocations_total`, `krab_guest_llm_skipped_total`,
`krab_handler_invocations_total`, `krab_handler_latency_seconds`,
`krab_llm_route_latency_seconds`, `krab_llm_route_ok`,
`krab_memory_adaptive_rerank_used_total`, `krab_memory_retrieval_latency_seconds`,
`krab_memory_retrieval_mode_total`, `krab_memory_validator_pending`,
`krab_metrics_generated_at`, `krab_process_start_time_seconds`,
`krab_reminders_pending_total`, `krab_stealth_detection_total`,
`krab_swarm_tool_blocked_total`, `krab_telegram_flood_wait_total`,
`krab_thread_coherence_drift_total`, `krab_thread_coherence_score`,
`krab_vec_query_duration_seconds`

## Grafana

Dashboard: `http://localhost:3000/d/krab-main` (admin/krab_local), 18 панелей.
