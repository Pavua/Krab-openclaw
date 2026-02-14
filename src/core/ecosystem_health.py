# -*- coding: utf-8 -*-
"""
Ecosystem Health Service.

Назначение:
1) Давать единый health-срез по 3-проектной экосистеме:
   - Krab/OpenClaw (cloud brain),
   - локальный AI fallback (LM Studio/Ollama через router),
   - Krab Voice Gateway,
   - Krab Ear backend.
2) Вычислять уровень деградации цепочки `cloud -> local fallback`.
3) Использоваться из Web API и Telegram-команд без дублирования логики.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Any

import aiohttp


class EcosystemHealthService:
    """Агрегатор health-статуса сервисов экосистемы Krab."""

    def __init__(
        self,
        router: Any,
        openclaw_client: Any | None = None,
        voice_gateway_client: Any | None = None,
        krab_ear_client: Any | None = None,
        krab_ear_backend_url: str | None = None,
        timeout_sec: float = 2.5,
    ):
        self.router = router
        self.openclaw_client = openclaw_client
        self.voice_gateway_client = voice_gateway_client
        self.krab_ear_client = krab_ear_client
        self.krab_ear_backend_url = (
            (krab_ear_backend_url or os.getenv("KRAB_EAR_BACKEND_URL", "http://127.0.0.1:8765")).strip().rstrip("/")
        )
        self.timeout_sec = max(0.5, float(timeout_sec))

    async def collect(self) -> dict[str, Any]:
        """Возвращает unified health snapshot с деградацией и рисками."""
        openclaw_check = await self._check_client_health(self.openclaw_client, "openclaw")
        local_check = await self._check_local_health()
        voice_check = await self._check_client_health(self.voice_gateway_client, "voice_gateway")
        ear_check = await self._check_krab_ear_health()

        cloud_ok = bool(openclaw_check["ok"])
        local_ok = bool(local_check["ok"])

        if cloud_ok:
            degradation = "normal"
            ai_channel = "cloud"
        elif local_ok:
            degradation = "degraded_to_local_fallback"
            ai_channel = "local_fallback"
        else:
            degradation = "critical_no_ai_backend"
            ai_channel = "none"

        # Важный контекст: voice-поток рабочий только при готовности Gateway + Ear.
        voice_assist_ready = bool(voice_check["ok"]) and bool(ear_check["ok"])

        risk_level = "low"
        if degradation == "critical_no_ai_backend":
            risk_level = "high"
        elif degradation == "degraded_to_local_fallback" or not voice_assist_ready:
            risk_level = "medium"

        recommendations: list[str] = []
        if degradation == "degraded_to_local_fallback":
            recommendations.append("OpenClaw offline: временно вести non-critical задачи через локальные модели.")
        elif degradation == "critical_no_ai_backend":
            recommendations.append("Нет доступного AI backend: проверить OpenClaw и LM Studio/Ollama.")
        if not voice_check["ok"]:
            recommendations.append("Voice Gateway недоступен: команды `!call*` будут ограничены.")
        if not ear_check["ok"]:
            recommendations.append("Krab Ear backend недоступен: desktop call-assist поток неактивен.")
        if not recommendations:
            recommendations.append("Экосистема в норме: поддерживай текущий режим мониторинга.")

        return {
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "status": "ok" if degradation == "normal" and voice_assist_ready else ("critical" if risk_level == "high" else "degraded"),
            "risk_level": risk_level,
            "degradation": degradation,
            "checks": {
                "openclaw": openclaw_check,
                "local_lm": local_check,
                "voice_gateway": voice_check,
                "krab_ear": ear_check,
            },
            "chain": {
                "active_ai_channel": ai_channel,
                "fallback_ready": local_ok,
                "voice_assist_ready": voice_assist_ready,
            },
            "recommendations": recommendations[:6],
        }

    async def _check_local_health(self) -> dict[str, Any]:
        """Проверка локального AI канала через ModelRouter."""
        started = time.monotonic()
        try:
            ok = bool(await self.router.check_local_health())
            latency_ms = int((time.monotonic() - started) * 1000)
            return {
                "ok": ok,
                "status": "ok" if ok else "unavailable",
                "latency_ms": latency_ms,
                "source": "router.check_local_health",
            }
        except Exception as exc:
            latency_ms = int((time.monotonic() - started) * 1000)
            return {
                "ok": False,
                "status": f"error: {exc}",
                "latency_ms": latency_ms,
                "source": "router.check_local_health",
            }

    async def _check_client_health(self, client: Any | None, source_name: str) -> dict[str, Any]:
        """Проверка health внешнего клиента (OpenClaw/Voice Gateway)."""
        if not client or not hasattr(client, "health_check"):
            return {
                "ok": False,
                "status": "not_configured",
                "latency_ms": 0,
                "source": source_name,
            }

        started = time.monotonic()
        try:
            result = await client.health_check()
            latency_ms = int((time.monotonic() - started) * 1000)
            ok = bool(result)
            return {
                "ok": ok,
                "status": "ok" if ok else "unavailable",
                "latency_ms": latency_ms,
                "source": source_name,
            }
        except Exception as exc:
            latency_ms = int((time.monotonic() - started) * 1000)
            return {
                "ok": False,
                "status": f"error: {exc}",
                "latency_ms": latency_ms,
                "source": source_name,
            }

    async def _check_krab_ear_health(self) -> dict[str, Any]:
        """
        Проверка Krab Ear:
        - если передан клиент с health_check -> используем его;
        - иначе проверяем HTTP backend `/health`.
        """
        if self.krab_ear_client and hasattr(self.krab_ear_client, "health_check"):
            return await self._check_client_health(self.krab_ear_client, "krab_ear_client")

        url = f"{self.krab_ear_backend_url}/health"
        timeout = aiohttp.ClientTimeout(total=self.timeout_sec)
        started = time.monotonic()
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url) as response:
                    status = int(response.status)
            latency_ms = int((time.monotonic() - started) * 1000)
            ok = status == 200
            return {
                "ok": ok,
                "status": "ok" if ok else f"http_{status}",
                "latency_ms": latency_ms,
                "source": url,
            }
        except Exception as exc:
            latency_ms = int((time.monotonic() - started) * 1000)
            return {
                "ok": False,
                "status": f"error: {exc}",
                "latency_ms": latency_ms,
                "source": url,
            }
