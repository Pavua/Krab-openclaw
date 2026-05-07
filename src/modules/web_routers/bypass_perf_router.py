# -*- coding: utf-8 -*-
"""Wave 31-A: bypass latency profiler endpoint.

GET /api/bypass/perf?window=1h — агрегированная статистика latency
по всем bypass провайдерам (codex/gemini-cli/vertex/anthropic-vertex/google-direct/gemma).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from ._context import RouterContext


def build_bypass_perf_router(ctx: RouterContext) -> APIRouter:  # noqa: ARG001
    """Factory: возвращает APIRouter с /api/bypass/perf endpoint."""
    router = APIRouter(prefix="/api/bypass", tags=["bypass-perf"])

    @router.get("/perf")
    async def get_bypass_perf(
        window: Annotated[
            str, Query(description="Окно анализа: '1h', '24h', '5m' или секунды")
        ] = "1h",
        exclude_expected: Annotated[
            bool,
            Query(
                description=(
                    "Если true, отбрасывает known transient failures "
                    "(quota/permission) — для alert pipeline. Session 39."
                ),
            ),
        ] = False,
    ) -> dict:
        """Агрегированная latency статистика bypass вызовов.

        Читает bypass_perf.jsonl и возвращает p50/p95/p99/mean/fail_rate
        по каждому kind (cli/vertex/anthropic-vertex/google-direct/gemma)
        и по каждой конкретной модели.

        - window=1h  — последний час (default)
        - window=24h — последние 24 часа
        - window=5m  — последние 5 минут
        - exclude_expected=true — отбрасывает quota/permission failures
        """
        from src.integrations._bypass_perf import aggregate_perf, parse_duration

        window_sec = parse_duration(window)
        stats = aggregate_perf(window_sec=window_sec, exclude_expected=exclude_expected)
        return {"ok": True, **stats}

    return router
