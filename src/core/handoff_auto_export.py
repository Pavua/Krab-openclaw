# -*- coding: utf-8 -*-
"""Wave 143: auto-export runtime handoff snapshot — async, с retry и метриками.

Заменяет старую blocking-реализацию из ``userbot_bridge._auto_export_handoff_snapshot``,
которая использовала ``urllib.request.urlopen`` (sync) внутри async-кода. Из-за этого
event loop блокировался, и periodic 30s-таймаут регулярно срабатывал при минимальной
нагрузке (``auto_handoff_export_failed error='timed out' reason=periodic_maintenance``).

Что починено:
- ``httpx.AsyncClient`` вместо ``urllib`` → не блокирует loop.
- Конфигурируемый timeout (``KRAB_HANDOFF_EXPORT_TIMEOUT_SEC``, default 60s)
  с разделением connect/read.
- Один retry с коротким backoff на recoverable-ошибках.
- ``expected_timeout=True`` маркер в warning для не-критичного periodic-канала —
  чтобы Sentry filter мог отделять «нормальные» таймауты от багов.
- Prometheus метрики через ``src.core.metrics.handoff_export``.

Модуль импортируется и из bridge (rolling periodic export), и из тестов
(unit-тесты Wave 143).
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import structlog

from .metrics.handoff_export import record_handoff_export

logger = structlog.get_logger(__name__)


# ── Дефолтные параметры ─────────────────────────────────────────────────────
# Periodic-channel тяжелее, чем кажется: handoff endpoint собирает health-summary
# 3 клиентов (3.5s каждый) + readiness snapshots + git/workspace probes.
# 60s даёт запас даже когда event loop под нагрузкой.
_DEFAULT_TIMEOUT_SEC = 60.0
_DEFAULT_CONNECT_TIMEOUT_SEC = 5.0
_DEFAULT_RETRY_BACKOFF_SEC = 2.0
_DEFAULT_HANDOFF_URL = "http://127.0.0.1:8080/api/runtime/handoff?probe_cloud_runtime=0"

# reason'ы, которые считаем «не критичными» — periodic-таймауты ожидаемы при
# нагрузке и не должны попадать в Sentry с error severity.
_EXPECTED_TIMEOUT_REASONS = {"periodic_maintenance"}


def _resolve_timeout_sec() -> float:
    """Читает ``KRAB_HANDOFF_EXPORT_TIMEOUT_SEC`` или возвращает default."""
    raw = (os.getenv("KRAB_HANDOFF_EXPORT_TIMEOUT_SEC", "") or "").strip()
    if not raw:
        return _DEFAULT_TIMEOUT_SEC
    try:
        value = float(raw)
    except ValueError:
        return _DEFAULT_TIMEOUT_SEC
    # Защита от мусора: < 1s бесполезен, > 600s можно подвесить maintenance loop.
    if value < 1.0:
        return _DEFAULT_TIMEOUT_SEC
    if value > 600.0:
        return 600.0
    return value


def _resolve_handoff_url() -> str:
    """Позволяет тесту/конфигу подменить целевой URL.

    По умолчанию locale loopback :8080 (см. ``WEB_PORT`` в bootstrap/runtime).
    """
    raw = (os.getenv("KRAB_HANDOFF_EXPORT_URL", "") or "").strip()
    return raw or _DEFAULT_HANDOFF_URL


async def _fetch_handoff_payload(
    url: str,
    *,
    timeout_sec: float,
    connect_timeout_sec: float,
    client_factory: Any = None,
) -> bytes:
    """Один сетевой запрос. ``client_factory`` опционален — для тестов с моками.

    Тест может передать factory типа ``lambda: FakeAsyncClient(...)``; в production
    создаём свежий ``httpx.AsyncClient`` per-call (handoff редко зовётся).
    """
    if client_factory is None:
        timeout = httpx.Timeout(timeout_sec, connect=connect_timeout_sec)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(url)
            response.raise_for_status()
            return response.content

    # Test-mode: factory возвращает уже сконфигурированный async-клиент.
    client = client_factory()
    async with client as c:  # type: ignore[union-attr]
        response = await c.get(url)
        response.raise_for_status()
        return response.content


async def auto_export_handoff_snapshot(
    *,
    reason: str,
    artifacts_dir: Path,
    handoff_url: str | None = None,
    timeout_sec: float | None = None,
    connect_timeout_sec: float | None = None,
    max_retries: int = 1,
    retry_backoff_sec: float | None = None,
    client_factory: Any = None,
    sleep_fn: Any = None,
) -> dict[str, Any]:
    """Скачать runtime handoff и сохранить на диск.

    Никогда не бросает: при любой ошибке возвращает структуру с ``exported=False``
    и ``error=...``. Metrics всегда записываются.

    Args:
        reason: маркер для логов/метрик (``periodic_maintenance`` / ``userbot_stop`` / ``manual``).
        artifacts_dir: куда сохранять JSON (создастся при необходимости).
        handoff_url: override URL endpoint'а (None → env/default).
        timeout_sec: общий timeout одной попытки (None → env/default 60s).
        connect_timeout_sec: timeout установки соединения (None → 5s).
        max_retries: дополнительные попытки сверх первой (0 = только одна попытка).
        retry_backoff_sec: пауза между retry (None → 2s).
        client_factory: тестовая factory async-клиента (вместо httpx.AsyncClient).
        sleep_fn: тестовая замена ``asyncio.sleep`` для контроля backoff.
    """
    started = time.monotonic()
    timeout = float(timeout_sec) if timeout_sec is not None else _resolve_timeout_sec()
    connect_to = (
        float(connect_timeout_sec)
        if connect_timeout_sec is not None
        else _DEFAULT_CONNECT_TIMEOUT_SEC
    )
    backoff = (
        float(retry_backoff_sec) if retry_backoff_sec is not None else _DEFAULT_RETRY_BACKOFF_SEC
    )
    url = handoff_url or _resolve_handoff_url()
    sleep = sleep_fn or asyncio.sleep

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = artifacts_dir / f"auto_handoff_{timestamp}.json"

    last_error: Exception | None = None
    outcome = "error"
    attempts = max(1, int(max_retries) + 1)

    for attempt in range(1, attempts + 1):
        try:
            raw = await _fetch_handoff_payload(
                url,
                timeout_sec=timeout,
                connect_timeout_sec=connect_to,
                client_factory=client_factory,
            )
            # Валидируем JSON прежде, чем пишем на диск — иначе можем сохранить мусор.
            data = json.loads(raw)
            artifacts_dir.mkdir(parents=True, exist_ok=True)
            payload = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
            dest.write_text(payload, encoding="utf-8")
            duration = time.monotonic() - started
            logger.info(
                "auto_handoff_export_success",
                reason=reason,
                path=str(dest),
                size_bytes=len(raw),
                duration_sec=round(duration, 3),
                attempt=attempt,
            )
            record_handoff_export(
                outcome="ok",
                reason=reason,
                duration_seconds=duration,
            )
            return {
                "exported": True,
                "path": str(dest),
                "error": None,
                "reason": reason,
                "attempts": attempt,
                "duration_sec": duration,
            }
        except (httpx.TimeoutException, asyncio.TimeoutError) as exc:
            last_error = exc
            outcome = "timeout"
            if attempt < attempts:
                await sleep(backoff)
                continue
            break
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            outcome = "error"
            if attempt < attempts:
                await sleep(backoff)
                continue
            break

    duration = time.monotonic() - started
    err_msg = str(last_error) if last_error is not None else "unknown_error"
    err_type = type(last_error).__name__ if last_error is not None else "UnknownError"
    expected_timeout = outcome == "timeout" and reason in _EXPECTED_TIMEOUT_REASONS

    logger.warning(
        "auto_handoff_export_failed",
        reason=reason,
        error=err_msg,
        error_type=err_type,
        outcome=outcome,
        attempts=attempts,
        duration_sec=round(duration, 3),
        timeout_sec=timeout,
        non_fatal=True,
        expected_timeout=expected_timeout,
    )
    record_handoff_export(
        outcome=outcome,
        reason=reason,
        duration_seconds=duration,
    )
    return {
        "exported": False,
        "path": str(dest),
        "error": err_msg,
        "reason": reason,
        "attempts": attempts,
        "duration_sec": duration,
        "outcome": outcome,
        "expected_timeout": expected_timeout,
    }


__all__ = [
    "auto_export_handoff_snapshot",
]
