# -*- coding: utf-8 -*-
"""Wave 75: LaunchAgent health exposition (используется только в collect_metrics).

Snapshot обновляется фоновым LaunchdHealthMonitor. Здесь — только helper
рендеринга в Prometheus text format.
"""

from __future__ import annotations


def render_launchd_metrics(format_fn, sanitize_fn) -> list[str]:  # noqa: ARG001 — format_fn зарезервирован для будущей расширяемости
    """Возвращает Prometheus text строк по launchd snapshot. Fail-safe."""
    lines: list[str] = []
    try:
        from src.core.launchd_health_monitor import get_snapshot as _launchd_get_snapshot

        snap = _launchd_get_snapshot()
        lines.append(
            "# HELP krab_launchd_last_exit_status Last exit status from launchctl list "
            "(0=success, >0=failure, <0=SIGTERM/SIGKILL normal)"
        )
        lines.append("# TYPE krab_launchd_last_exit_status gauge")
        lines.append(
            "# HELP krab_launchd_running 1 if launchctl reports a PID for the label, 0 otherwise"
        )
        lines.append("# TYPE krab_launchd_running gauge")
        if not snap:
            lines.append('krab_launchd_last_exit_status{label="none"} 0')
            lines.append('krab_launchd_running{label="none"} 0')
        else:
            for label, data in snap.items():
                label_safe = sanitize_fn(str(label)[:80])
                exit_status = int(data.get("exit_status", 0) or 0)
                pid = data.get("pid")
                running = 1 if pid is not None else 0
                lines.append(f'krab_launchd_last_exit_status{{label="{label_safe}"}} {exit_status}')
                lines.append(f'krab_launchd_running{{label="{label_safe}"}} {running}')
    except Exception:  # noqa: BLE001
        pass
    return lines
