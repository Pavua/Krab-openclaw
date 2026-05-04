# -*- coding: utf-8 -*-
"""
Gap 2 (Wave 17-A): /api/runtime/recover — HTTP path e2e тест для exit_code=78.

Wave 16-O HIGH-2: endpoint возвращает HTTP 503 + recovery_loop_detected=true
когда repair script возвращает exit_code=78 (recovery loop idempotency guard).

Тесты используют FastAPI TestClient (httpx-based) чтобы покрыть полный HTTP path
включая status_code и тело ответа — в отличие от test_runtime_recover_exit_78.py
который вызывает endpoint напрямую минуя HTTP слой.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.modules.web_routers._context import RouterContext

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_ctx(run_script_result: dict[str, Any] | None = None) -> RouterContext:
    """
    Создаём RouterContext-заглушку.
    run_script_result — что вернёт run_project_python_script_helper при вызове.
    """
    result = run_script_result or {"ok": True, "exit_code": 0, "error": "", "stdout_tail": ""}

    def _fake_run_script(*args: Any, **kwargs: Any) -> dict[str, Any]:
        """Заглушка для run_project_python_script_helper."""
        return result

    # collect_runtime_lite вызывается async в endpoint — нужен AsyncMock
    collect_runtime_lite_mock = AsyncMock(return_value={"ok": True, "runtime": "stub"})

    ctx = RouterContext(
        deps={
            "run_project_python_script_helper": _fake_run_script,
            "openclaw": None,
            "collect_runtime_lite": collect_runtime_lite_mock,
        },
        project_root=Path("/tmp"),
        web_api_key_fn=lambda: "",  # пустой ключ — auth bypass в тестах
        assert_write_access_fn=lambda h, t: None,
    )
    # Патчим collect_runtime_lite напрямую на ctx если метод ищется там
    ctx.collect_runtime_lite = collect_runtime_lite_mock  # type: ignore[attr-defined]
    return ctx


def _client(run_script_result: dict[str, Any] | None = None) -> TestClient:
    """Создаём TestClient с system_router для заданного run_script_result."""
    from src.modules.web_routers.system_router import build_system_router

    ctx = _build_ctx(run_script_result)
    app = FastAPI()
    app.include_router(build_system_router(ctx))
    # TestClient(raise_server_exceptions=False) — ловим HTTP ответы как есть
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Gap 2 — HTTP path тесты через TestClient
# ---------------------------------------------------------------------------


def test_http_recover_exit_78_returns_503() -> None:
    """
    Gap 2 (Wave 17-A): POST /api/runtime/recover через HTTP.
    exit_code=78 от repair script → HTTP 503 статус в ответе.
    Проверяем что HTTP слой (FastAPI + Response serialization) работает корректно.
    """
    client = _client(
        run_script_result={
            "ok": False,
            "exit_code": 78,
            "error": "recovery loop: recent backup < 1h, exit 78",
            "stdout_tail": "idempotency guard triggered",
        }
    )

    resp = client.post("/api/runtime/recover", json={})

    # HTTP статус должен быть 503
    assert resp.status_code == 503, (
        f"Ожидали HTTP 503 при exit_code=78, получили {resp.status_code}. Body: {resp.text[:200]}"
    )

    # Тело должно быть валидным JSON
    body = resp.json()

    # Обязательные поля из HIGH-2 fix
    assert body.get("recovery_loop_detected") is True, (
        f"recovery_loop_detected должен быть True в HTTP ответе при exit_code=78. Body: {body}"
    )
    assert body.get("requires_manual_intervention") is True, (
        f"requires_manual_intervention должен быть True в HTTP ответе при exit_code=78. "
        f"Body: {body}"
    )


def test_http_recover_exit_78_steps_present() -> None:
    """
    Gap 2: HTTP 503 ответ содержит steps с exit_code=78 для диагностики.
    steps[*].exit_code должен отражать код выхода скрипта.
    """
    client = _client(
        run_script_result={
            "ok": False,
            "exit_code": 78,
            "error": "idempotency guard",
            "stdout_tail": "",
        }
    )

    resp = client.post("/api/runtime/recover", json={})
    assert resp.status_code == 503

    body = resp.json()
    steps = body.get("steps", [])
    # Хотя бы один step должен содержать exit_code=78
    exit_codes = [s.get("exit_code") for s in steps if not s.get("skipped")]
    assert 78 in exit_codes, (
        f"steps должны содержать exit_code=78, получили exit_codes={exit_codes}. steps={steps}"
    )


def test_http_recover_success_returns_200() -> None:
    """
    Gap 2: Успешный repair (exit_code=0) → HTTP 200 (не 503).
    Убеждаемся что 503 специфичен только для exit_code=78.
    """
    client = _client(
        run_script_result={
            "ok": True,
            "exit_code": 0,
            "error": "",
            "stdout_tail": "repair completed successfully",
        }
    )

    resp = client.post("/api/runtime/recover", json={})

    # Успех: HTTP 200
    assert resp.status_code == 200, (
        f"Ожидали HTTP 200 при успехе, получили {resp.status_code}. Body: {resp.text[:200]}"
    )
    body = resp.json()
    # recovery_loop_detected должен отсутствовать или быть False
    assert not body.get("recovery_loop_detected"), (
        "recovery_loop_detected не должен быть True при успешном repair"
    )


def test_http_recover_exit_1_returns_200_not_503() -> None:
    """
    Gap 2: exit_code=1 (обычная ошибка, не recovery loop) → HTTP 200, не 503.
    HTTP 503 зарезервирован строго для exit_code=78.
    """
    client = _client(
        run_script_result={
            "ok": False,
            "exit_code": 1,
            "error": "generic repair error",
            "stdout_tail": "",
        }
    )

    resp = client.post("/api/runtime/recover", json={})

    # exit_code=1 не recovery loop → не 503
    assert resp.status_code != 503, (
        f"exit_code=1 не должен возвращать HTTP 503 (только exit_code=78). "
        f"Status: {resp.status_code}"
    )
    body = resp.json()
    assert not body.get("recovery_loop_detected"), (
        "recovery_loop_detected не должен быть True при exit_code=1"
    )


def test_http_recover_response_content_type_json() -> None:
    """
    Gap 2: HTTP 503 ответ имеет Content-Type: application/json.
    Важно для мониторинга который парсит JSON из 503 ответов.
    """
    client = _client(
        run_script_result={
            "ok": False,
            "exit_code": 78,
            "error": "recovery loop detected",
            "stdout_tail": "",
        }
    )

    resp = client.post("/api/runtime/recover", json={})
    assert resp.status_code == 503

    content_type = resp.headers.get("content-type", "")
    assert "application/json" in content_type, (
        f"HTTP 503 должен возвращать application/json, получили: {content_type!r}"
    )
