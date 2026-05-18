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

# === idle_skip (S62 W6) ===
from .idle_skip import (
    _BYPASS_IDLE_SKIP_COUNTER,
    _TRANSLATOR_IDLE_SKIP_COUNTER,
    _VERIFIER_SAMPLES_COUNTER,
    _VISION_IDLE_SKIP_COUNTER,
    _bypass_idle_skip_total,
    _translator_idle_skip_total,
    _verifier_samples_total,
    _vision_idle_skip_total,
    inc_bypass_idle_skip,
    inc_translator_idle_skip,
    inc_verifier_sample,
    inc_vision_idle_skip,
)

# === long_context_routing (Wave 223) ===
from .long_context_routing import (
    _MLX_LOCAL_ROUTING_COUNTER,
    _mlx_local_routing_total,
    inc_mlx_local_routing,
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

# === mlx_local_aliases (Wave 225) ===
from .mlx_local_aliases import (
    _MLX_LOCAL_ALIAS_COUNTER,
    _mlx_local_alias_resolved_total,
    inc_mlx_local_alias_resolved,
)

# === nlu_intent (Wave 135) ===
from .nlu_intent import (
    krab_nlu_commands_dispatched_total,
    krab_nlu_confidence_score,
    record_nlu_intent,
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

# === pyrogram_reconnect (Wave 142) ===
from .pyrogram_reconnect import (
    _PYROGRAM_DISCONNECTS_COUNTER,
    _PYROGRAM_SESSION_LABEL,
    _pyrogram_disconnects_total,
    get_pyrogram_session_label,
    inc_pyrogram_disconnect,
    set_pyrogram_session_label,
)

# === smart_routing (Wave 73) ===
from .smart_routing import (
    krab_smart_routing_decisions_total,
    krab_smart_routing_stage_duration_seconds,
    map_smart_routing_path,
    record_smart_routing_decision,
)

# === swarm_artifacts (Wave 134) ===
from .swarm_artifacts import (
    _SWARM_ARTIFACTS_SIZE_MB,
    _SWARM_ARTIFACTS_TOTAL,
    _swarm_artifacts_size_mb,
    _swarm_artifacts_total,
    set_swarm_artifacts_metrics,
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

# === typing_indicator (Wave 177) ===
from .typing_indicator import (
    krab_typing_indicator_cancelled_total,
    krab_typing_indicator_duration_seconds,
    krab_typing_indicator_floodwait_total,
    krab_typing_indicator_started_total,
    record_typing_cancelled,
    record_typing_floodwait,
    record_typing_started,
)

# === voice_stt (Wave 138) ===
from .voice_stt import (
    krab_voice_stt_cost_eur_total,
    krab_voice_stt_duration_seconds,
    krab_voice_stt_total,
    record_voice_stt,
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
    # nlu_intent (Wave 135)
    "krab_nlu_commands_dispatched_total",
    "krab_nlu_confidence_score",
    "record_nlu_intent",
    # voice_stt (Wave 138)
    "krab_voice_stt_cost_eur_total",
    "krab_voice_stt_duration_seconds",
    "krab_voice_stt_total",
    "record_voice_stt",
    # typing_indicator (Wave 177)
    "krab_typing_indicator_cancelled_total",
    "krab_typing_indicator_duration_seconds",
    "krab_typing_indicator_floodwait_total",
    "krab_typing_indicator_started_total",
    "record_typing_cancelled",
    "record_typing_floodwait",
    "record_typing_started",
    # pressure_aware (Wave 86)
    "_PRESSURE_AWARE_FALLBACK_COUNTER",
    "_pressure_aware_fallback_total",
    "inc_pressure_aware_fallback",
    # long_context_routing (Wave 223)
    "_MLX_LOCAL_ROUTING_COUNTER",
    "_mlx_local_routing_total",
    "inc_mlx_local_routing",
    # idle_skip (S62 W6)
    "_BYPASS_IDLE_SKIP_COUNTER",
    "_TRANSLATOR_IDLE_SKIP_COUNTER",
    "_VERIFIER_SAMPLES_COUNTER",
    "_VISION_IDLE_SKIP_COUNTER",
    "_bypass_idle_skip_total",
    "_translator_idle_skip_total",
    "_verifier_samples_total",
    "_vision_idle_skip_total",
    "inc_bypass_idle_skip",
    "inc_translator_idle_skip",
    "inc_verifier_sample",
    "inc_vision_idle_skip",
    # mlx_local_aliases (Wave 225)
    "_MLX_LOCAL_ALIAS_COUNTER",
    "_mlx_local_alias_resolved_total",
    "inc_mlx_local_alias_resolved",
    # swarm_artifacts (Wave 134)
    "_SWARM_ARTIFACTS_SIZE_MB",
    "_SWARM_ARTIFACTS_TOTAL",
    "_swarm_artifacts_size_mb",
    "_swarm_artifacts_total",
    "set_swarm_artifacts_metrics",
    # pyrogram_reconnect (Wave 142)
    "_PYROGRAM_DISCONNECTS_COUNTER",
    "_PYROGRAM_SESSION_LABEL",
    "_pyrogram_disconnects_total",
    "get_pyrogram_session_label",
    "inc_pyrogram_disconnect",
    "set_pyrogram_session_label",
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
