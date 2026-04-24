# -*- coding: utf-8 -*-
"""
Prometheus metrics для Krab — simple text format (без prometheus_client).

Собираем счётчики/гейджи вручную и отдаём в text/plain version=0.0.4.
Все импорты опциональных модулей завёрнуты в try/except — missing модули
не ломают /metrics endpoint.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)

# Процесс стартовал в этот момент (unix ts). Используется для krab uptime gauge.
_PROCESS_START_TIME: float = time.time()

# Счётчик использований adaptive rerank (mutable singleton для hot-path инкремента).
_ADAPTIVE_RERANK_COUNTER: list[int] = [0]

# Security: счётчик пропущенных LLM-ответов гостям в группах (SwMaster incident 2026-04-21).
# Словарь reason → count. Инкрементируется из userbot_bridge._process_message_serialized.
_GUEST_LLM_SKIPPED_COUNTER: dict[str, int] = {}

# === C6: Memory retrieval метрики (prometheus_client). ===
# Регистрируем один раз на уровне модуля. Если prometheus_client отсутствует —
# объекты становятся None, а вызывающий код (memory_retrieval.search) делает
# None-check перед inc/observe. Это сохраняет совместимость dev-окружений без
# опциональной зависимости.
try:
    from prometheus_client import Counter as _Counter  # type: ignore[import-not-found]
    from prometheus_client import Histogram as _Histogram  # type: ignore[import-not-found]

    _memory_retrieval_mode_total = _Counter(
        "krab_memory_retrieval_mode_total",
        "Количество retrieval queries по режиму (fts/vec/hybrid/none)",
        ["mode"],
    )
    _memory_retrieval_latency_seconds = _Histogram(
        "krab_memory_retrieval_latency_seconds",
        "Latency retrieval per phase (fts/vec/mmr/total)",
        ["phase"],
        buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
    )
except Exception:  # noqa: BLE001 - prometheus_client optional
    _memory_retrieval_mode_total = None  # type: ignore[assignment]
    _memory_retrieval_latency_seconds = None  # type: ignore[assignment]


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
                    # No embedding column
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
            # Заголовок один раз на имя (упрощённо — выводим перед первым bucket)
            lines.append(f"# HELP {metric_name} LLM route latency histogram (seconds)")
            lines.append(f"# TYPE {metric_name} histogram")
            for le_str, cnt in series["buckets"].items():
                label_str = (
                    f'provider="{_sanitize_label(provider)}",'
                    f'model="{_sanitize_label(model)}",'
                    f'le="{le_str}"'
                )
                lines.append(f"{metric_name}_bucket{{{label_str}}} {cnt}")
            # sum / count
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
                        help_text=f"RRF score distribution {quantile} (last {len(rrf_score_window)} queries)",
                    )
                )
    except Exception:
        pass

    # === Adaptive rerank usage ===
    lines.append(
        _format_metric(
            "krab_memory_adaptive_rerank_used_total",
            _ADAPTIVE_RERANK_COUNTER[0],
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
                        help_text="Anti-bot detection signals by layer (canvas/webgl/webrtc/captcha/ratelimit/blocked)",
                        mtype="counter",
                    )
                )
    except Exception:
        pass

    # === Guest LLM skip (security ACL) ===
    for _skip_reason, _skip_count in _GUEST_LLM_SKIPPED_COUNTER.items():
        lines.append(
            _format_metric(
                "krab_guest_llm_skipped_total",
                _skip_count,
                labels={"reason": _skip_reason[:60]},
                help_text="LLM replies skipped for guests in groups (security ACL)",
                mtype="counter",
            )
        )

    # === Swarm per-team tool blocks (silent strip) ===
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
            _PROCESS_START_TIME,
            help_text="Unix timestamp когда процесс owner panel стартовал",
        )
    )

    return "\n".join(lines) + "\n"
