# -*- coding: utf-8 -*-
"""
Клиент Krab Voice Gateway.

Назначение:
1) Проверять живость voice-gateway для dashboard/health API.
2) Давать единый async-контракт `health_check()` для EcosystemHealthService.
3) Изолировать HTTP-детали (URL, токен, таймаут) от runtime-слоя.
"""

from __future__ import annotations

import os
import time
from typing import Any

import httpx

from ..core.logger import get_logger

logger = get_logger(__name__)


class VoiceGatewayClient:
    """Минимальный клиент для диагностики Krab Voice Gateway."""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout_sec: float | None = None,
    ) -> None:
        self.base_url = (
            (base_url or os.getenv("VOICE_GATEWAY_URL", "http://127.0.0.1:8090"))
            .strip()
            .rstrip("/")
        )
        # Поддерживаем оба варианта имени ключа, чтобы не ломать старые .env.
        self.api_key = str(
            api_key
            or os.getenv("KRAB_VOICE_API_KEY", "")
            or os.getenv("VOICE_GATEWAY_API_KEY", "")
        ).strip()
        self.timeout_sec = max(0.5, float(timeout_sec or os.getenv("VOICE_GATEWAY_TIMEOUT_SEC", "2.5")))

    @staticmethod
    def _is_ok_payload(payload: dict[str, Any]) -> bool:
        """Единое правило определения здоровья из JSON-ответа /health."""
        if bool(payload.get("ok")):
            return True
        status = str(payload.get("status", "")).strip().lower()
        return status in {"ok", "healthy", "up"}

    async def _fetch_health_payload(self) -> tuple[int, dict[str, Any]]:
        """
        Возвращает `(http_status, json_payload)`.

        Почему отдельный метод:
        - удобно мокать в unit-тестах;
        - меньше дублирования между `health_check` и `health_report`.
        """
        headers: dict[str, str] = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        url = f"{self.base_url}/health"
        async with httpx.AsyncClient(timeout=self.timeout_sec, headers=headers) as client:
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
        """True, если gateway доступен и /health сообщает о нормальном статусе."""
        try:
            status_code, payload = await self._fetch_health_payload()
            return status_code == 200 and self._is_ok_payload(payload)
        except Exception as exc:  # noqa: BLE001 - health должен быть fail-safe
            logger.debug("voice_gateway_health_failed", error=str(exc), base_url=self.base_url)
            return False

    async def health_report(self) -> dict[str, Any]:
        """Подробный отчет (для будущих diagnostics endpoint-ов)."""
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
