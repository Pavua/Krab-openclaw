# Auto-generated Prometheus (13 алертов, 32 метрики)

Обновлено: Wave 53-F (10.05.2026). Конфиг: `deploy/monitoring/rules/krab_alerts.yml`
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

### Wired counters (Wave 51-A, 2026-05-10)

Counters teper ARE exported из `src/core/prometheus_metrics.py` — alerts
смотрят на live data. 5 новых counters (label cardinality protected via
helper string slicing):

* `krab_model_fallback_engaged_total{from_model,to_model,reason}` — wired
  в `src/openclaw_client.py` (Wave 47-A chain advance + Wave 44-V quota
  fallback). Triggers `FallbackChainExhaustedAlert`.
* `krab_codex_disabled_transition_total{kind}` — wired в
  `src/integrations/codex_quota_state.py:mark_codex_disabled`. kind=weekly|transient.
  Triggers `CodexQuotaExhaustedAlert`.
* `krab_startup_catchup_chat_failed_total{chat_id}` — wired в
  `src/userbot/message_catchup.py:_catchup_chat_history`. Triggers
  `MultiChatCatchupFailedAlert`.
* `krab_state_snapshot_failed_total{reason}` — wired в
  `src/core/state_snapshots.py` (3 hooks: copy_failed/list_entry_failed/restore_failed).
  Triggers `StateSnapshotFailedAlert`.
* `krab_provider_timeout_total{provider,model}` — wired в
  `src/openclaw_client.py` (semantic_error_detected + httpx.TimeoutException).
  Triggers `ProviderTimeoutHighAlert`.

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
