# -*- coding: utf-8 -*-
"""Orchestrator: формирует /metrics text response, аггрегируя все блоки."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from . import krab_ear as _krab_ear
from . import launchd as _launchd
from . import probes as _probes


def _sanitize_label(value: str) -> str:
    """Escape кавычек и переводов строк в значении label."""
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def _format_metric(
    name: str,
    value: float | int,
    labels: dict[str, str] | None = None,
    help_text: str = "",
    mtype: str = "gauge",
) -> str:
    """Format single Prometheus metric."""
    lines: list[str] = []
    if help_text:
        lines.append(f"# HELP {name} {help_text}")
    lines.append(f"# TYPE {name} {mtype}")
    if labels:
        label_str = ",".join(f'{k}="{_sanitize_label(str(v))}"' for k, v in labels.items())
        lines.append(f"{name}{{{label_str}}} {value}")
    else:
        lines.append(f"{name} {value}")
    return "\n".join(lines)


def collect_metrics() -> str:
    """Main collector — возвращает Prometheus text."""
    # Lazy import фасада чтобы избежать circular import.
    import src.core.prometheus_metrics as _pm  # noqa: PLC0415

    lines: list[str] = []

    # === Memory Validator ===
    try:
        from src.core.memory_validator import memory_validator  # type: ignore[import-not-found]

        stats = getattr(memory_validator, "stats", {}) or {}
        for key in (
            "safe_total",
            "injection_blocked_total",
            "confirmed_total",
            "confirm_failed_total",
        ):
            lines.append(
                _format_metric(
                    f"krab_memory_validator_{key}",
                    stats.get(key, 0),
                    help_text=f"Memory validator {key}",
                    mtype="counter",
                )
            )
        try:
            pending_count = len(memory_validator.list_pending())
        except Exception:
            pending_count = 0
        lines.append(
            _format_metric(
                "krab_memory_validator_pending",
                pending_count,
                help_text="Memory validator pending confirmations",
            )
        )
    except Exception:
        pass

    # === Archive DB ===
    try:
        db_path = Path("~/.openclaw/krab_memory/archive.db").expanduser()
        if db_path.exists():
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            try:
                for table in ("messages", "chats", "chunks"):
                    try:
                        count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                        lines.append(
                            _format_metric(
                                f"krab_archive_{table}_total",
                                count,
                                help_text=f"Archive.db {table} count",
                            )
                        )
                    except sqlite3.Error:
                        pass
                try:
                    embedded = conn.execute(
                        "SELECT COUNT(*) FROM chunks WHERE embedding IS NOT NULL"
                    ).fetchone()[0]
                    lines.append(
                        _format_metric(
                            "krab_archive_chunks_embedded_total",
                            embedded,
                            help_text="Chunks with Model2Vec embedding",
                        )
                    )
                except sqlite3.OperationalError:
                    pass
            finally:
                conn.close()
            try:
                lines.append(
                    _format_metric(
                        "krab_archive_db_size_bytes",
                        db_path.stat().st_size,
                        help_text="Archive.db file size",
                    )
                )
            except OSError:
                pass
    except Exception:
        pass

    # === Runtime Route ===
    try:
        from src.openclaw_client import openclaw_client

        route = getattr(openclaw_client, "last_runtime_route", None) or getattr(
            openclaw_client, "_last_runtime_route", None
        )
        if isinstance(route, dict) and route:
            status_ok = 1 if route.get("status") == "ok" else 0
            lines.append(
                _format_metric(
                    "krab_llm_route_ok",
                    status_ok,
                    labels={
                        "provider": str(route.get("provider", "unknown"))[:50],
                        "model": str(route.get("model", "unknown"))[:80],
                    },
                    help_text="Last LLM route status (1=ok, 0=error)",
                )
            )
    except Exception:
        pass

    # === Reminders ===
    try:
        from src.core.reminders_queue import reminders_queue  # type: ignore[import-not-found]

        pending = reminders_queue.list_pending()
        lines.append(
            _format_metric(
                "krab_reminders_pending_total",
                len(pending),
                help_text="Pending reminders",
            )
        )
    except Exception:
        pass

    # === Auto-restart ===
    try:
        from src.core.auto_restart_policy import _attempts_total as _arp_attempts

        for svc_name, attempt_count in _arp_attempts.items():
            lines.append(
                _format_metric(
                    "krab_auto_restart_attempts_total",
                    attempt_count,
                    labels={"service": str(svc_name)[:50]},
                    help_text="Total auto-restart attempts since process start",
                    mtype="counter",
                )
            )
    except Exception:
        pass

    # === Command invocations ===
    try:
        from src.core.command_registry import get_usage  # type: ignore[import-not-found]

        usage = get_usage()
        if usage:
            for cmd, count in usage.items():
                lines.append(
                    _format_metric(
                        "krab_command_invocations_total",
                        count,
                        labels={"command": cmd[:30]},
                        help_text="Total invocations per command",
                        mtype="counter",
                    )
                )
    except Exception:
        pass

    # === LLM route latency histogram ===
    try:
        from src.core.llm_latency_tracker import (
            llm_latency_tracker,  # type: ignore[import-not-found]
        )

        for series in llm_latency_tracker.snapshot():
            provider = series["provider"]
            model = series["model"]
            metric_name = "krab_llm_route_latency_seconds"
            lines.append(f"# HELP {metric_name} LLM route latency histogram (seconds)")
            lines.append(f"# TYPE {metric_name} histogram")
            for le_str, cnt in series["buckets"].items():
                label_str = (
                    f'provider="{_sanitize_label(provider)}",'
                    f'model="{_sanitize_label(model)}",'
                    f'le="{le_str}"'
                )
                lines.append(f"{metric_name}_bucket{{{label_str}}} {cnt}")
            label_str_base = (
                f'provider="{_sanitize_label(provider)}",model="{_sanitize_label(model)}"'
            )
            lines.append(f"{metric_name}_sum{{{label_str_base}}} {series['sum']:.6f}")
            lines.append(f"{metric_name}_count{{{label_str_base}}} {series['count']}")
    except Exception:
        pass

    # === Chat filter modes ===
    try:
        from src.core.chat_filter_config import chat_filter_config  # type: ignore[import-not-found]

        stats = chat_filter_config.stats()
        for mode, count in stats.get("by_mode", {}).items():
            lines.append(
                _format_metric(
                    "krab_chat_filter_modes_total",
                    count,
                    labels={"mode": mode},
                    help_text="Chats per filter mode",
                    mtype="counter",
                )
            )
    except Exception:
        pass

    # === ChatWindow stats ===
    try:
        from src.core.chat_window_manager import (
            chat_window_manager,  # type: ignore[import-not-found]
        )

        cw = chat_window_manager.stats()
        lines.append(
            _format_metric(
                "krab_chat_windows_active",
                cw.get("active_windows", 0),
                help_text="Active ChatWindow instances",
            )
        )
        lines.append(
            _format_metric(
                "krab_chat_windows_capacity",
                cw.get("capacity", 0),
                help_text="Total ChatWindow capacity (sum of all window sizes)",
            )
        )
        lines.append(
            _format_metric(
                "krab_chat_windows_total_messages",
                cw.get("total_messages", 0),
                help_text="Total messages buffered across all ChatWindows",
            )
        )
        evicted = chat_window_manager.get_eviction_counts()
        for reason, count in evicted.items():
            lines.append(
                _format_metric(
                    "krab_chat_windows_evicted_total",
                    count,
                    labels={"reason": reason},
                    help_text="Total ChatWindow evictions by reason (lru, idle)",
                    mtype="counter",
                )
            )
    except Exception:
        pass

    # === Memory query relevance score percentiles ===
    try:
        from src.core.memory_retrieval_scores import rrf_score_window

        pcts = rrf_score_window.percentiles()
        if pcts:
            for quantile, value in pcts.items():
                lines.append(
                    _format_metric(
                        f"krab_memory_query_relevance_score_{quantile}",
                        round(value, 6),
                        help_text=(
                            f"RRF score distribution {quantile} "
                            f"(last {len(rrf_score_window)} queries)"
                        ),
                    )
                )
    except Exception:
        pass

    # === Adaptive rerank usage ===
    lines.append(
        _format_metric(
            "krab_memory_adaptive_rerank_used_total",
            _pm._ADAPTIVE_RERANK_COUNTER[0],
            help_text="Total adaptive rerank invocations (MEMORY_ADAPTIVE_RERANK_ENABLED=1)",
            mtype="counter",
        )
    )

    # === Stealth detection counters ===
    try:
        from src.core.stealth_metrics import get_counts as _stealth_get_counts

        stealth_counts = _stealth_get_counts()
        if stealth_counts:
            for layer, count in stealth_counts.items():
                lines.append(
                    _format_metric(
                        "krab_stealth_detection_total",
                        count,
                        labels={"layer": layer[:30]},
                        help_text=(
                            "Anti-bot detection signals by layer "
                            "(canvas/webgl/webrtc/captcha/ratelimit/blocked)"
                        ),
                        mtype="counter",
                    )
                )
    except Exception:
        pass

    # === Telegram FloodWait ===
    lines.append("# HELP krab_telegram_flood_wait_total Telegram FloodWait incidents by caller")
    lines.append("# TYPE krab_telegram_flood_wait_total counter")
    if not _pm._TELEGRAM_FLOOD_WAIT_COUNTER:
        lines.append('krab_telegram_flood_wait_total{caller="none"} 0')
    else:
        for _fw_caller, _fw_count in _pm._TELEGRAM_FLOOD_WAIT_COUNTER.items():
            label_str = f'caller="{_sanitize_label(_fw_caller)}"'
            lines.append(f"krab_telegram_flood_wait_total{{{label_str}}} {_fw_count}")

    # === Guest LLM skip (security ACL) ===
    for _skip_reason, _skip_count in _pm._GUEST_LLM_SKIPPED_COUNTER.items():
        lines.append(
            _format_metric(
                "krab_guest_llm_skipped_total",
                _skip_count,
                labels={"reason": _skip_reason[:60]},
                help_text="LLM replies skipped for guests in groups (security ACL)",
                mtype="counter",
            )
        )

    # === Swarm per-team tool blocks ===
    try:
        from src.core.swarm_tool_allowlist import (  # type: ignore[import-not-found]
            get_blocked_tool_stats,
        )

        for (_team, _tool), _cnt in get_blocked_tool_stats().items():
            lines.append(
                _format_metric(
                    "krab_swarm_tool_blocked_total",
                    _cnt,
                    labels={"team": _team[:40], "tool": _tool[:80]},
                    help_text="Swarm per-team tool calls blocked by allowlist",
                    mtype="counter",
                )
            )
    except Exception:
        pass

    # === Session corruption counter ===
    lines.append(
        "# HELP krab_session_corruption_total DB corruption events requiring quarantine by kind"
    )
    lines.append("# TYPE krab_session_corruption_total counter")
    if not _pm._SESSION_CORRUPTION_COUNTER:
        lines.append('krab_session_corruption_total{kind="none"} 0')
    else:
        for _corr_kind, _corr_count in _pm._SESSION_CORRUPTION_COUNTER.items():
            label_str = f'kind="{_sanitize_label(_corr_kind)}"'
            lines.append(f"krab_session_corruption_total{{{label_str}}} {_corr_count}")

    # === Startup duration ===
    lines.append(
        _format_metric(
            "krab_startup_duration_seconds",
            _pm._STARTUP_DURATION_SECONDS[0],
            help_text="Время от старта процесса до kraab_running (секунды)",
        )
    )

    # === Agent Engine runs ===
    lines.append(
        "# HELP krab_agent_engine_runs_total Total agent engine runs by engine and success"
    )
    lines.append("# TYPE krab_agent_engine_runs_total counter")
    if not _pm._AGENT_ENGINE_RUNS_COUNTER:
        lines.append('krab_agent_engine_runs_total{engine="openclaw",success="0"} 0')
    else:
        for _ae_engine, _ae_bucket in _pm._AGENT_ENGINE_RUNS_COUNTER.items():
            for _ae_success, _ae_count in _ae_bucket.items():
                label_str = f'engine="{_sanitize_label(_ae_engine)}",success="{_ae_success}"'
                lines.append(f"krab_agent_engine_runs_total{{{label_str}}} {_ae_count}")

    lines.append(
        "# HELP krab_agent_engine_latency_seconds_avg Average latency of agent engine runs"
    )
    lines.append("# TYPE krab_agent_engine_latency_seconds_avg gauge")
    if not _pm._AGENT_ENGINE_LATENCY_ACC:
        lines.append('krab_agent_engine_latency_seconds_avg{engine="openclaw"} 0')
    else:
        for _ae_engine, _ae_acc in _pm._AGENT_ENGINE_LATENCY_ACC.items():
            total_sec, count = _ae_acc[0], int(_ae_acc[1])
            avg = round(total_sec / count, 4) if count > 0 else 0.0
            label_str = f'engine="{_sanitize_label(_ae_engine)}"'
            lines.append(f"krab_agent_engine_latency_seconds_avg{{{label_str}}} {avg}")

    lines.append(
        "# HELP krab_agent_engine_fallback_total Fallback events when requested engine is unhealthy"
    )
    lines.append("# TYPE krab_agent_engine_fallback_total counter")
    if not _pm._AGENT_ENGINE_FALLBACK_COUNTER:
        lines.append(
            'krab_agent_engine_fallback_total{from_engine="hermes",to_engine="openclaw"} 0'
        )
    else:
        for _ae_from, _ae_to_bucket in _pm._AGENT_ENGINE_FALLBACK_COUNTER.items():
            for _ae_to, _ae_cnt in _ae_to_bucket.items():
                label_str = (
                    f'from_engine="{_sanitize_label(_ae_from)}",'
                    f'to_engine="{_sanitize_label(_ae_to)}"'
                )
                lines.append(f"krab_agent_engine_fallback_total{{{label_str}}} {_ae_cnt}")

    # === Wave 70: dispatcher / swarm / paid Gemini guard probes ===
    try:
        from src.core.network_probes_snapshot import collect_network_probes_snapshot

        ub = _probes._get_userbot_for_metrics()
        snapshot = collect_network_probes_snapshot(ub)

        tick_ago = snapshot.get("main_dispatcher_tick_ago_sec")
        tick_ago_metric = -1.0 if tick_ago is None else float(tick_ago)
        lines.append(
            _format_metric(
                "krab_main_dispatcher_tick_ago_seconds",
                round(tick_ago_metric, 3),
                help_text=(
                    "Wave 63-C: сколько секунд назад main dispatcher последний раз "
                    "тикнул (-1 = userbot не зарегистрирован)"
                ),
            )
        )

        lines.append(
            "# HELP krab_swarm_probe_ago_seconds Wave 63-B: сколько секунд назад "
            "swarm team pts последний раз обновился"
        )
        lines.append("# TYPE krab_swarm_probe_ago_seconds gauge")
        swarm_probes = snapshot.get("swarm_probes") or {}
        if not isinstance(swarm_probes, dict) or not swarm_probes:
            lines.append('krab_swarm_probe_ago_seconds{team="none"} 0')
        else:
            for team, team_snap in swarm_probes.items():
                if not isinstance(team_snap, dict):
                    continue
                ago = team_snap.get("ago_sec")
                ago_val = -1.0 if ago is None else float(ago)
                label = f'team="{_sanitize_label(str(team)[:40])}"'
                lines.append(f"krab_swarm_probe_ago_seconds{{{label}}} {round(ago_val, 3)}")

        guard_mode = str(snapshot.get("paid_gemini_guard", {}).get("mode", "off"))
        mode_value = {"block": 1, "warn": 0, "off": -1}.get(guard_mode, -1)
        lines.append(
            _format_metric(
                "krab_paid_gemini_guard_mode",
                mode_value,
                labels={"mode": guard_mode},
                help_text=(
                    "Wave 67 guard mode: 1=block, 0=warn, -1=off (KRAB_BLOCK_PAID_GEMINI_AI_STUDIO)"
                ),
            )
        )
    except Exception:
        pass

    # === Wave 75: LaunchAgent health ===
    lines.extend(_launchd.render_launchd_metrics(_format_metric, _sanitize_label))

    # === Wave 79: Krab Ear health probe ===
    lines.extend(_krab_ear.render_krab_ear_metrics(_format_metric, _sanitize_label))

    # === Wave 86: pressure-aware select (krab_free_memory_gb + fallback counter) ===
    try:
        from ..pressure_aware_select import get_free_memory_gb as _free_gb_fn
        from .pressure_aware import _PRESSURE_AWARE_FALLBACK_COUNTER

        _free_gb = _free_gb_fn()
        if _free_gb is not None:
            lines.append(
                _format_metric(
                    "krab_free_memory_gb",
                    "Wave 86: free memory snapshot (GB) — pressure-aware model select trigger",
                    "gauge",
                    float(_free_gb),
                )
            )
        if _PRESSURE_AWARE_FALLBACK_COUNTER:
            lines.append(
                "# HELP krab_pressure_aware_fallback_total Wave 86: memory-pressure-driven model fallbacks"
            )
            lines.append("# TYPE krab_pressure_aware_fallback_total counter")
            for (from_m, to_m, reason), cnt in _PRESSURE_AWARE_FALLBACK_COUNTER.items():
                label_str = (
                    f'from_model="{_sanitize_label(from_m)}",'
                    f'to_model="{_sanitize_label(to_m)}",'
                    f'reason="{_sanitize_label(reason)}"'
                )
                lines.append(f"krab_pressure_aware_fallback_total{{{label_str}}} {cnt}")
    except Exception:  # noqa: BLE001
        pass

    # === Wave 223: long-context routing decisions (MLX local) ===
    try:
        from .long_context_routing import _MLX_LOCAL_ROUTING_COUNTER

        if _MLX_LOCAL_ROUTING_COUNTER:
            lines.append(
                "# HELP krab_mlx_local_routing_total Wave 223: routing decisions to local MLX (long context / task type)"
            )
            lines.append("# TYPE krab_mlx_local_routing_total counter")
            for reason, cnt in _MLX_LOCAL_ROUTING_COUNTER.items():
                lines.append(
                    f'krab_mlx_local_routing_total{{reason="{_sanitize_label(reason)}"}} {cnt}'
                )
    except Exception:  # noqa: BLE001
        pass

    # === Wave 121: Telegram FloodWait Gauge (refresh expired + render) ===
    try:
        from .telegram_rate import refresh_telegram_rate_limited_active

        active_snapshot = refresh_telegram_rate_limited_active()
        if active_snapshot:
            lines.append(
                "# HELP krab_telegram_rate_limited_active "
                "Wave 121: 1 пока FloodWait deadline в будущем"
            )
            lines.append("# TYPE krab_telegram_rate_limited_active gauge")
            for caller, value in active_snapshot.items():
                lines.append(
                    f'krab_telegram_rate_limited_active{{caller="{_sanitize_label(caller)}"}} {value}'
                )
    except Exception:  # noqa: BLE001
        pass

    # === Wave 142: Pyrogram reconnect counter ===
    try:
        from .pyrogram_reconnect import _PYROGRAM_DISCONNECTS_COUNTER

        lines.append(
            "# HELP krab_pyrogram_disconnects_total Wave 142: Pyrogram Connection.close events"
        )
        lines.append("# TYPE krab_pyrogram_disconnects_total counter")
        if not _PYROGRAM_DISCONNECTS_COUNTER:
            lines.append('krab_pyrogram_disconnects_total{session="none"} 0')
        else:
            for session, count in _PYROGRAM_DISCONNECTS_COUNTER.items():
                lines.append(
                    f'krab_pyrogram_disconnects_total{{session="{_sanitize_label(session)}"}} {count}'
                )
    except Exception:  # noqa: BLE001
        pass

    # === Wave 205: Memory leak detector ===
    try:
        from src.core.memory_leak_detector import get_prometheus_state

        mem_state = get_prometheus_state()
        lines.append(
            _format_metric(
                "krab_process_rss_bytes",
                mem_state["krab_process_rss_bytes"],
                help_text="Wave 205: own process RSS bytes (psutil)",
            )
        )
        lines.append(
            _format_metric(
                "krab_process_vms_bytes",
                mem_state["krab_process_vms_bytes"],
                help_text="Wave 205: own process VMS bytes (psutil)",
            )
        )
        lines.append(
            _format_metric(
                "krab_process_swap_bytes",
                mem_state["krab_process_swap_bytes"],
                help_text="Wave 205: own process swap bytes (0 if AccessDenied)",
            )
        )
        lines.append(
            _format_metric(
                "krab_memory_leak_growth_mb_per_hour",
                mem_state["krab_memory_leak_growth_mb_per_hour"],
                help_text="Wave 205: RSS growth rate over window",
            )
        )
        lines.append(
            _format_metric(
                "krab_memory_leak_suspected",
                mem_state["krab_memory_leak_suspected"],
                help_text="Wave 205: 1 if RSS growth exceeds threshold",
            )
        )
    except Exception:  # noqa: BLE001
        pass

    # === Timestamps ===
    lines.append(
        _format_metric(
            "krab_metrics_generated_at",
            int(time.time()),
            help_text="Metrics generation timestamp",
        )
    )
    lines.append(
        _format_metric(
            "krab_process_start_time_seconds",
            _pm._PROCESS_START_TIME,
            help_text="Unix timestamp когда процесс owner panel стартовал",
        )
    )

    return "\n".join(lines) + "\n"
