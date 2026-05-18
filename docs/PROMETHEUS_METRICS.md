# Krab Prometheus Metrics

Last updated: 2026-05-18 (S63 Wave 2)

Total: **137 metrics** across **18 categories** (auto-generated from
`src/core/metrics/*` and `src/core/metrics/collect.py`).

Source-of-truth: `src/core/metrics/` package (façade re-exported via
`src/core/prometheus_metrics.py`). Many metrics are rendered on-scrape in
`collect.py` (in-memory dict → text format) rather than via
`prometheus_client` registry — this is a deliberate choice to keep imports
optional and avoid registry-collision in tests. Helpers are best-effort
(never throw).

> NOTE: counts mentioned in CLAUDE.md ("53 metrics") refer to the subset
> exposed via the live `/api/admin/metrics` autotables view at the time of
> that count. The full catalog including on-scrape and ear/launchd
> renderers is documented below.

## How to discover live values

```bash
curl -s http://127.0.0.1:8080/metrics | grep -E '^krab_'
```

---

## Categories

### Silent-Death Defense / Dispatcher Health

Detects when Pyrogram dispatcher / swarm probes / guards stop ticking.
Pattern: "outcomes, not heartbeats" (Wave 63 series).

- `krab_main_dispatcher_tick_ago_seconds` — Wave 63-C. Seconds since main
  dispatcher last ticked. `-1` = userbot not registered (probe weakref
  not wired). Alert >120s.
- `krab_uptime_seconds` — S66 W3. Seconds since `userbot_started` event
  (process uptime, sourced from module-load `_PROCESS_START_TIME`).
  Graph by Krab version to correlate with S64 W4 restart-cause logging.
- `krab_last_handler_tick_age_seconds` — S66 W3. Seconds since last
  `@on_message` handler invocation (reads `_last_dispatcher_tick_ts`
  via Wave 70 weakref). `-1` = userbot not registered. Complements
  `krab_main_dispatcher_tick_ago_seconds` for dispatcher liveness graphs.
- `krab_swarm_probe_ago_seconds{team}` — Wave 63-B. Seconds since swarm
  team pts last advanced. `-1` = team probe unset.
- `krab_paid_gemini_guard_mode{mode}` — Wave 67. `1`=block, `0`=warn,
  `-1`=off. Tracks `KRAB_BLOCK_PAID_GEMINI_AI_STUDIO`.
- `krab_pyrogram_disconnects_total{session}` — Wave 142. Count of
  `Connection.close` events per session label.
- `krab_session_corruption_total{kind}` — DB-corruption events that
  triggered quarantine (`malformed_disk_image`, `wal_drop`, etc.).

### Idle Observability (S55-S63)

S55-S62 "idle skip" markers — when the dispatcher would *idle-wake* a
local component but explicitly skips it. Designed to make silent fallbacks
visible.

- `krab_bypass_idle_skip_total{reason}` — S55 D. Local primary bypass
  skipped (`has_photo`, `cloud_or_cli_model`, …).
- `krab_vision_idle_skip_total{reason}` — S56 C. Phase 1 local vision
  (`frame_describe_local`) skipped (`cloud_route_preferred`, …).
- `krab_translator_idle_skip_total{reason}` — S61 W2. Phase 2 local
  translator skipped.
- `krab_codex_idle_skip_total{reason}` — S62 W4 + S63 W1. Codex CLI
  subprocess skipped (`weekly_quota_exhausted`, `disabled_via_env`,
  `subprocess_unavailable`).
- `krab_verifier_samples_total{status}` — S57 P3.1. Local draft verifier
  sample events (`sampled`, `skipped_not_sampled`,
  `skipped_env_disabled`, `skipped_empty_input`).
- `krab_idle_wake_events_total` — Idle-wake fires (post-sleep detector).
- `krab_idle_wake_gap_seconds` — Histogram of measured idle gaps.
- `krab_last_idle_wake_ts` — Unix ts of last idle wake.

### Pyrogram + Telegram Transport

- `krab_telegram_flood_wait_total{caller}` — FloodWait incidents by
  caller-tag.
- `krab_telegram_flood_wait_duration_seconds{caller}` — Wave 121.
  Histogram of FloodWait sleep durations.
- `krab_telegram_rate_limited_active{caller}` — Wave 121. `1` while a
  FloodWait deadline is still in the future (per caller).
- `krab_telegram_outgoing_rate_per_sec` — Telegram-throttle rolling
  outgoing rate.
- `krab_telegram_throttle_applied_total{reason}` — Times throttle paused
  outgoing send.

### LLM Routing + Models

- `krab_llm_route_ok{provider,model}` — Last LLM call status (1=ok, 0=err).
- `krab_llm_route_latency_seconds{provider,model}` — Histogram of route
  latency (sliding window via `llm_latency_tracker`).
- `krab_llm_context_usage_pct{model}` — Last-seen prompt context fill %.
- `krab_active_model_switches_total{from_model,to_model}` — Explicit
  active-model switches.
- `krab_active_model_override_engaged_total{model,scope}` — Per-chat /
  per-team override activations.
- `krab_active_model_resolve_duration_seconds` — Histogram of resolver
  latency.
- `krab_model_fallback_engaged_total{from_model,to_model,reason}` —
  Wave 48-B. Fallback chain advance counter.
- `krab_provider_timeout_total{provider}` — Provider-level timeout count.
- `krab_provider_quarantine_total{provider,reason}` — Provider quarantine
  enter events.
- `krab_provider_quarantined{provider}` — `1` while provider is currently
  in quarantine.
- `krab_codex_disabled_transition_total{state}` — Wave 62-G. Transitions
  in/out of codex-disabled (weekly quota / env gate).
- `krab_smart_retry_wait_seconds` — Histogram of smart-retry backoff.
- `krab_chain_advance_duration_seconds` — Histogram of fallback-chain
  step durations.
- `krab_model_response_chars{model}` — Histogram of output char count.

### Agent Engine (Hermes / OpenClaw)

- `krab_agent_engine_runs_total{engine,success}` — Wave 17-B. Runs per
  engine + success bool.
- `krab_agent_engine_latency_seconds_avg{engine}` — Running average
  latency (no histogram by design — cheap to compute).
- `krab_agent_engine_fallback_total{from_engine,to_engine}` —
  Cross-engine fallback events (hermes → openclaw etc).

### Smart Routing (Wave 73 / Session 26)

5-stage pipeline metrics (hard-gates → policy → regex → LLM → feedback).

- `krab_smart_routing_decisions_total{decision,path}` — Final dispatch
  decisions.
- `krab_smart_routing_stage_duration_seconds{stage}` — Per-stage latency
  histogram.
- `krab_nlu_commands_dispatched_total{intent}` — Wave 135. Commands
  routed via NLU intent.
- `krab_nlu_confidence_score{intent}` — Histogram of NLU confidence.

### Memory Retrieval (Phase 2 + Wave 22 + Wave 74)

- `krab_memory_retrieval_total{mode,outcome}` — Counter by retrieval mode
  + outcome.
- `krab_memory_retrieval_mode_total{mode}` — Counter by mode only.
- `krab_memory_retrieval_latency_seconds{mode}` — Histogram of retrieval
  latency.
- `krab_memory_retrieval_duration_seconds` — Aggregated duration.
- `krab_vec_query_duration_seconds` — Vector-only sub-stage histogram.
- `krab_memory_adaptive_rerank_used_total` — Wave 31. Times adaptive
  reranker chose to fire.
- `krab_memory_query_relevance_score_{quantile}` — Percentile snapshots
  (p50/p90/p99) of RRF score distribution.
- `krab_memory_validator_safe_total` — Confirmed-safe memory writes.
- `krab_memory_validator_injection_blocked_total` — Prompt-injection
  blocks.
- `krab_memory_validator_confirmed_total` — User confirmations to write.
- `krab_memory_validator_confirm_failed_total` — Confirmations failed
  (timeout / decline).
- `krab_memory_validator_pending` — Live count of pending confirmations.
- `krab_archive_messages_total` / `krab_archive_chats_total` /
  `krab_archive_chunks_total` — Counts in `archive.db`.
- `krab_archive_chunks_embedded_total` — Chunks with Model2Vec embedding.
- `krab_archive_db_size_bytes` — Archive.db file size.

### Cost / FinOps (Wave 78)

- `krab_completion_cost_eur{provider,model}` — Per-call cost (gauge).
- `krab_completion_cost_eur_total{provider,model}` — Cumulative cost.
- `krab_tokens_consumed_total{provider,model,type}` — Tokens
  (`type`=prompt/completion).
- `krab_cost_daily_used_eur` / `krab_cost_weekly_used_eur` — Budget
  consumed.
- `krab_cost_daily_pct` / `krab_cost_weekly_pct` — % of budget consumed.

### Google Direct Bypass (Wave 20-B / 18-B)

- `krab_google_direct_bypass_total{model,result}` — Direct google.genai
  SDK calls.
- `krab_google_direct_bypass_latency_seconds{model}` — Histogram.
- `krab_google_direct_bypass_thoughts_tokens{model}` — Thinking-token
  histogram.

### MLX Local + Long Context (Wave 223 / 225)

- `krab_mlx_local_routing_total{reason}` — Routing decisions to local
  MLX (long context / task whitelist).
- `krab_mlx_local_alias_resolved_total{alias}` — Alias-module hits.

### MCP / Tools

- `krab_mcp_server_alive{server}` — `1` if MCP probe returned ok.
- `krab_mcp_server_probe_failures_total{server}` — Probe failure count.
- `krab_mcp_tool_calls_total{server,tool,result}` — Tool-call counter.
- `krab_mcp_tool_duration_seconds{server,tool}` — Histogram.
- `krab_capability_cache_mismatches_total{reason}` — Wave 129.

### Swarm

- `krab_swarm_runs_total{team,result}` — Multi-agent runs.
- `krab_swarm_run_duration_seconds{team}` — Histogram.
- `krab_swarm_artifacts_total` — Wave 134. Stored artifact count.
- `krab_swarm_artifacts_size_mb` — Storage footprint.
- `krab_swarm_tool_blocked_total{team,tool}` — Per-team allowlist blocks.

### Voice (TTS/STT/Gateway)

- `krab_voice_gateway_requests_total{result}` — Voice Gateway calls.
- `krab_voice_gateway_request_duration_seconds` — Histogram.
- `krab_voice_gateway_chars_total{lang}` — Char count.
- `krab_voice_gateway_cost_eur_total{lang}` — Cost.
- `krab_voice_stt_total{provider,result}` — Wave 138. STT calls.
- `krab_voice_stt_duration_seconds{provider}` — Histogram.
- `krab_voice_stt_cost_eur_total{provider}` — STT cost.
- `krab_translation_cache_hits_total` / `krab_translation_cache_misses_total`
  / `krab_translation_cache_size` — Translator cache stats.
- `krab_typing_indicator_started_total{kind}` / `..._cancelled_total{kind}` /
  `..._floodwait_total{kind}` / `..._duration_seconds{kind}` — Wave 177.

### Browser / Stealth / Search

- `krab_browser_session_recycled_total{reason}` — Browser session
  recycles.
- `krab_browser_pool_active` — Active browser-pool instances.
- `krab_stealth_detection_total{layer}` — Anti-bot detection signals
  (canvas/webgl/webrtc/captcha/ratelimit/blocked).
- `krab_search_calls_total{provider,result}` — Search-engine calls.
- `krab_search_cost_eur_total{provider}` — Search cost.

### Owner Panel / FastAPI

- `krab_owner_panel_requests_total{method,path,status}` — HTTP counter.
- `krab_owner_panel_request_duration_seconds{method,path}` — Histogram.
- `krab_owner_panel_5xx_total{path}` — Wave 165. 5xx events.
- `krab_handler_invocations_total{handler}` — Idea 23. Per-handler count.
- `krab_handler_latency_seconds{handler}` — Histogram.
- `krab_command_invocations_total{command}` — Owner command usage.
- `krab_chat_filter_modes_total{mode}` — Chats per filter mode.
- `krab_chat_windows_active` / `..._capacity` / `..._total_messages` /
  `..._evicted_total{reason}` — ChatWindow buffers.
- `krab_chat_heat_score{chat_id}` — Per-chat heat score gauge.

### Pressure-Aware Runtime (Wave 86)

- `krab_free_memory_gb` — Snapshot of free memory at scrape time.
- `krab_pressure_aware_fallback_total{from_model,to_model,reason}` —
  Memory-pressure-driven model fallbacks.
- `krab_process_rss_bytes` / `krab_process_vms_bytes` /
  `krab_process_swap_bytes` — Wave 205. Own-process memory.
- `krab_memory_leak_growth_mb_per_hour` — RSS growth rate.
- `krab_memory_leak_suspected` — `1` if RSS growth exceeds threshold.

### LaunchAgents (Wave 75) + Krab Ear (Wave 79)

Rendered on-scrape via `launchd.py` / `krab_ear.py`.

- LaunchAgent per-service alive + restart counters (`launchd.py` emits
  `krab_launchd_*` family — see source).
- `krab_ear_consecutive_failures` — KrabEar probe consecutive fails.
- `krab_ear_probe_last_ago_seconds` — Seconds since last successful KE
  probe.

### Catchup / Startup (Wave 46-A / 48-A)

- `krab_startup_duration_seconds` — Process-start → `kraab_running` time.
- `krab_startup_catchup_completed_ts` — Unix ts of catchup completion.
- `krab_startup_catchup_failures_total{chat_id}` — Per-chat failure.
- `krab_startup_catchup_chat_failed_total` — Aggregate fail counter.
- `krab_catchup_message_processed_total{chat_id,result}` — Per-msg
  result.
- `krab_catchup_age_seconds` — Histogram of "how old was the message
  when caught up".
- `krab_state_snapshot_failed_total{kind}` — Wave 49-F.

### Operational / Infra

- `krab_cron_run_total{job,result}` — Cron-style internal jobs.
- `krab_cron_duration_seconds{job}` — Histogram.
- `krab_disk_free_bytes{path}` / `krab_disk_used_pct{path}` — Disk-space
  gauge.
- `krab_ssl_cert_days_remaining{host}` — SSL cert expiry tracker.
- `krab_dependency_vulns_total{severity}` — `pip-audit` result.
- `krab_handoff_export_total{result}` — Handoff exports.
- `krab_handoff_export_duration_seconds` — Histogram.
- `krab_session_backup_valid_count` / `krab_session_backup_corrupt_count`
  — Wave 33 backup ledger.
- `krab_lm_studio_discovery_total{result}` — LM Studio discovery probes.
- `krab_lm_models_loaded_count` — Active LM Studio models.
- `krab_lm_estimated_ram_gb` — Estimated total RAM of loaded LM models.
- `krab_openclaw_gateway_healthy` / `..._restarts_total` /
  `..._probe_failures_total` — Gateway probe.
- `krab_rate_limit_blocks_total{caller}` — Internal rate-limiter blocks.
- `krab_rate_limit_active_keys` — Active rate-limit key count.
- `krab_moderation_actions_total{action}` — Anti-abuse actions.
- `krab_reminders_pending_total` — Reminder queue depth.
- `krab_thread_coherence_score{chat_id}` — Feature K coherence gauge.
- `krab_thread_coherence_drift_total{chat_id}` — Drift events.
- `krab_guest_llm_skipped_total{reason}` — Guest LLM skipped (ACL).
- `krab_auto_restart_attempts_total{service}` — Auto-restart attempts.
- `krab_paid_gemini_guard_mode{mode}` — (see Silent-Death section).

### Meta

- `krab_metrics_generated_at` — Unix ts of last `/metrics` collection.
- `krab_process_start_time_seconds` — Owner panel start ts.

---

## Reading-this-in-context tips

- **Idle skip family + dispatcher_tick_ago**: cross-reference these. If
  `tick_ago_seconds` stays low but `bypass_idle_skip_total` keeps
  incrementing — dispatcher is alive but local bypass is being
  consistently bypassed. That's a signal to inspect routing config or
  cloud-prefer flags.
- **`*_idle_skip_total{reason}` cardinality** is intentionally bounded:
  enum-like reasons defined in source. Adding a new reason requires a
  source-code change (no free-form labels).
- **`*_total{*=none}` zero-sentinels**: several counters emit a single
  `name{label="none"} 0` line at startup so Grafana panels don't show
  "no data". This is collect.py policy — not a bug.
- **`-1` sentinels in `*_ago_seconds` gauges**: indicate "probe never
  fired" / "weakref not wired" — not "0 seconds ago". Alert rules should
  filter out `< 0`.

## See also

- `docs/CLAUDE_AUTO_PROMETHEUS.md` — auto-generated alert + metric
  cross-reference (autotables refreshed by `scripts/refresh_autotables.py`).
- `src/core/metrics/collect.py` — on-scrape renderer (single source of
  truth for line-by-line output).
- `prometheus/alerts.yml` — alert rules.
