# -*- coding: utf-8 -*-
"""Wave 103: per-chat heat score Prometheus Gauge.

Exposes `krab_chat_heat_score{chat_id, mode}` для observability priority routing.
Score 0..1 показывает "горячесть" чата (mention rate, explicit Qs, owner engagement,
member-count inverse). Высокий score → высокий приоритет.
"""

from __future__ import annotations

try:
    from prometheus_client import Gauge as _GaugeHeat  # type: ignore[import-not-found]

    krab_chat_heat_score = _GaugeHeat(
        "krab_chat_heat_score",
        "Per-chat heat score (0..1) для priority routing (Wave 103)",
        ["chat_id", "mode"],
    )
except Exception:  # noqa: BLE001
    krab_chat_heat_score = None  # type: ignore[assignment]


def record_chat_heat_score(chat_id: str, mode: str, score: float) -> None:
    """Wave 103: устанавливает Gauge значение score для (chat_id, mode). Fail-safe."""
    try:
        if krab_chat_heat_score is None:
            return
        cid = str(chat_id)
        md = str(mode) if mode else "unknown"
        val = max(0.0, min(1.0, float(score)))
        krab_chat_heat_score.labels(chat_id=cid, mode=md).set(val)
    except Exception:  # noqa: BLE001
        pass
