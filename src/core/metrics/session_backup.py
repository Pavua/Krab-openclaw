# -*- coding: utf-8 -*-
"""Wave 118: Prometheus метрики session backup integrity.

- Gauge `krab_session_backup_valid_count` — кол-во session backups, у которых
  auth key читается (sessions table ≥1 row).
- Gauge `krab_session_backup_corrupt_count` — кол-во backups с corrupted
  auth/sessions table. Alert `SessionBackupCorrupt` >0 for 1h → critical.

prometheus_client опционален: при отсутствии все объекты None, helper'ы no-op.
"""

from __future__ import annotations

from typing import Any

krab_session_backup_valid_count: Any = None
krab_session_backup_corrupt_count: Any = None

try:
    from prometheus_client import Gauge as _Gauge  # type: ignore[import-not-found]

    krab_session_backup_valid_count = _Gauge(
        "krab_session_backup_valid_count",
        "Кол-во Pyrofork session backups, у которых auth key читается",
    )
    krab_session_backup_corrupt_count = _Gauge(
        "krab_session_backup_corrupt_count",
        "Кол-во session backups с unreadable auth (Wave 118)",
    )
except Exception:  # noqa: BLE001
    krab_session_backup_valid_count = None
    krab_session_backup_corrupt_count = None


def set_counts(*, valid: int, corrupt: int) -> None:
    """Атомарно обновляет обе gauge'и. Best-effort."""
    if krab_session_backup_valid_count is not None:
        try:
            krab_session_backup_valid_count.set(max(0, int(valid)))
        except Exception:  # noqa: BLE001
            pass
    if krab_session_backup_corrupt_count is not None:
        try:
            krab_session_backup_corrupt_count.set(max(0, int(corrupt)))
        except Exception:  # noqa: BLE001
            pass
