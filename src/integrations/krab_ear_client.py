# -*- coding: utf-8 -*-
"""
Клиент Krab Ear backend.

Назначение:
1) Проверять живость локального backend (`/health`) для единого ecosystem health.
2) Изолировать детали подключения к Krab Ear (URL/таймаут) от web/runtime слоя.
3) Предоставлять такой же контракт `health_check()` как у остальных клиентов.
4) Cross-project distributed tracing (Sentry): пробрасывать `sentry-trace`
   и `baggage` в Ear backend через HTTP headers и IPC params. Это связывает
   issues между python-fastapi (Main Krab) и krab-ear-backend в Sentry UI.
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


def _get_sentry_trace_headers() -> dict[str, str]:
    """Возвращает dict с `sentry-trace` и `baggage` для propagation.

    Graceful: если sentry_sdk не установлен или нет активного span — пустой dict.
    Это позволяет cross-project trace linking без hard-dependency на SDK.
    """
    headers: dict[str, str] = {}
    try:
        import sentry_sdk  # type: ignore[import-not-found]
    except ImportError:
        return headers

    try:
        # Публичный API (SDK 2.x): get_traceparent / get_baggage на модуле.
        traceparent = (
            sentry_sdk.get_traceparent() if hasattr(sentry_sdk, "get_traceparent") else None
        )
        baggage = sentry_sdk.get_baggage() if hasattr(sentry_sdk, "get_baggage") else None
    except Exception:  # noqa: BLE001
        traceparent = None
        baggage = None

    if not traceparent:
        # Fallback через tracing_utils (некоторые версии SDK).
        try:
            from sentry_sdk.tracing_utils import get_traceparent as _get_tp  # type: ignore

            traceparent = _get_tp()
        except Exception:  # noqa: BLE001
            traceparent = None

    if traceparent:
        headers["sentry-trace"] = traceparent
    if baggage:
        headers["baggage"] = baggage
    return headers


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

        Cross-project tracing: Sentry trace headers прокидываются через
        `params._trace` (Ear backend должен прочитать и передать в continue_trace).
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
            params: dict[str, Any] = {}
            trace_headers = _get_sentry_trace_headers()
            if trace_headers:
                params["_trace"] = trace_headers
            request = {"id": "health", "method": "ping", "params": params}
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
        """Возвращает `(http_status, payload)` для `/health`.

        Cross-project tracing: пробрасывает `sentry-trace` и `baggage` headers,
        чтобы Ear backend (при настроенном FastApiIntegration) склеил span-tree.
        А также тегирует вызов как `target_service=krab-ear` для фильтрации.
        """
        url = f"{self.base_url}/health"
        headers = _get_sentry_trace_headers()
        # Отметим target_service на активной Sentry-span, чтобы было фильтруемо.
        try:
            import sentry_sdk  # type: ignore[import-not-found]

            sentry_sdk.set_tag("target_service", "krab-ear")
        except Exception:  # noqa: BLE001
            pass
        async with httpx.AsyncClient(timeout=self.timeout_sec) as client:
            response = await client.get(url, headers=headers or None)
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
