# Анализ покрытия тестами зоны `src/core/`

> Сгенерировано: 2026-04-09 (session 4 coverage agent)
> Промотировано в репо: 2026-04-10 (session 5)

## Инвентарь `src/core/` (49 модулей)

**Крупнейшие модули (>10KB):** `inbox_service.py` (71KB, 48 методов), `provider_manager.py` (38KB, ~34 метода), `auth_recovery_readiness.py` (38KB, 19 функций), `capability_registry.py` (32KB, 5 функций), `swarm_channels.py` (28KB, 35 методов), `proactive_watch.py` (25KB, 17 методов), `scheduler.py` (27KB, 24 метода), `cost_analytics.py` (10KB, 11 методов).

## HIGH RISK — модули без тест-файла вообще

- **`provider_manager.py`** (38KB, 34+ методов, ядро `ProviderManager`) — set_provider/set_model/set_thinking_depth/set_fallback_chain/report_usage/resolve_config_for_provider/to_api_dict/format_status. Критично: переключение провайдеров OAuth/API/LM Studio. **Нет `test_provider_manager.py`.**
- **`cloud_gateway.py`** (12KB) — `classify_gemini_error`, `fetch_google_models`, `verify_gemini_access`, `get_cloud_fallback_chain`, `resolve_working_gemini_key`, `fetch_google_models_with_fallback`, `get_best_cloud_model`. Вся облачная fallback-цепочка не покрыта.
- **`cost_analytics.py`** (10KB) — `CostAnalytics.record_usage`, `get_cost_so_far_usd`, `check_budget_ok`, `build_usage_report`, `monthly_calls_forecast`. Бюджет и биллинг без тестов.
- **`ecosystem_health.py`** (16KB, `EcosystemHealthService`) — health-агрегатор.
- **`observability.py`** (5KB) — `LatencyTracker.get_percentile`, `MetricsRegistry`, `EventTimeline`, `mask_secrets`, `build_ops_response`, `get_observability_snapshot`. Метрики owner-панели.
- **`openclaw_runtime_signal_truth.py`** (11KB, 12 функций) — `broken_models_from_signal_log`, `runtime_auth_failed_providers_from_signal_log`, `resolve_probe_runtime_truth`. Парсинг gateway-лога.
- **`openclaw_workspace.py`** (12KB, 8 функций) — `load_workspace_prompt_bundle`, `append_workspace_memory_entry`, `recall_workspace_memory`, `build_workspace_state_snapshot`. Есть смежный `test_openclaw_workspace_and_memory_bridge.py`, но это интеграция, не этот модуль напрямую.
- **`provisioning_service.py`** (15KB, `ProvisioningService` с 17 методами) — полностью.
- **`shared_worktree_permissions.py`** (11KB) — `scan_shared_worktree_writability`, `normalize_shared_worktree_permissions`. Файловая система без тестов.
- **`swarm.py`** (`AgentRoom.run_round`, `run_loop`, `SwarmOrchestrator.execute_parallel`), **`swarm_bus.py`** (`SwarmBus.dispatch`, `resolve_team_name`, `list_teams`). Ядро мультиагентов — без юнит-тестов.
- **`error_handler.py`** (`safe_handler`, `get_error_stats`), **`model_router.py`**, **`local_health.py`** (6 функций LM Studio), **`runtime_policy.py`**, **`voice_gateway_control_plane.py`** (Protocol, 24 async-метода), **`cloud_key_probe.py`** (тест есть, но всего 3).
- **`subprocess_env.py`**, **`logger.py`**, **`model_config.py`**, **`model_types.py`**, **`exceptions.py`**, **`openclaw_runtime_models.py`**, **`translator_*`** (5 модулей, только `translator_finish_gate` покрыт). Большинство — маленькие data/helpers, допустимо MEDIUM/LOW.
- **`handoff_auto_export.py`** — файл 0 байт, но `test_handoff_auto_export.py` существует (5 тестов тестируют что-то смежное).

## MEDIUM RISK — тест есть, но покрытие тонкое

- **`inbox_service.py`** — 48 методов `InboxService`, 26 тестов (`test_inbox_service.py`). Не покрыты: `archive_by_kind`, `bulk_update_status`, `list_stale_open_items`, `_append_workflow_event`, `filter_by_age`.
- **`auth_recovery_readiness.py`** — 19 функций, только 2 теста. Не покрыты: `provider_oauth_scope_truth`, `_decode_jwt_payload`, `build_auth_recovery_readiness_snapshot`, `_provider_recovery_entry`, `_gemini_cli_api_key_hint`.
- **`capability_registry.py`** — 5 больших функций, 4 теста (+ web-endpoints). `build_policy_matrix`, `build_channel_capability_snapshot`, `build_system_control_snapshot` покрыты поверхностно.
- **`scheduler.py`** — 24 метода `KrabScheduler`, 9 тестов. Не покрыты: `_retry_or_fail`, `_fire_reminder`, `_persist`, `_load`, `parse_due_time` edge-cases.
- **`proactive_watch.py`** — 17 методов, 11 тестов. OK, но retention/background loops тонко.
- **`mcp_registry.py`** — 10 функций, 3 теста. `resolve_managed_server_launch`, `build_lmstudio_mcp_json` не покрыты.
- **`access_control.py`** — 15 функций, 11 тестов. `update_acl_subject`, `save_acl_runtime_state` в граничных условиях.
- **`cloud_key_probe.py`** — 10 функций, 3 теста.

## LOW RISK (хорошо покрыты)

`chat_ban_cache.py` (14 тестов / 11 методов), `chat_capability_cache.py` (14/14), `silence_mode.py` (20/14), `spam_filter.py` (16/3), `swarm_channels.py` (41/35), `swarm_memory.py` (20/15), `swarm_scheduler.py` (19/19), `telegram_rate_limiter.py` (8/7), `routing_errors.py` (7/7+9), `translator_finish_gate.py` (5/6), `operator_identity.py` (3/4), `lm_studio_auth.py` (5/5), `openclaw_secrets_runtime.py` (4/3), `model_aliases.py` (2/3), `lm_studio_health.py` (5/1).

## Топ-5 приоритетов

1. **`provider_manager.ProviderManager`** — сердце роутинга OAuth/API/LM Studio; регрессии ломают весь userbot. Effort: 1-2ч
2. **`cloud_gateway.classify_gemini_error` + fallback chain** — Gemini-сбои идут неклассифицированными. Effort: 30-60 мин
3. **`cost_analytics.CostAnalytics`** — биллинг и бюджет. Effort: 30 мин
4. **`openclaw_runtime_signal_truth`** — парсинг gateway-лога. Effort: 30 мин
5. **`swarm.AgentRoom` + `swarm_bus.SwarmBus`** — мультиагентное ядро. Effort: 1-2ч
