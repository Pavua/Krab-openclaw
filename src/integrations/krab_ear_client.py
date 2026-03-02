# -*- coding: utf-8 -*-
"""
Клиент Krab Ear backend.

Назначение:
1) Проверять живость локального backend (`/health`) для единого ecosystem health.
2) Изолировать детали подключения к Krab Ear (URL/таймаут) от web/runtime слоя.
3) Предоставлять такой же контракт `health_check()` как у остальных клиентов.
"""

from __future__ import annotations

import os
import time
from typing import Any

import httpx

from ..core.logger import get_logger

logger = get_logger(__name__)


class KrabEarClient:
    """Минимальный клиент диагностики Krab Ear backend."""

    def __init__(self, base_url: str | None = None, timeout_sec: float | None = None) -> None:
        self.base_url = (
            (base_url or os.getenv("KRAB_EAR_BACKEND_URL", "http://127.0.0.1:5005"))
            .strip()
            .rstrip("/")
        )
        self.timeout_sec = max(0.5, float(timeout_sec or os.getenv("KRAB_EAR_TIMEOUT_SEC", "2.5")))

    @staticmethod
    def _is_ok_payload(payload: dict[str, Any]) -> bool:
        """Krab Ear может возвращать `status=ok` и/или `ok=true`."""
        if bool(payload.get("ok")):
            return True
        return str(payload.get("status", "")).strip().lower() in {"ok", "healthy", "up"}

    async def _fetch_health_payload(self) -> tuple[int, dict[str, Any]]:
        """Возвращает `(http_status, payload)` для `/health`."""
        url = f"{self.base_url}/health"
        async with httpx.AsyncClient(timeout=self.timeout_sec) as client:
            response = await client.get(url)
            payload: dict[str, Any] = {}
            content_type = str(response.headers.get("content-type", "")).lower()
            if "application/json" in content_type:
                try:
                    payload = response.json() if response.content else {}
                except ValueError:
                    payload = {"raw": response.text}
            elif response.text:
                payload = {"raw": response.text}
            return response.status_code, payload

    async def health_check(self) -> bool:
        """True, если backend отвечает и отдает корректный health-статус."""
        try:
            status_code, payload = await self._fetch_health_payload()
            return status_code == 200 and self._is_ok_payload(payload)
        except Exception as exc:  # noqa: BLE001 - health не должен ронять runtime
            logger.warning("krab_ear_health_failed", error=str(exc), base_url=self.base_url)
            return False

    async def health_report(self) -> dict[str, Any]:
        """Подробный отчет с latency и detail payload."""
        started = time.monotonic()
        try:
            status_code, payload = await self._fetch_health_payload()
            ok = status_code == 200 and self._is_ok_payload(payload)
            return {
                "ok": ok,
                "status": "ok" if ok else f"http_{status_code}",
                "latency_ms": int((time.monotonic() - started) * 1000),
                "source": f"{self.base_url}/health",
                "detail": payload,
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "ok": False,
                "status": "error",
                "latency_ms": int((time.monotonic() - started) * 1000),
                "source": f"{self.base_url}/health",
                "detail": str(exc),
            }

