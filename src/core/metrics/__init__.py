# -*- coding: utf-8 -*-
"""Package re-exports: все публичные/тестируемые символы из submodules
доступны как `from src.core.metrics import X`.

Используется фасадом `src.core.prometheus_metrics` для обратной совместимости.
"""

from __future__ import annotations

# === capability_cache_audit (Wave 129) ===
from .capability_cache_audit import (
    _CAPABILITY_CACHE_MISMATCH_COUNTER,
    _capability_cache_mismatch_total,
    inc_capability_cache_mismatch,
)

# === collect orchestrator ===
from .collect import (
    _format_metric,
    _sanitize_label,
    collect_metrics,
)

# === google_bypass (Wave 20-B) ===
from .google_bypass import (
    krab_google_direct_bypass_latency_seconds,
    krab_google_direct_bypass_thoughts_tokens,
    krab_google_direct_bypass_total,
    record_google_bypass_call,
)

# === memory (Wave 22 + Wave 74 retrieval) ===
from .memory import (
    _memory_retrieval_duration_seconds,
    _memory_retrieval_latency_seconds,
    _memory_retrieval_mode_total,
    _memory_retrieval_total,
    _vec_query_duration_seconds,
    inc_retrieval_outcome,
    record_retrieval_duration,
)

# === pressure_aware (Wave 86) ===
from .pressure_aware import (
    _PRESSURE_AWARE_FALLBACK_COUNTER,
    _pressure_aware_fallback_total,
    inc_pressure_aware_fallback,
)

# === probes (Wave 70 weakref) ===
from .probes import (
    _get_userbot_for_metrics,
    register_userbot_for_metrics,
)

# === process (uptime, FloodWait, session corruption, startup, agent engine,
# Wave 51-A, Wave 55-C, Idea 23 handler latency, guest LLM skip, adaptive rerank) ===
from .process import (
    _ADAPTIVE_RERANK_COUNTER,
    _AGENT_ENGINE_FALLBACK_COUNTER,
    _AGENT_ENGINE_LATENCY_ACC,
    _AGENT_ENGINE_RUNS_COUNTER,
    _GUEST_LLM_SKIPPED_COUNTER,
    _PROCESS_START_TIME,
    _SESSION_CORRUPTION_COUNTER,
    _STARTUP_DURATION_SECONDS,
    _TELEGRAM_FLOOD_WAIT_COUNTER,
    _handler_invocations_total,
    _handler_latency_seconds,
    _HandlerLatencyTimer,
    inc_session_corruption,
    inc_telegram_flood_wait,
    krab_chain_advance_duration_seconds,
    krab_codex_disabled_transition_total,
    krab_model_fallback_engaged_total,
    krab_model_response_chars,
    krab_provider_timeout_total,
    krab_smart_retry_wait_seconds,
    krab_startup_catchup_chat_failed_total,
    krab_state_snapshot_failed_total,
    observe_handler_latency,
    record_agent_engine_fallback,
    record_agent_engine_run,
    record_chain_advance_duration,
    record_codex_disabled_transition,
    record_model_fallback_engaged,
    record_provider_timeout,
    record_response_chars,
    record_smart_retry_wait,
    record_startup_catchup_chat_failed,
    record_state_snapshot_failed,
    set_startup_duration,
    time_handler,
)

# === smart_routing (Wave 73) ===
from .smart_routing import (
    krab_smart_routing_decisions_total,
    krab_smart_routing_stage_duration_seconds,
    map_smart_routing_path,
    record_smart_routing_decision,
)

# === telegram_rate (Wave 121) ===
from .telegram_rate import (
    _RATE_LIMIT_DEADLINES as _TELEGRAM_RATE_LIMIT_DEADLINES,  # test alias
)
from .telegram_rate import (
    _flood_wait_duration_seconds as _telegram_flood_wait_duration_seconds,
)
from .telegram_rate import (
    _rate_limited_active as _telegram_rate_limited_active,
)
from .telegram_rate import (
    observe_telegram_flood_wait,
    refresh_telegram_rate_limited_active,
)

# === thread_coherence (Feature K) ===
from .thread_coherence import (
    _thread_coherence_drift_total,
    _thread_coherence_score,
    observe_thread_coherence,
)

# === token_cost (Wave 78) ===
from .token_cost import (
    _calculate_cost_eur,
    _infer_provider_from_model,
    _resolve_pricing,
    krab_completion_cost_eur,
    krab_completion_cost_eur_total,
    krab_tokens_consumed_total,
    record_completion_cost,
)

__all__ = [
    # process
    "_ADAPTIVE_RERANK_COUNTER",
    "_AGENT_ENGINE_FALLBACK_COUNTER",
    "_AGENT_ENGINE_LATENCY_ACC",
    "_AGENT_ENGINE_RUNS_COUNTER",
    "_GUEST_LLM_SKIPPED_COUNTER",
    "_PROCESS_START_TIME",
    "_SESSION_CORRUPTION_COUNTER",
    "_STARTUP_DURATION_SECONDS",
    "_TELEGRAM_FLOOD_WAIT_COUNTER",
    "_handler_invocations_total",
    "_handler_latency_seconds",
    "_HandlerLatencyTimer",
    "inc_session_corruption",
    "inc_telegram_flood_wait",
    "krab_chain_advance_duration_seconds",
    "krab_codex_disabled_transition_total",
    "krab_model_fallback_engaged_total",
    "krab_model_response_chars",
    "krab_provider_timeout_total",
    "krab_smart_retry_wait_seconds",
    "krab_startup_catchup_chat_failed_total",
    "krab_state_snapshot_failed_total",
    "observe_handler_latency",
    "record_agent_engine_fallback",
    "record_agent_engine_run",
    "record_chain_advance_duration",
    "record_codex_disabled_transition",
    "record_model_fallback_engaged",
    "record_provider_timeout",
    "record_response_chars",
    "record_smart_retry_wait",
    "record_startup_catchup_chat_failed",
    "record_state_snapshot_failed",
    "set_startup_duration",
    "time_handler",
    # google_bypass
    "krab_google_direct_bypass_latency_seconds",
    "krab_google_direct_bypass_thoughts_tokens",
    "krab_google_direct_bypass_total",
    "record_google_bypass_call",
    # memory
    "_memory_retrieval_duration_seconds",
    "_memory_retrieval_latency_seconds",
    "_memory_retrieval_mode_total",
    "_memory_retrieval_total",
    "_vec_query_duration_seconds",
    "inc_retrieval_outcome",
    "record_retrieval_duration",
    # thread_coherence
    "_thread_coherence_drift_total",
    "_thread_coherence_score",
    "observe_thread_coherence",
    # probes
    "_get_userbot_for_metrics",
    "register_userbot_for_metrics",
    # smart_routing
    "krab_smart_routing_decisions_total",
    "krab_smart_routing_stage_duration_seconds",
    "map_smart_routing_path",
    "record_smart_routing_decision",
    # token_cost
    "_calculate_cost_eur",
    "_infer_provider_from_model",
    "_resolve_pricing",
    "krab_completion_cost_eur",
    "krab_completion_cost_eur_total",
    "krab_tokens_consumed_total",
    "record_completion_cost",
    # pressure_aware (Wave 86)
    "_PRESSURE_AWARE_FALLBACK_COUNTER",
    "_pressure_aware_fallback_total",
    "inc_pressure_aware_fallback",
    # capability_cache_audit (Wave 129)
    "_CAPABILITY_CACHE_MISMATCH_COUNTER",
    "_capability_cache_mismatch_total",
    "inc_capability_cache_mismatch",
    # telegram_rate (Wave 121)
    "_TELEGRAM_RATE_LIMIT_DEADLINES",
    "_telegram_flood_wait_duration_seconds",
    "_telegram_rate_limited_active",
    "observe_telegram_flood_wait",
    "refresh_telegram_rate_limited_active",
    # collect
    "_format_metric",
    "_sanitize_label",
    "collect_metrics",
]
