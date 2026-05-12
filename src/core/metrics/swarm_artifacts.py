# -*- coding: utf-8 -*-
"""Wave 134: swarm artifact storage size gauges.

Gauges:
- krab_swarm_artifacts_total{team} — count per team
- krab_swarm_artifacts_size_mb — total size across all teams (MB)

Helper set_swarm_artifacts_metrics(kept_per_team, size_mb) — overwrite snapshot.
"""

from __future__ import annotations

try:
    from prometheus_client import Gauge as _GaugeSA  # type: ignore[import-not-found]

    _swarm_artifacts_total = _GaugeSA(
        "krab_swarm_artifacts_total",
        "Wave 134: artifact count per team",
        ["team"],
    )
    _swarm_artifacts_size_mb = _GaugeSA(
        "krab_swarm_artifacts_size_mb",
        "Wave 134: total artifact storage size (MB)",
    )
except Exception:  # noqa: BLE001
    _swarm_artifacts_total = None  # type: ignore[assignment]
    _swarm_artifacts_size_mb = None  # type: ignore[assignment]


_SWARM_ARTIFACTS_TOTAL: dict[str, int] = {}
_SWARM_ARTIFACTS_SIZE_MB: list[float] = [0.0]


def set_swarm_artifacts_metrics(kept_per_team: dict, size_mb: float) -> None:
    """Wave 134: overwrite snapshot. Best-effort."""
    try:
        _SWARM_ARTIFACTS_TOTAL.clear()
        for team, count in (kept_per_team or {}).items():
            try:
                _SWARM_ARTIFACTS_TOTAL[str(team)[:40]] = int(count)
                if _swarm_artifacts_total is not None:
                    _swarm_artifacts_total.labels(team=str(team)[:40]).set(int(count))
            except Exception:  # noqa: BLE001
                continue
        _SWARM_ARTIFACTS_SIZE_MB[0] = max(0.0, float(size_mb))
        if _swarm_artifacts_size_mb is not None:
            _swarm_artifacts_size_mb.set(_SWARM_ARTIFACTS_SIZE_MB[0])
    except Exception:  # noqa: BLE001
        pass


__all__ = [
    "_SWARM_ARTIFACTS_SIZE_MB",
    "_SWARM_ARTIFACTS_TOTAL",
    "_swarm_artifacts_size_mb",
    "_swarm_artifacts_total",
    "set_swarm_artifacts_metrics",
]
