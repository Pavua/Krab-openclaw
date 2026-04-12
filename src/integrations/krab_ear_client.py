# -*- coding: utf-8 -*-
"""
Клиент Krab Ear backend.

Назначение:
1) Проверять живость локального backend (`/health`) для единого ecosystem health.
2) Изолировать детали подключения к Krab Ear (URL/таймаут) от web/runtime слоя.
3) Предоставлять такой же контракт `health_check()` как у остальных клиентов.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

import httpx

from ..core.logger import get_logger

logger = get_logger(__name__)


class KrabEarClient:
    """Минимальный клиент диагностики Krab Ear backend."""

    def __init__(
        self,
        base_url: str | None = None,
        socket_path: str | None = None,
        timeout_sec: float | None = None,
    ) -> None:
        self.base_url = (
            (base_url if base_url is not None else os.getenv("KRAB_EAR_BACKEND_URL", ""))
            .strip()
            .rstrip("/")
        )
        # Krab Ear по умолчанию работает как IPC backend (unix socket), а не HTTP REST.
        self.socket_path = Path(
            (
                socket_path
                or os.getenv(
                    "KRAB_EAR_SOCKET_PATH", "~/Library/Application Support/KrabEar/krabear.sock"
                )
            )
        ).expanduser()
        self.timeout_sec = max(0.5, float(timeout_sec or os.getenv("KRAB_EAR_TIMEOUT_SEC", "2.5")))

    @staticmethod
    def _is_ok_payload(payload: dict[str, Any]) -> bool:
        """Krab Ear может возвращать `status=ok` и/или `ok=true`."""
        if bool(payload.get("ok")):
            return True
        return str(payload.get("status", "")).strip().lower() in {"ok", "healthy", "up"}

    async def _ping_ipc_health(self) -> tuple[bool, str]:
        """
        Пинг IPC backend через unix socket.

        Контракт взят из KrabEar backend/service.py:
        - метод: `ping`
        - формат: JSON line (`\\n`-terminated).
        """
        if not self.socket_path.exists():
            return False, "socket_missing"

        reader: asyncio.StreamReader | None = None
        writer: asyncio.StreamWriter | None = None
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_unix_connection(path=str(self.socket_path)),
                timeout=self.timeout_sec,
            )
            request = {"id": "health", "method": "ping", "params": {}}
            writer.write((json.dumps(request, ensure_ascii=False) + "\n").encode("utf-8"))
            await writer.drain()

            raw = await asyncio.wait_for(reader.readline(), timeout=self.timeout_sec)
            if not raw:
                return False, "empty_ipc_response"

            payload = json.loads(raw.decode("utf-8", errors="replace"))
            ok = bool(payload.get("ok"))
            result = payload.get("result", {}) if isinstance(payload.get("result"), dict) else {}
            status = str(result.get("status", "")).strip().lower()
            if ok and status in {"ok", "healthy", "up"}:
                return True, "ok"
            return False, payload.get("error", {}).get("code", "ipc_not_ok")
        except Exception as exc:  # noqa: BLE001
            return False, f"ipc_error:{exc}"
        finally:
            if writer is not None:
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:  # noqa: BLE001
                    pass

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
        # Приоритет: IPC (нативный режим Krab Ear), затем HTTP fallback.
        ipc_ok, _ipc_status = await self._ping_ipc_health()
        if ipc_ok:
            return True
        if not self.base_url:
            return False

        try:
            status_code, payload = await self._fetch_health_payload()
            return status_code == 200 and self._is_ok_payload(payload)
        except Exception as exc:  # noqa: BLE001 - health не должен ронять runtime
            logger.debug("krab_ear_health_http_failed", error=str(exc), base_url=self.base_url)
            return False

    async def health_report(self) -> dict[str, Any]:
        """Подробный отчет с latency и detail payload."""
        started = time.monotonic()
        ipc_ok, ipc_status = await self._ping_ipc_health()
        if ipc_ok:
            return {
                "ok": True,
                "status": "ok",
                "latency_ms": int((time.monotonic() - started) * 1000),
                "source": f"ipc:{self.socket_path}",
                "detail": {
                    "mode": "ipc",
                    "socket_path": str(self.socket_path),
                    "status": ipc_status,
                },
            }
        if not self.base_url:
            return {
                "ok": False,
                "status": ipc_status,
                "latency_ms": int((time.monotonic() - started) * 1000),
                "source": f"ipc:{self.socket_path}",
                "detail": {"mode": "ipc", "socket_path": str(self.socket_path)},
            }

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
