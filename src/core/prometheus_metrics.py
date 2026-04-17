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
        from src.core.auto_restart_policy import (
            auto_restart_manager,  # type: ignore[import-not-found]
        )

        for svc_name, state in getattr(auto_restart_manager, "_states", {}).items():
            attempts = getattr(state, "attempts", []) or []
            lines.append(
                _format_metric(
                    "krab_auto_restart_attempts_total",
                    len(attempts),
                    labels={"service": str(svc_name)[:50]},
                    help_text="Auto-restart attempts last hour",
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

    return "\n".join(lines) + "\n"
