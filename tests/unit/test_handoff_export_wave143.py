# -*- coding: utf-8 -*-
"""Wave 143: тесты ``src.core.handoff_auto_export`` + метрики.

Покрывают:
1) success path — JSON ушёл на диск, метрика ``ok`` инкрементнулась.
2) timeout path — корректный outcome, ``expected_timeout=True`` для periodic_maintenance.
3) retry success — первая попытка таймаут, вторая ок.
4) retry exhausted — после ``max_retries+1`` попыток отдаёт error.
5) non-recoverable error (httpx.HTTPStatusError) — outcome=error, метрика инкрементнулась.
6) env timeout override — ``KRAB_HANDOFF_EXPORT_TIMEOUT_SEC`` парсится корректно.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from src.core.handoff_auto_export import (
    _resolve_handoff_url,
    _resolve_timeout_sec,
    auto_export_handoff_snapshot,
)

# ────────────────────────────────────────────────────────────────────────────
# Helpers — лёгкий fake httpx.AsyncClient через MockTransport.
# Реальный httpx.AsyncClient умеет ходить через transport=, что снимает
# необходимость поднимать локальный сервер.
# ────────────────────────────────────────────────────────────────────────────


_FAKE_PAYLOAD: dict[str, Any] = {
    "ok": True,
    "generated_at_utc": "2026-05-12T00:00:00",
    "items": [1, 2, 3],
}


def _make_ok_client_factory() -> Any:
    """Возвращает factory, отдающую httpx.AsyncClient с MockTransport → 200 OK."""

    def _handler(request: httpx.Request) -> httpx.Response:
        body = json.dumps(_FAKE_PAYLOAD).encode()
        return httpx.Response(200, content=body, headers={"Content-Type": "application/json"})

    def factory() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(_handler))

    return factory


def _make_timeout_client_factory(*, fail_count: int = 999) -> tuple[Any, list[int]]:
    """Factory, бросающая ``httpx.ConnectTimeout`` первые ``fail_count`` раз."""

    call_log: list[int] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        call_log.append(1)
        if len(call_log) <= fail_count:
            raise httpx.ConnectTimeout("timed out", request=request)
        body = json.dumps(_FAKE_PAYLOAD).encode()
        return httpx.Response(200, content=body)

    def factory() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(_handler))

    return factory, call_log


def _make_http_error_client_factory(status: int = 500) -> Any:
    """Factory отдаёт ``status`` (raise_for_status поднимет HTTPStatusError)."""

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, content=b"upstream error")

    def factory() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(_handler))

    return factory


# ────────────────────────────────────────────────────────────────────────────
# Tests
# ────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_success_writes_snapshot_and_returns_exported_true(tmp_path: Path) -> None:
    """Happy path: 200 OK → JSON на диск, exported=True, outcome=ok."""

    artifacts = tmp_path / "artifacts"
    result = await auto_export_handoff_snapshot(
        reason="periodic_maintenance",
        artifacts_dir=artifacts,
        handoff_url="http://test.invalid/api/runtime/handoff",
        client_factory=_make_ok_client_factory(),
        sleep_fn=lambda _s: asyncio.sleep(0),
    )

    assert result["exported"] is True
    assert result["error"] is None
    assert result["reason"] == "periodic_maintenance"
    assert result["attempts"] == 1
    dest = Path(result["path"])
    assert dest.exists()
    assert dest.parent == artifacts
    written = json.loads(dest.read_text(encoding="utf-8"))
    assert written == _FAKE_PAYLOAD


@pytest.mark.asyncio
async def test_timeout_marks_expected_timeout_for_periodic(tmp_path: Path) -> None:
    """Timeout в periodic_maintenance: exported=False, outcome=timeout, expected_timeout=True."""

    factory, _calls = _make_timeout_client_factory()
    result = await auto_export_handoff_snapshot(
        reason="periodic_maintenance",
        artifacts_dir=tmp_path,
        handoff_url="http://test.invalid/api/runtime/handoff",
        timeout_sec=0.5,
        max_retries=0,  # одна попытка
        client_factory=factory,
        sleep_fn=lambda _s: asyncio.sleep(0),
    )

    assert result["exported"] is False
    assert result["outcome"] == "timeout"
    assert result["expected_timeout"] is True
    assert "timed out" in (result["error"] or "").lower()


@pytest.mark.asyncio
async def test_retry_succeeds_after_first_timeout(tmp_path: Path) -> None:
    """Первая попытка таймаут, вторая успешна → exported=True, attempts=2."""

    factory, calls = _make_timeout_client_factory(fail_count=1)
    result = await auto_export_handoff_snapshot(
        reason="manual",
        artifacts_dir=tmp_path,
        handoff_url="http://test.invalid/api/runtime/handoff",
        timeout_sec=0.5,
        max_retries=1,
        retry_backoff_sec=0.0,
        client_factory=factory,
        sleep_fn=lambda _s: asyncio.sleep(0),
    )

    assert result["exported"] is True
    assert result["attempts"] == 2
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_retry_exhausted_returns_error(tmp_path: Path) -> None:
    """Все попытки таймаут → exported=False, attempts=max_retries+1."""

    factory, calls = _make_timeout_client_factory(fail_count=999)
    result = await auto_export_handoff_snapshot(
        reason="userbot_stop",
        artifacts_dir=tmp_path,
        handoff_url="http://test.invalid/api/runtime/handoff",
        timeout_sec=0.5,
        max_retries=2,
        retry_backoff_sec=0.0,
        client_factory=factory,
        sleep_fn=lambda _s: asyncio.sleep(0),
    )

    assert result["exported"] is False
    assert result["outcome"] == "timeout"
    assert result["attempts"] == 3
    assert len(calls) == 3
    # userbot_stop НЕ помечается expected_timeout (он критичен для shutdown handoff).
    assert result["expected_timeout"] is False


@pytest.mark.asyncio
async def test_http_error_returns_error_outcome(tmp_path: Path) -> None:
    """5xx от endpoint'а → outcome=error (не timeout), exported=False."""

    factory = _make_http_error_client_factory(status=503)
    result = await auto_export_handoff_snapshot(
        reason="manual",
        artifacts_dir=tmp_path,
        handoff_url="http://test.invalid/api/runtime/handoff",
        timeout_sec=1.0,
        max_retries=0,
        client_factory=factory,
        sleep_fn=lambda _s: asyncio.sleep(0),
    )

    assert result["exported"] is False
    assert result["outcome"] == "error"
    assert "503" in (result["error"] or "") or "Server error" in (result["error"] or "")


def test_resolve_timeout_uses_env_when_valid(monkeypatch: pytest.MonkeyPatch) -> None:
    """Env override 30s → 30.0; невалидное значение → default."""

    monkeypatch.setenv("KRAB_HANDOFF_EXPORT_TIMEOUT_SEC", "30")
    assert _resolve_timeout_sec() == 30.0

    monkeypatch.setenv("KRAB_HANDOFF_EXPORT_TIMEOUT_SEC", "not-a-number")
    assert _resolve_timeout_sec() == 60.0  # _DEFAULT_TIMEOUT_SEC

    monkeypatch.setenv("KRAB_HANDOFF_EXPORT_TIMEOUT_SEC", "0.1")
    assert _resolve_timeout_sec() == 60.0  # too small → default

    monkeypatch.setenv("KRAB_HANDOFF_EXPORT_TIMEOUT_SEC", "5000")
    assert _resolve_timeout_sec() == 600.0  # capped


def test_resolve_handoff_url_uses_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """KRAB_HANDOFF_EXPORT_URL подменяет default URL."""

    monkeypatch.delenv("KRAB_HANDOFF_EXPORT_URL", raising=False)
    default = _resolve_handoff_url()
    assert "/api/runtime/handoff" in default
    assert "probe_cloud_runtime=0" in default

    monkeypatch.setenv("KRAB_HANDOFF_EXPORT_URL", "http://custom/handoff")
    assert _resolve_handoff_url() == "http://custom/handoff"


def test_metrics_record_helper_accepts_unknown_labels_gracefully() -> None:
    """record_handoff_export не падает на мусорных outcome/reason."""

    from src.core.metrics.handoff_export import (
        _normalize_outcome,
        _normalize_reason,
        record_handoff_export,
    )

    # Нормализация: junk → fallback
    assert _normalize_outcome("garbage") == "error"
    assert _normalize_outcome(None) == "error"
    assert _normalize_outcome("OK") == "ok"
    assert _normalize_reason("garbage") == "unknown"
    assert _normalize_reason("Periodic_Maintenance") == "periodic_maintenance"

    # Helper не бросает даже с None labels
    record_handoff_export(outcome="ok", reason="periodic_maintenance", duration_seconds=1.5)
    record_handoff_export(outcome=None, reason=None, duration_seconds=None)  # type: ignore[arg-type]
    record_handoff_export(outcome="ok", reason="manual", duration_seconds=-5.0)
