# -*- coding: utf-8 -*-
"""Agent Engine Metrics Router — /api/agent-engine/*.

Wave 17-B (Hermes Phase C): A/B comparison endpoints для мониторинга
разницы между OpenClaw и Hermes по качеству, latency, cost.

Endpoints:
    GET /api/agent-engine/comparison?window=7d
    GET /api/agent-engine/runs?engine=openclaw&limit=100
"""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Query

from ._context import RouterContext


def build_agent_engine_metrics_router(ctx: RouterContext) -> APIRouter:  # noqa: ARG001
    """Factory — создаёт и возвращает router.

    ctx не используется сейчас (endpoints не требуют WebApp deps),
    но принимается для единообразия с другими build_X_router().
    """
    router = APIRouter(prefix="/api/agent-engine", tags=["agent-engine"])

    @router.get("/comparison")
    async def comparison(
        window: str = Query("7d", description="Окно: 7d, 24h, 30d"),
    ) -> dict[str, Any]:
        """Сравнение OpenClaw vs Hermes за последний период.

        Агрегаты по engine: runs, success_rate, avg_latency_ms, tokens, cost.
        """
        window_days = _parse_window(window)
        try:
            from ...core.agent_engine_runs import get_engine_comparison

            return get_engine_comparison(window_days=window_days)
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc), "window_days": window_days}

    @router.get("/runs")
    async def runs(
        engine: str | None = Query(None, description="Фильтр по engine: openclaw|hermes"),
        limit: int = Query(100, ge=1, le=1000, description="Максимум записей"),
        offset: int = Query(0, ge=0, description="Смещение для пагинации"),
    ) -> dict[str, Any]:
        """Список agent_engine_runs из archive.db.

        Отсортированы по времени (новые первые).
        """
        try:
            from ...core.agent_engine_runs import list_engine_runs

            items = list_engine_runs(engine=engine, limit=limit, offset=offset)
            return {
                "ok": True,
                "engine_filter": engine,
                "count": len(items),
                "offset": offset,
                "limit": limit,
                "items": items,
            }
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    @router.get("/status")
    async def status() -> dict[str, Any]:
        """Статус dispatch: включён/выключен + текущий engine для default route."""
        import os

        dispatch_enabled = os.environ.get("KRAB_AGENT_ENGINE_DISPATCH_ENABLED", "0") in {
            "1",
            "true",
            "yes",
        }
        default_engine = os.environ.get("KRAB_AGENT_ENGINE", "openclaw")
        return {
            "dispatch_enabled": dispatch_enabled,
            "default_engine": default_engine,
            "env": {
                "KRAB_AGENT_ENGINE_DISPATCH_ENABLED": os.environ.get(
                    "KRAB_AGENT_ENGINE_DISPATCH_ENABLED", "0"
                ),
                "KRAB_AGENT_ENGINE": os.environ.get("KRAB_AGENT_ENGINE", "openclaw"),
            },
        }

    @router.get("/dispatch-status")
    async def dispatch_status() -> dict[str, Any]:
        """Wave 38-B: Полный статус dispatch — включён/выключен, доступные engines, skill_curator cron.

        Endpoint для быстрой проверки состояния Phase D wire-up:
        - dispatch_enabled: True когда KRAB_AGENT_ENGINE_DISPATCH_ENABLED=1
        - skill_curator_cron_enabled: True когда KRAB_SKILL_CURATOR_CRON_ENABLED=1
        - engines_available: список поддерживаемых engine-kind
        - default_engine: openclaw (дефолт при dispatch OFF)
        """
        import os

        _enabled_vals = {"1", "true", "yes"}

        dispatch_enabled = (
            os.environ.get("KRAB_AGENT_ENGINE_DISPATCH_ENABLED", "0").strip() in _enabled_vals
        )
        skill_curator_cron_enabled = (
            os.environ.get("KRAB_SKILL_CURATOR_CRON_ENABLED", "0").strip() in _enabled_vals
        )
        skill_curator_ab_enabled = (
            os.environ.get("KRAB_SKILL_CURATOR_AB_ENABLED", "0").strip() in _enabled_vals
        )

        return {
            "ok": True,
            "dispatch_enabled": dispatch_enabled,
            "engines_available": ["openclaw", "hermes", "auto"],
            "default_engine": "openclaw",
            "skill_curator_cron_enabled": skill_curator_cron_enabled,
            "skill_curator_ab_enabled": skill_curator_ab_enabled,
            "env": {
                "KRAB_AGENT_ENGINE_DISPATCH_ENABLED": os.environ.get(
                    "KRAB_AGENT_ENGINE_DISPATCH_ENABLED", "0"
                ),
                "KRAB_SKILL_CURATOR_CRON_ENABLED": os.environ.get(
                    "KRAB_SKILL_CURATOR_CRON_ENABLED", "0"
                ),
            },
            "wire_up": "Wave 38-B: swarm.py _dispatch_route_query + cron 04:00 UTC",
        }

    return router


def _parse_window(window: str) -> int:
    """Парсит строку вида '7d', '24h', '30d' в количество дней.

    Поддерживает суффиксы: d (дни), h (часы). Default 7d.
    """
    window = window.strip().lower()
    m = re.match(r"^(\d+)(d|h)?$", window)
    if not m:
        return 7
    value = int(m.group(1))
    unit = m.group(2) or "d"
    if unit == "h":
        # Переводим часы в дни (минимум 1)
        return max(1, value // 24)
    return max(1, min(value, 365))
