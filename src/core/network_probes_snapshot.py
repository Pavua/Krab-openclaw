# -*- coding: utf-8 -*-
"""Wave 65-K: snapshot Wave 63 network probe state для observability.

Контекст: Wave 63-A/B/C добавили несколько полей на ``KraabUserbot`` для
отслеживания liveness/dispatcher split-brain (см. ``network_watchdog.py``,
``swarm_team_clients.py``). До этого Wave они были видны только через логи.
Этот модуль собирает snapshot, который exposed через
``GET /api/network/probes`` (system_router).

Дизайн:
    * fail-open: если у owner нет нужного атрибута — возвращаем 0/None
      без падения. Любой ловимый Exception превращается в пустой dict.
    * never raise: endpoint никогда не должен валиться из-за внутренних
      инвариантов (используется внешним watchdog).
"""

from __future__ import annotations

import os
import time
from typing import Any


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _paid_gemini_guard_mode() -> str:
    """Текущий режим Wave 67 guard.

    Возвращает ``'block' | 'warn' | 'off'``. Логика идентична
    ``paid_gemini_guard._guard_mode`` (не импортируем напрямую — guard
    может быть не инициализирован при cold start tests).
    """
    raw = str(os.environ.get("KRAB_BLOCK_PAID_GEMINI_AI_STUDIO", "1")).strip().lower()
    if raw in {"0", "false", "no", "off", "disabled"}:
        return "off"
    if raw == "warn":
        return "warn"
    return "block"


def _paid_gemini_guard_stats() -> dict[str, Any]:
    """Snapshot counters Wave 69. Fail-open: пустой dict при сбое импорта."""
    try:
        from src.integrations.paid_gemini_guard import get_paid_gemini_guard_stats

        return get_paid_gemini_guard_stats()
    except Exception:  # noqa: BLE001
        return {
            "blocked_count": 0,
            "allowed_count": 0,
            "warned_count": 0,
            "last_blocked_at": None,
            "last_blocked_host": None,
            "last_blocked_model": None,
        }


def _paid_gemini_guard_section() -> dict[str, Any]:
    """Sub-section ``paid_gemini_guard`` для snapshot dict."""
    section: dict[str, Any] = {"mode": _paid_gemini_guard_mode()}
    section.update(_paid_gemini_guard_stats())
    return section


def collect_network_probes_snapshot(userbot: Any, *, now: float | None = None) -> dict[str, Any]:
    """Собирает snapshot всех Wave 63 network probe полей.

    Args:
        userbot: ссылка на ``KraabUserbot`` (или duck-type с нужными
            атрибутами). Может быть ``None`` — тогда возвращается пустой
            dict с пометкой ``available=False``.
        now: optional override для тестов (UNIX timestamp).

    Returns:
        Dict со структурой::

            {
                "available": bool,
                "main_dispatcher_tick_count": int,
                "main_dispatcher_tick_ago_sec": float | None,
                "main_last_event_ago_sec": float | None,
                "main_last_seen_update_id": int,
                "swarm_probes": {team: {pts, qts, seq, date, ago_sec}},
                "paid_gemini_guard": {"mode": str},
            }
    """
    ts_now = float(now) if now is not None else time.time()

    if userbot is None:
        return {
            "available": False,
            "main_dispatcher_tick_count": 0,
            "main_dispatcher_tick_ago_sec": None,
            "main_last_event_ago_sec": None,
            "main_last_seen_update_id": 0,
            "swarm_probes": {},
            "paid_gemini_guard": _paid_gemini_guard_section(),
        }

    tick_count = _safe_int(getattr(userbot, "_dispatcher_tick_count", 0))

    tick_ts_raw = getattr(userbot, "_last_dispatcher_tick_ts", None)
    tick_ago: float | None
    if tick_ts_raw is None:
        tick_ago = None
    else:
        tick_ago = max(0.0, ts_now - _safe_float(tick_ts_raw))

    event_ts_raw = getattr(userbot, "_last_telegram_event_ts", None)
    event_ago: float | None
    if event_ts_raw is None:
        event_ago = None
    else:
        event_ago = max(0.0, ts_now - _safe_float(event_ts_raw))

    last_update_id = _safe_int(getattr(userbot, "_last_seen_update_id", 0))

    # Session 53 P3.6: raw_update tick exposes Pyrogram dispatcher liveness
    # независимо от message handler chain. Используется для disambiguation:
    # raw alive + message stale → filter chain broken; both stale → silent-death.
    raw_tick_count = _safe_int(getattr(userbot, "_raw_update_tick_count", 0))
    raw_tick_ts_raw = getattr(userbot, "_last_raw_update_ts", None)
    raw_tick_ago: float | None
    if raw_tick_ts_raw is None:
        raw_tick_ago = None
    else:
        raw_tick_ago = max(0.0, ts_now - _safe_float(raw_tick_ts_raw))

    swarm_probes: dict[str, dict[str, Any]] = {}
    try:
        raw_swarm = getattr(userbot, "_last_swarm_pts", None) or {}
        if isinstance(raw_swarm, dict):
            for team, snapshot in raw_swarm.items():
                if not isinstance(snapshot, dict):
                    continue
                snap_ts = _safe_float(snapshot.get("ts", 0))
                swarm_probes[str(team)] = {
                    "pts": _safe_int(snapshot.get("pts")),
                    "qts": _safe_int(snapshot.get("qts")),
                    "seq": _safe_int(snapshot.get("seq")),
                    "date": _safe_int(snapshot.get("date")),
                    "ago_sec": max(0.0, ts_now - snap_ts) if snap_ts else None,
                }
    except Exception:  # noqa: BLE001
        swarm_probes = {}

    return {
        "available": True,
        "main_dispatcher_tick_count": tick_count,
        "main_dispatcher_tick_ago_sec": tick_ago,
        "main_raw_update_tick_count": raw_tick_count,
        "main_raw_update_tick_ago_sec": raw_tick_ago,
        "main_last_event_ago_sec": event_ago,
        "main_last_seen_update_id": last_update_id,
        "swarm_probes": swarm_probes,
        "paid_gemini_guard": _paid_gemini_guard_section(),
    }
