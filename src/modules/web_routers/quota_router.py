# -*- coding: utf-8 -*-
"""
Quota router — Wave 29-B.

GET /api/quota — статус квот по всем провайдерам для panel UI.

Переиспользует helpers из Wave 25-D (observability_commands):
- _probe_gemini_cli
- _probe_anthropic_vertex
- _probe_vertex_gemini
- _count_today_calls

Query params:
- ?probe=true  (default) — выполнить live probe каждого провайдера
- ?probe=false            — только счётчики из лога (быстрый ответ)
"""

from __future__ import annotations

import datetime
import pathlib
from typing import Annotated

from fastapi import APIRouter, Query

from ._context import RouterContext

# Путь к лог-файлу (тот же что в observability_commands)
_LOG_FILE = pathlib.Path.home() / ".openclaw/krab_runtime_state/krab_main.log"


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

    return router
