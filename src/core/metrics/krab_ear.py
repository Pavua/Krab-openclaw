# -*- coding: utf-8 -*-
"""Wave 79: Krab Ear health probe exposition.

Snapshot обновляется фоновым KrabEarHealthProbe каждые 60 секунд. Здесь —
только рендеринг в Prometheus text.
"""

from __future__ import annotations

import time


def render_krab_ear_metrics(format_fn, sanitize_fn) -> list[str]:
    """Возвращает Prometheus text строк по KE health snapshot. Fail-safe.

    format_fn — фасадная _format_metric helper.
    sanitize_fn — фасадная _sanitize_label helper.
    """
    lines: list[str] = []
    try:
        from src.core.krab_ear_health_probe import get_snapshot as _ke_get_snapshot

        snap = _ke_get_snapshot()
        now = time.time()
        last_success = float(snap.get("last_success_ts") or 0.0)
        ago = -1.0 if last_success <= 0 else max(0.0, now - last_success)
        lines.append(
            format_fn(
                "krab_ear_probe_last_ago_seconds",
                round(ago, 3),
                help_text=(
                    "Wave 79: секунд с последнего успешного probe Krab Ear /health "
                    "(-1 = probe ни разу не отработал)"
                ),
            )
        )
        lines.append(
            format_fn(
                "krab_ear_consecutive_failures",
                int(snap.get("consecutive_failures", 0) or 0),
                help_text=(
                    "Wave 79: длина текущей streak отказов KE probe (0 если последний probe ok)"
                ),
            )
        )
        lines.append("# HELP krab_ear_probe_failures_total Wave 79: KE probe отказы по причинам")
        lines.append("# TYPE krab_ear_probe_failures_total counter")
        failures = snap.get("failures_by_reason") or {}
        if not isinstance(failures, dict) or not failures:
            lines.append('krab_ear_probe_failures_total{reason="none"} 0')
        else:
            for reason, cnt in failures.items():
                r_safe = sanitize_fn(str(reason)[:40])
                lines.append(f'krab_ear_probe_failures_total{{reason="{r_safe}"}} {int(cnt)}')
    except Exception:  # noqa: BLE001
        pass
    return lines
