# -*- coding: utf-8 -*-
"""Wave 75 + Wave 88: LaunchAgent health + cron schedule audit exposition.

Snapshot Wave 75 обновляется фоновым LaunchdHealthMonitor;
Wave 88 snapshot — weekly LaunchAgent ai.krab.cron-schedule-audit
(JSON в ~/.openclaw/krab_runtime_state/cron_schedule_audit.json).
Здесь — только helper рендеринга в Prometheus text format.
"""

from __future__ import annotations

import json
from pathlib import Path


def _render_cron_schedule_audit(sanitize_fn) -> list[str]:
    """Wave 88: gauge krab_cron_schedule_stale{label} (1 stale / 0 fresh)."""
    lines: list[str] = []
    try:
        snapshot_path = (
            Path.home() / ".openclaw" / "krab_runtime_state" / "cron_schedule_audit.json"
        )
        if not snapshot_path.exists():
            return lines
        try:
            data = json.loads(snapshot_path.read_text())
        except (json.JSONDecodeError, OSError):
            return lines
        all_agents = data.get("all_agents") or []
        if not isinstance(all_agents, list):
            return lines
        lines.append(
            "# HELP krab_cron_schedule_stale Wave 88: 1 if launchd log mtime > 2× expected interval"
        )
        lines.append("# TYPE krab_cron_schedule_stale gauge")
        for agent in all_agents:
            if not isinstance(agent, dict):
                continue
            label = agent.get("label")
            if not label:
                continue
            label_safe = sanitize_fn(str(label)[:80])
            stale = 1 if agent.get("stale_cron") else 0
            lines.append(f'krab_cron_schedule_stale{{label="{label_safe}"}} {stale}')
    except Exception:  # noqa: BLE001 — fail-safe metrics
        pass
    return lines


def render_launchd_metrics(format_fn, sanitize_fn) -> list[str]:  # noqa: ARG001 — format_fn зарезервирован для будущей расширяемости
    """Возвращает Prometheus text строк по launchd snapshot + cron audit. Fail-safe."""
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

    lines.extend(_render_cron_schedule_audit(sanitize_fn))
    return lines
