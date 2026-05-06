# -*- coding: utf-8 -*-
"""
Quota router — Wave 29-B / Wave 34-A.

GET /api/quota          — статус квот по всем провайдерам для panel UI.
GET /api/quota/history  — агрегированная история квот из JSONL-лога.

Переиспользует helpers из Wave 25-D (observability_commands):
- _probe_gemini_cli
- _probe_anthropic_vertex
- _probe_vertex_gemini
- _count_today_calls

Query params (quota):
- ?probe=true  (default) — выполнить live probe каждого провайдера
- ?probe=false            — только счётчики из лога (быстрый ответ)

Query params (quota/history):
- ?window=1h|6h|24h|7d|30d  — временное окно (default 24h)
"""

from __future__ import annotations

import datetime
import json
import pathlib
import time
from typing import Annotated

from fastapi import APIRouter, Query

from ._context import RouterContext

# Путь к лог-файлу (тот же что в observability_commands)
_LOG_FILE = pathlib.Path.home() / ".openclaw/krab_runtime_state/krab_main.log"

# Путь к JSONL-лог quota history (Wave 34-A)
_QUOTA_HISTORY_LOG = pathlib.Path.home() / ".openclaw/krab_runtime_state/quota_history.jsonl"

# Допустимые окна и соответствующие секунды
_WINDOW_SECONDS: dict[str, int] = {
    "1h": 3_600,
    "6h": 6 * 3_600,
    "24h": 24 * 3_600,
    "7d": 7 * 24 * 3_600,
    "30d": 30 * 24 * 3_600,
}


def build_quota_router(ctx: RouterContext) -> APIRouter:  # noqa: ARG001
    """Factory: возвращает APIRouter с /api/quota endpoint."""
    router = APIRouter(tags=["quota"])

    @router.get("/api/quota")
    async def get_quota(probe: Annotated[bool, Query()] = True) -> dict:
        """
        Статус квот по всем провайдерам.

        - probe=true  — запускает live probe (15s timeout на gemini-cli)
        - probe=false — только счётчики из лога, мгновенно
        """
        # Импорт helpers из Wave 25-D
        from src.handlers.commands.observability_commands import (
            _count_today_calls,
            _probe_anthropic_vertex,
            _probe_gemini_cli,
            _probe_vertex_gemini,
        )

        today_str = datetime.datetime.now().strftime("%Y-%m-%d")

        # Считаем вызовы за сегодня из лога
        counts = _count_today_calls(_LOG_FILE, today_str)

        if probe:
            import asyncio

            # Параллельные probe — не блокируем UI надолго
            gemini_status, anthropic_status, vertex_status = await asyncio.gather(
                _probe_gemini_cli(),
                _probe_anthropic_vertex(),
                _probe_vertex_gemini(),
            )
        else:
            gemini_status = anthropic_status = vertex_status = "skipped"

        return {
            "ok": True,
            "date": today_str,
            "providers": {
                "gemini-cli": {
                    "tier": "free OAuth",
                    "tier_limit": "~1000/day shared",
                    "probe": gemini_status,
                    "today_calls": counts.get("gemini", 0),
                },
                "codex-cli": {
                    "tier": "ChatGPT Plus subscription",
                    "today_calls": counts.get("codex", 0),
                },
                "google-vertex": {
                    "tier": "€848 credits до 2027-03",
                    "probe_model": "gemini-2.5-flash",
                    "probe": vertex_status,
                    "today_calls": counts.get("vertex", 0),
                },
                "anthropic-vertex": {
                    "tier": "Vertex Anthropic, ждёт quota approval",
                    "probe_model": "claude-haiku-4-5",
                    "probe": anthropic_status,
                    "today_calls": counts.get("anthropic", 0),
                },
            },
        }

    @router.get("/api/quota/history")
    async def get_quota_history(window: Annotated[str, Query()] = "24h") -> dict:
        """
        Wave 34-A: агрегированная история квот.

        Читает JSONL-лог quota_history.jsonl (пишется hourly LaunchAgent),
        фильтрует по временному окну и агрегирует max(today_calls) per day per provider.

        Query: ?window=1h|6h|24h|7d|30d
        """
        # Нормализуем окно
        seconds = _WINDOW_SECONDS.get(window, _WINDOW_SECONDS["24h"])
        cutoff = time.time() - seconds

        if not _QUOTA_HISTORY_LOG.exists():
            return {
                "ok": True,
                "window": window,
                "snapshots_count": 0,
                "snapshots": [],
                "aggregated": {},
            }

        snapshots: list[dict] = []
        with _QUOTA_HISTORY_LOG.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    snap = json.loads(line)
                    if snap.get("ts", 0) >= cutoff:
                        snapshots.append(snap)
                except (json.JSONDecodeError, ValueError):
                    continue  # повреждённые строки пропускаем

        # Агрегация: max today_calls per (date, provider)
        # Результат: {"2026-05-06": {"gemini-cli": 42, "codex-cli": 7, ...}, ...}
        aggregated: dict[str, dict[str, int]] = {}
        for snap in snapshots:
            if not snap.get("ok") or "providers" not in snap:
                continue
            ts = snap.get("ts", 0)
            date_str = (
                datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc).date().isoformat()
            )
            day_data = aggregated.setdefault(date_str, {})
            for prov, info in snap["providers"].items():
                calls = info.get("today_calls", 0)
                if isinstance(calls, int):
                    day_data[prov] = max(day_data.get(prov, 0), calls)

        return {
            "ok": True,
            "window": window,
            "snapshots_count": len(snapshots),
            "aggregated": aggregated,
        }

    return router
