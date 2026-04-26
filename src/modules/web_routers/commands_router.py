# -*- coding: utf-8 -*-
"""
Commands router — Phase 2 Wave A extraction (Session 25).

Stateless endpoints для command_registry: список команд, usage статистика,
top-N usage и детальная инфо о конкретной команде.

Все 4 endpoints НЕ требуют RouterContext (deps) — используют
``core.command_registry`` singleton-функции напрямую.

Контракт ответов сохранён 1:1 с inline definitions из web_app.py
(до Session 25 Phase 2 Wave A extraction).

Routes:
- GET /api/commands              — полный реестр (registry.to_api_response())
- GET /api/commands/usage        — usage statistics (отсортировано DESC)
- GET /api/commands/usage/top    — top-N usage с ranking
- GET /api/commands/{name}       — детальная инфо о команде (404 при unknown)
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["commands"])


@router.get("/api/commands")
async def list_commands() -> dict:
    """Полный список команд с метаданными из command_registry."""
    from ...core.command_registry import registry as _reg

    return _reg.to_api_response()


@router.get("/api/commands/usage")
async def get_command_usage() -> dict:
    """Статистика вызовов команд (отсортировано по убыванию)."""
    from ...core.command_registry import get_usage as _get_usage

    usage = _get_usage()
    return {
        "ok": True,
        "total_calls": sum(usage.values()),
        "unique_commands": len(usage),
        "usage": usage,
    }


@router.get("/api/commands/usage/top")
async def get_command_usage_top(limit: int = 10) -> dict:
    """Топ-N команд по количеству вызовов (count DESC, name ASC при ничьей)."""
    from ...core.command_registry import get_usage as _get_usage

    raw = _get_usage()
    clamped = max(1, min(limit, 100))
    sorted_items = sorted(raw.items(), key=lambda x: (-x[1], x[0]))
    top = [{"command": cmd, "count": cnt} for cmd, cnt in sorted_items[:clamped]]
    return {
        "ok": True,
        "top": top,
        "total_commands": len(raw),
    }


@router.get("/api/commands/{name}")
async def get_command(name: str) -> dict:
    """Детальная информация о конкретной команде."""
    from fastapi import HTTPException

    from ...core.command_registry import registry as _reg

    cmd = _reg.get(name)
    if cmd is None:
        raise HTTPException(
            status_code=404,
            detail=f"Команда '{name}' не найдена",
        )
    return {"ok": True, "command": cmd.to_dict()}
