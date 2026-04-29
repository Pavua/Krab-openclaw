# -*- coding: utf-8 -*-
"""
Meta router — Phase 2 extraction (Session 25).

Stateless system meta endpoints: hostname/platform info, NTP clock drift.

Эти endpoints НЕ требуют RouterContext (deps) — pure functions с
импортами из ``..core.*``. Идеальные кандидаты для второго раунда
extraction после version_router proof-of-concept.

Контракт ответов сохранён 1:1 с inline definitions из web_app.py
(до Session 25 db6d9fd extraction).
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["meta"])


@router.get("/api/system/info")
async def system_info() -> dict:
    """Системная информация о хосте (platform, RAM, disk).

    Stateless: только psutil + platform stdlib calls.
    """
    import platform

    import psutil

    return {
        "ok": True,
        "hostname": platform.node(),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "cpu_count": psutil.cpu_count(),
        "ram_total_gb": round(psutil.virtual_memory().total / (1024**3), 1),
        "ram_used_pct": psutil.virtual_memory().percent,
        "disk_used_pct": psutil.disk_usage("/").percent,
    }


@router.get("/api/system/clock_drift")
async def system_clock_drift() -> dict:
    """Дрейф системных часов относительно NTP.

    Используется для диагностики Pyrogram msg_id mismatch (если local
    clock сильно расходится с NTP, MTProto session ловит protocol errors).
    """
    from ...core.clock_drift_check import check_clock_drift

    result = await check_clock_drift()
    return {
        "local_ts": result.local_ts,
        "ntp_offset_sec": result.ntp_offset_sec,
        "status": result.status,
        "message": result.message,
    }
