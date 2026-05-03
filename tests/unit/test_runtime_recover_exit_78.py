# -*- coding: utf-8 -*-
"""
HIGH-2 (Wave 16-O): /api/runtime/recover → exit_code=78 должен возвращать HTTP 503.

exit_code=78 означает "recovery loop detected — нужно manual intervention".
Молчаливый ok=false был бы слишком незаметным для мониторинга.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_router_context(run_script_result: dict[str, Any]) -> MagicMock:
    """Создаём RouterContext-заглушку с нужным run_project_python_script_helper."""
    ctx = MagicMock()
    ctx.project_root = MagicMock()
    ctx.project_root.__truediv__ = lambda self, other: MagicMock()

    # run_script возвращает заданный result
    ctx.get_dep = lambda key: (
        (lambda *args, **kwargs: run_script_result)
        if key == "run_project_python_script_helper"
        else None
    )
    ctx.assert_write_access = MagicMock()
    ctx.collect_runtime_lite = AsyncMock(return_value={"ok": True})
    return ctx


async def _call_recover_endpoint(ctx: MagicMock, data: dict | None = None) -> Any:
    """Вызываем runtime_recover через build_system_router factory."""
    from src.modules.web_routers.system_router import build_system_router

    router = build_system_router(ctx)
    # Достаём endpoint из роутера напрямую
    recover_fn = None
    for route in router.routes:
        if hasattr(route, "path") and route.path == "/api/runtime/recover":
            recover_fn = route.endpoint
            break
    assert recover_fn is not None, "/api/runtime/recover endpoint не найден в роутере"
    result = await recover_fn(payload=data or {}, x_krab_web_key="", token="")
    return result


# ---------------------------------------------------------------------------
# 1. exit_code=78 → HTTP 503 + поля recovery_loop_detected / requires_manual_intervention
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_runtime_recover_exit_78_returns_503() -> None:
    """Если repair script выходит с кодом 78 — ответ должен быть HTTP 503."""
    ctx = _make_router_context(
        run_script_result={
            "ok": False,
            "exit_code": 78,
            "error": "recovery loop: recent backup < 1h, exit 78",
            "stdout_tail": "idempotency guard triggered",
        }
    )

    result = await _call_recover_endpoint(ctx)

    # Должен вернуть fastapi.responses.Response (не dict) со статусом 503
    from fastapi.responses import Response

    assert isinstance(result, Response), f"Ожидали Response (503), получили {type(result).__name__}"
    assert result.status_code == 503, f"Ожидали HTTP 503, получили {result.status_code}"

    body = json.loads(result.body)
    assert body.get("recovery_loop_detected") is True, (
        "recovery_loop_detected должен быть True при exit_code=78"
    )
    assert body.get("requires_manual_intervention") is True, (
        "requires_manual_intervention должен быть True при exit_code=78"
    )


# ---------------------------------------------------------------------------
# 2. exit_code=0 (успех) → обычный dict с ok=True
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_runtime_recover_success_returns_dict() -> None:
    """При успехе ответ — обычный dict (не Response), HTTP 200."""
    ctx = _make_router_context(
        run_script_result={
            "ok": True,
            "exit_code": 0,
            "error": "",
            "stdout_tail": "repair complete",
        }
    )

    result = await _call_recover_endpoint(ctx)

    assert isinstance(result, dict), f"Ожидали dict при успехе, получили {type(result).__name__}"
    assert result.get("ok") is True


# ---------------------------------------------------------------------------
# 3. exit_code=1 (ошибка, не recovery loop) → dict с ok=False (не 503)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_runtime_recover_exit_1_returns_dict_not_503() -> None:
    """exit_code=1 — обычная ошибка, не recovery loop → dict без 503."""
    ctx = _make_router_context(
        run_script_result={
            "ok": False,
            "exit_code": 1,
            "error": "generic error",
            "stdout_tail": "",
        }
    )

    result = await _call_recover_endpoint(ctx)

    # Обычный dict — не Response с 503
    from fastapi.responses import Response

    if isinstance(result, Response):
        assert result.status_code != 503, (
            "exit_code=1 НЕ должен возвращать 503 — это только для exit_code=78"
        )
    else:
        assert isinstance(result, dict)
        # recovery_loop_detected не должен быть True для обычных ошибок
        assert not result.get("recovery_loop_detected"), (
            "recovery_loop_detected не должен быть True при exit_code=1"
        )


# ---------------------------------------------------------------------------
# 4. skipped step (run_openclaw_runtime_repair=False) → никогда не 503
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_runtime_recover_skip_repair_never_503() -> None:
    """Если repair пропущен — нет exit_code=78 → не 503."""
    ctx = _make_router_context(run_script_result={"ok": True, "exit_code": 0})

    # Передаём run_openclaw_runtime_repair=False — repair шаг будет skipped
    result = await _call_recover_endpoint(ctx, data={"run_openclaw_runtime_repair": False})

    from fastapi.responses import Response

    if isinstance(result, Response):
        assert result.status_code != 503
    else:
        assert isinstance(result, dict)
        assert not result.get("recovery_loop_detected")
