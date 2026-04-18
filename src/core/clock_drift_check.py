"""Проверка дрейфа системных часов macOS относительно NTP.

Диагностический помощник для Pyrogram warning `msg_id is lower than stored`.
Pyrogram требует drift < 30s от NTP-эквивалента Telegram-сервера.
"""

from __future__ import annotations

import asyncio
import subprocess
import time
from dataclasses import dataclass

from .logger import get_logger
from .subprocess_env import clean_subprocess_env

logger = get_logger(__name__)


@dataclass
class ClockDriftResult:
    """Результат одной sntp-выборки."""

    local_ts: float
    ntp_offset_sec: float | None  # положительный = local опережает NTP
    status: str  # "ok" | "drift_warning" | "drift_critical" | "unavailable"
    message: str


def _parse_offset(stdout: str) -> float | None:
    """Парсит offset из первого токена вида `+0.045678` или `-0.045678`."""
    for line in stdout.splitlines():
        tokens = line.strip().split()
        for token in tokens:
            if not token or token[0] not in ("+", "-"):
                continue
            if "." not in token or "/" in token:
                continue
            try:
                return float(token)
            except ValueError:
                continue
    return None


def check_clock_drift_sync(ntp_server: str = "time.apple.com") -> ClockDriftResult:
    """Запускает `sntp -t 5 <server>` и парсит offset. Sync-вариант."""
    local_ts = time.time()
    try:
        result = subprocess.run(
            ["sntp", "-t", "5", ntp_server],
            capture_output=True,
            text=True,
            timeout=8,
            env=clean_subprocess_env(),
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.warning(
            "clock_drift_sntp_failed error_type=%s error=%s",
            type(exc).__name__,
            exc,
        )
        return ClockDriftResult(local_ts, None, "unavailable", f"sntp failed: {exc}")

    if result.returncode != 0:
        return ClockDriftResult(
            local_ts, None, "unavailable", f"sntp rc={result.returncode}"
        )

    offset = _parse_offset(result.stdout)
    if offset is None:
        return ClockDriftResult(local_ts, None, "unavailable", "parse failed")

    abs_offset = abs(offset)
    if abs_offset > 30:
        status = "drift_critical"
    elif abs_offset > 5:
        status = "drift_warning"
    else:
        status = "ok"

    logger.info(
        "clock_drift_checked status=%s offset_sec=%+.3f server=%s",
        status,
        offset,
        ntp_server,
    )
    return ClockDriftResult(
        local_ts,
        offset,
        status,
        f"offset={offset:+.3f}s vs {ntp_server}",
    )


async def check_clock_drift(ntp_server: str = "time.apple.com") -> ClockDriftResult:
    """Async-обёртка поверх sync-реализации."""
    return await asyncio.to_thread(check_clock_drift_sync, ntp_server)
