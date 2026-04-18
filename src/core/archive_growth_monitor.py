"""
Archive.db growth monitor — tracks daily size delta, alerts on anomaly.

Snapshot stored в ~/.openclaw/krab_runtime_state/archive_growth.json:
{
  "snapshots": [
    {"ts": 1714567890, "size_mb": 50.3, "message_count": 43199},
    ...
  ]
}
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .logger import get_logger

logger = get_logger(__name__)

STATE_PATH = Path("~/.openclaw/krab_runtime_state/archive_growth.json").expanduser()
ARCHIVE_DB = Path("~/.openclaw/krab_memory/archive.db").expanduser()
ANOMALY_MB_PER_DAY = float(os.environ.get("ARCHIVE_GROWTH_ANOMALY_MB", "100"))


@dataclass
class GrowthSnapshot:
    ts: int
    size_mb: float
    message_count: int


def take_snapshot() -> Optional[GrowthSnapshot]:
    """Take current snapshot of archive.db size and message count."""
    if not ARCHIVE_DB.exists():
        return None

    size_mb = ARCHIVE_DB.stat().st_size / 1024 / 1024

    try:
        conn = sqlite3.connect(f"file:{ARCHIVE_DB}?mode=ro", uri=True)
        count = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        conn.close()
    except Exception as e:
        logger.warning("archive_snapshot_failed", error=str(e))
        count = 0

    return GrowthSnapshot(ts=int(time.time()), size_mb=round(size_mb, 2), message_count=count)


def load_history() -> list[GrowthSnapshot]:
    """Load snapshot history from state file."""
    if not STATE_PATH.exists():
        return []

    try:
        data = json.loads(STATE_PATH.read_text())
        return [GrowthSnapshot(**s) for s in data.get("snapshots", [])]
    except Exception as e:
        logger.warning("archive_history_load_failed", error=str(e))
        return []


def save_history(snapshots: list[GrowthSnapshot]):
    """Save snapshot history to state file."""
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "snapshots": [
            {"ts": s.ts, "size_mb": s.size_mb, "message_count": s.message_count} for s in snapshots
        ]
    }
    STATE_PATH.write_text(json.dumps(data, indent=2))


def append_snapshot_and_check_anomaly() -> tuple[Optional[GrowthSnapshot], Optional[str]]:
    """Take snapshot, append to history (max 30 days), return anomaly warning."""
    snap = take_snapshot()
    if not snap:
        return None, None

    history = load_history()
    history.append(snap)

    # Keep only last 30 days
    cutoff = int(time.time()) - 30 * 86400
    history = [s for s in history if s.ts > cutoff]
    save_history(history)

    # Anomaly detection — compare with snapshot 1 day ago
    day_ago = int(time.time()) - 86400
    older = [s for s in history if s.ts < day_ago]
    if not older:
        return snap, None

    prev = older[-1]
    delta_mb = snap.size_mb - prev.size_mb
    if delta_mb > ANOMALY_MB_PER_DAY:
        msg = (
            f"⚠️ Archive.db grew {delta_mb:.1f} MB за день "
            f"({prev.size_mb:.1f} → {snap.size_mb:.1f} MB). "
            f"Threshold: {ANOMALY_MB_PER_DAY} MB/day."
        )
        logger.warning("archive_growth_anomaly", delta_mb=delta_mb, current_mb=snap.size_mb)
        return snap, msg

    return snap, None


def growth_summary() -> dict:
    """Compute growth statistics over tracked history."""
    history = load_history()
    if len(history) < 2:
        return {"snapshots": len(history), "summary": "Not enough data"}

    latest = history[-1]
    first = history[0]
    days = max(1, (latest.ts - first.ts) / 86400)

    return {
        "snapshots": len(history),
        "first_ts": first.ts,
        "latest_ts": latest.ts,
        "days_tracked": round(days, 1),
        "latest_size_mb": latest.size_mb,
        "latest_messages": latest.message_count,
        "first_size_mb": first.size_mb,
        "first_messages": first.message_count,
        "growth_mb_per_day": round((latest.size_mb - first.size_mb) / days, 2),
        "growth_messages_per_day": round((latest.message_count - first.message_count) / days, 0),
    }
