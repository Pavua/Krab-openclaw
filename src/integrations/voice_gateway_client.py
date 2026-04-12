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
from typing import Any, cast

import httpx

from ..core.logger import get_logger
from ..core.voice_gateway_control_plane import VoiceGatewayControlPlane

logger = get_logger(__name__)


class VoiceGatewayClient(VoiceGatewayControlPlane):
    """HTTP-клиент Voice Gateway с health и control-plane методами."""

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
            api_key or os.getenv("KRAB_VOICE_API_KEY", "") or os.getenv("VOICE_GATEWAY_API_KEY", "")
        ).strip()
        self.timeout_sec = max(
            0.5, float(timeout_sec or os.getenv("VOICE_GATEWAY_TIMEOUT_SEC", "2.5"))
        )

    @staticmethod
    def _is_ok_payload(payload: dict[str, Any]) -> bool:
        """Единое правило определения здоровья из JSON-ответа /health."""
        if bool(payload.get("ok")):
            return True
        status = str(payload.get("status", "")).strip().lower()
        return status in {"ok", "healthy", "up"}

    def _headers(self) -> dict[str, str]:
        """Собирает стандартные заголовки клиента."""
        headers = {"Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_payload: dict[str, Any] | None = None,
        timeout_sec: float | None = None,
    ) -> tuple[int | None, dict[str, Any], str]:
        """
        Выполняет JSON-запрос к Voice Gateway и возвращает `(status, payload, error)`.

        Почему единый helper:
        - методы control-plane не должны плодить разрозненную HTTP-логику;
        - error handling должен быть одинаковым и пригодным для owner-facing API.
        """
        url = f"{self.base_url}{path}"
        try:
            async with httpx.AsyncClient(
                timeout=timeout_sec or self.timeout_sec, headers=self._headers()
            ) as client:
                response = await client.request(
                    method.upper(), url, params=params or None, json=json_payload or None
                )
        except Exception as exc:  # noqa: BLE001 - клиент должен возвращать structured error
            return None, {}, str(exc)

        payload: dict[str, Any] = {}
        content_type = str(response.headers.get("content-type", "")).lower()
        if "application/json" in content_type:
            try:
                payload = response.json() if response.content else {}
            except ValueError:
                payload = {}
        elif response.text:
            payload = {"raw": response.text}
        return response.status_code, payload, ""

    @staticmethod
    def _error_payload(
        status_code: int | None, error: str, *, detail: Any = None
    ) -> dict[str, Any]:
        """Нормализует network/HTTP ошибки под единый owner-facing формат."""
        if status_code is None:
            return {
                "ok": False,
                "error": error or "network_error",
                "detail": detail or error or "network_error",
            }
        if status_code >= 400:
            return {
                "ok": False,
                "error": f"http_{status_code}",
                "detail": detail if detail not in (None, "") else error or f"http_{status_code}",
            }
        return {
            "ok": False,
            "error": error or "unexpected_gateway_payload",
            "detail": detail if detail not in (None, "") else error or "unexpected_gateway_payload",
        }

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

    async def capabilities_report(self) -> dict[str, Any]:
        """Читает contract-first capabilities snapshot Voice Gateway."""
        started = time.monotonic()
        status_code, payload, error = await self._request_json("GET", "/v1/capabilities")
        if error:
            return {
                "ok": False,
                "status": "error",
                "latency_ms": int((time.monotonic() - started) * 1000),
                "source": f"{self.base_url}/v1/capabilities",
                "detail": error,
            }
        ok = bool(status_code == 200 and isinstance(payload, dict))
        return {
            "ok": ok,
            "status": "ok" if ok else f"http_{status_code}",
            "latency_ms": int((time.monotonic() - started) * 1000),
            "source": f"{self.base_url}/v1/capabilities",
            "detail": payload if isinstance(payload, dict) else {},
        }

    async def list_sessions(
        self, *, status: str | None = None, source: str | None = None, limit: int = 20
    ) -> dict[str, Any]:
        """Возвращает список translator-сессий."""
        params = {"limit": max(1, int(limit))}
        if status:
            params["status"] = str(status).strip()
        if source:
            params["source"] = str(source).strip()
        status_code, payload, error = await self._request_json("GET", "/v1/sessions", params=params)
        if error or status_code != 200 or not isinstance(payload, dict):
            return self._error_payload(status_code, error, detail=payload)
        items = (
            payload.get("items")
            if isinstance(payload.get("items"), list)
            else payload.get("sessions")
        )
        if not isinstance(items, list):
            items = []
        return {
            "ok": True,
            "count": int(payload.get("count") or len(items)),
            "items": [cast(dict[str, Any], item) for item in items if isinstance(item, dict)],
        }

    async def get_diagnostics(self, session_id: str) -> dict[str, Any]:
        """Читает diagnostics для конкретной translator-сессии."""
        status_code, payload, error = await self._request_json(
            "GET", f"/v1/sessions/{session_id}/diagnostics"
        )
        if error or status_code != 200 or not isinstance(payload, dict):
            return self._error_payload(status_code, error, detail=payload)
        return {"ok": True, "result": dict(payload.get("result") or payload)}

    async def get_diagnostics_why(self, session_id: str) -> dict[str, Any]:
        """Читает why-report для translator-сессии."""
        status_code, payload, error = await self._request_json(
            "GET", f"/v1/sessions/{session_id}/diagnostics/why"
        )
        if error or status_code != 200 or not isinstance(payload, dict):
            return self._error_payload(status_code, error, detail=payload)
        return {"ok": True, "result": dict(payload.get("result") or payload)}

    async def get_timeline_summary(self, session_id: str) -> dict[str, Any]:
        """Читает summary/timeline digest по сессии."""
        status_code, payload, error = await self._request_json(
            "GET", f"/v1/sessions/{session_id}/timeline/summary"
        )
        if error or status_code != 200 or not isinstance(payload, dict):
            return self._error_payload(status_code, error, detail=payload)
        return {"ok": True, "result": dict(payload.get("result") or payload)}

    async def get_timeline(self, session_id: str, *, limit: int = 8) -> dict[str, Any]:
        """Возвращает timeline items по сессии."""
        status_code, payload, error = await self._request_json(
            "GET",
            f"/v1/sessions/{session_id}/timeline",
            params={"limit": max(1, int(limit))},
        )
        if error or status_code != 200 or not isinstance(payload, dict):
            return self._error_payload(status_code, error, detail=payload)
        items = payload.get("items") if isinstance(payload.get("items"), list) else []
        return {
            "ok": True,
            "items": [cast(dict[str, Any], item) for item in items if isinstance(item, dict)],
        }

    async def get_timeline_stats(self, session_id: str, *, limit: int = 200) -> dict[str, Any]:
        """Возвращает статистику timeline."""
        status_code, payload, error = await self._request_json(
            "GET",
            f"/v1/sessions/{session_id}/timeline/stats",
            params={"limit": max(1, int(limit))},
        )
        if error or status_code != 200 or not isinstance(payload, dict):
            return self._error_payload(status_code, error, detail=payload)
        return {"ok": True, "result": dict(payload.get("result") or payload)}

    async def export_timeline(
        self, session_id: str, *, format: str = "md", limit: int = 40
    ) -> dict[str, Any]:
        """Экспортирует timeline в owner-facing формат."""
        status_code, payload, error = await self._request_json(
            "GET",
            f"/v1/sessions/{session_id}/timeline/export",
            params={"format": str(format or "md").strip() or "md", "limit": max(1, int(limit))},
        )
        if error or status_code != 200 or not isinstance(payload, dict):
            return self._error_payload(status_code, error, detail=payload)
        return {"ok": True, "result": dict(payload.get("result") or payload)}

    async def list_quick_phrases(
        self, *, source_lang: str = "", target_lang: str = ""
    ) -> dict[str, Any]:
        """Возвращает quick-phrase presets."""
        params: dict[str, Any] = {}
        if source_lang:
            params["source_lang"] = str(source_lang).strip()
        if target_lang:
            params["target_lang"] = str(target_lang).strip()
        status_code, payload, error = await self._request_json(
            "GET", "/v1/quick-phrases", params=params
        )
        if error or status_code != 200 or not isinstance(payload, dict):
            return self._error_payload(status_code, error, detail=payload)
        items = payload.get("items") if isinstance(payload.get("items"), list) else []
        return {
            "ok": True,
            "items": [cast(dict[str, Any], item) for item in items if isinstance(item, dict)],
        }

    async def start_session(
        self,
        *,
        source: str,
        translation_mode: str,
        notify_mode: str,
        tts_mode: str,
        src_lang: str,
        tgt_lang: str,
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Создаёт translator session."""
        payload = {
            "source": str(source).strip(),
            "translation_mode": str(translation_mode).strip(),
            "notify_mode": str(notify_mode).strip(),
            "tts_mode": str(tts_mode).strip(),
            "src_lang": str(src_lang).strip(),
            "tgt_lang": str(tgt_lang).strip(),
            "meta": dict(meta or {}),
        }
        status_code, response_payload, error = await self._request_json(
            "POST", "/v1/sessions", json_payload=payload, timeout_sec=max(5.0, self.timeout_sec)
        )
        if error or status_code not in {200, 201} or not isinstance(response_payload, dict):
            return self._error_payload(status_code, error, detail=response_payload)
        result = dict(response_payload.get("result") or response_payload)
        return {
            "ok": True,
            "session_id": str(response_payload.get("session_id") or result.get("id") or "").strip(),
            "result": result,
        }

    async def patch_session(self, session_id: str, **patch: Any) -> dict[str, Any]:
        """Обновляет translator session policy/state."""
        status_code, payload, error = await self._request_json(
            "PATCH",
            f"/v1/sessions/{session_id}",
            json_payload={key: value for key, value in patch.items() if value is not None},
            timeout_sec=max(5.0, self.timeout_sec),
        )
        if error or status_code != 200 or not isinstance(payload, dict):
            return self._error_payload(status_code, error, detail=payload)
        return {
            "ok": True,
            "session_id": session_id,
            "result": dict(payload.get("result") or payload),
        }

    async def stop_session(self, session_id: str) -> dict[str, Any]:
        """Останавливает translator session."""
        status_code, payload, error = await self._request_json(
            "POST", f"/v1/sessions/{session_id}/stop", timeout_sec=max(5.0, self.timeout_sec)
        )
        if (error or status_code not in {200, 204}) and status_code == 404:
            status_code, payload, error = await self._request_json(
                "DELETE", f"/v1/sessions/{session_id}", timeout_sec=max(5.0, self.timeout_sec)
            )
        if error or status_code not in {200, 204}:
            return self._error_payload(status_code, error, detail=payload)
        return {
            "ok": True,
            "session_id": session_id,
            "result": dict(payload.get("result") or payload),
        }

    async def tune_runtime(self, session_id: str, **patch: Any) -> dict[str, Any]:
        """Обновляет runtime tuning конкретной сессии."""
        status_code, payload, error = await self._request_json(
            "PATCH",
            f"/v1/sessions/{session_id}/runtime",
            json_payload={key: value for key, value in patch.items() if value is not None},
            timeout_sec=max(5.0, self.timeout_sec),
        )
        if error or status_code != 200 or not isinstance(payload, dict):
            return self._error_payload(status_code, error, detail=payload)
        return {
            "ok": True,
            "session_id": session_id,
            "result": dict(payload.get("result") or payload),
        }

    async def send_quick_phrase(
        self,
        session_id: str,
        *,
        text: str,
        source_lang: str = "",
        target_lang: str = "",
    ) -> dict[str, Any]:
        """Отправляет quick phrase через backend-контур Voice Gateway."""
        payload = {
            "text": str(text).strip(),
            "source_lang": str(source_lang).strip(),
            "target_lang": str(target_lang).strip(),
        }
        status_code, response_payload, error = await self._request_json(
            "POST",
            f"/v1/sessions/{session_id}/quick-phrase",
            json_payload=payload,
            timeout_sec=max(5.0, self.timeout_sec),
        )
        if error or status_code != 200 or not isinstance(response_payload, dict):
            return self._error_payload(status_code, error, detail=response_payload)
        return {
            "ok": True,
            "session_id": session_id,
            "result": dict(response_payload.get("result") or response_payload),
        }

    async def build_summary(self, session_id: str, *, max_items: int = 12) -> dict[str, Any]:
        """Строит summary для текущей сессии."""
        status_code, payload, error = await self._request_json(
            "POST",
            f"/v1/sessions/{session_id}/summary",
            json_payload={"max_items": max(1, int(max_items))},
            timeout_sec=max(5.0, self.timeout_sec),
        )
        if error or status_code != 200 or not isinstance(payload, dict):
            return self._error_payload(status_code, error, detail=payload)
        return {
            "ok": True,
            "session_id": session_id,
            "result": dict(payload.get("result") or payload),
        }

    async def list_mobile_devices(self, *, limit: int = 8) -> dict[str, Any]:
        """Возвращает companion registry."""
        status_code, payload, error = await self._request_json(
            "GET", "/v1/mobile/devices", params={"limit": max(1, int(limit))}
        )
        if error or status_code != 200 or not isinstance(payload, dict):
            return self._error_payload(status_code, error, detail=payload)
        items = payload.get("items") if isinstance(payload.get("items"), list) else []
        return {
            "ok": True,
            "items": [cast(dict[str, Any], item) for item in items if isinstance(item, dict)],
        }

    async def get_mobile_session_snapshot(self, device_id: str) -> dict[str, Any]:
        """Возвращает snapshot выбранного companion-device."""
        status_code, payload, error = await self._request_json(
            "GET", f"/v1/mobile/devices/{device_id}/snapshot"
        )
        if error or status_code != 200 or not isinstance(payload, dict):
            return self._error_payload(status_code, error, detail=payload)
        return {"ok": True, "result": dict(payload.get("result") or payload)}

    async def register_mobile_device(
        self,
        *,
        device_id: str,
        voip_push_token: str,
        apns_environment: str,
        app_version: str,
        locale: str,
        preferred_source_lang: str,
        preferred_target_lang: str,
        notify_default: bool,
    ) -> dict[str, Any]:
        """Создаёт или обновляет mobile device в registry."""
        payload = {
            "device_id": str(device_id).strip().lower(),
            "voip_push_token": str(voip_push_token).strip(),
            "apns_environment": str(apns_environment).strip(),
            "app_version": str(app_version).strip(),
            "locale": str(locale).strip(),
            "preferred_source_lang": str(preferred_source_lang).strip(),
            "preferred_target_lang": str(preferred_target_lang).strip(),
            "notify_default": bool(notify_default),
        }
        status_code, response_payload, error = await self._request_json(
            "POST",
            "/v1/mobile/devices",
            json_payload=payload,
            timeout_sec=max(5.0, self.timeout_sec),
        )
        if error or status_code not in {200, 201} or not isinstance(response_payload, dict):
            return self._error_payload(status_code, error, detail=response_payload)
        return {
            "ok": True,
            "device_id": str(response_payload.get("device_id") or payload["device_id"]).strip(),
            "result": dict(response_payload.get("result") or response_payload),
        }

    async def bind_mobile_device(self, device_id: str, *, session_id: str) -> dict[str, Any]:
        """Привязывает companion-device к translator session."""
        payload = {"session_id": str(session_id).strip()}
        status_code, response_payload, error = await self._request_json(
            "POST",
            f"/v1/mobile/devices/{str(device_id).strip().lower()}/bind",
            json_payload=payload,
            timeout_sec=max(5.0, self.timeout_sec),
        )
        if error or status_code != 200 or not isinstance(response_payload, dict):
            return self._error_payload(status_code, error, detail=response_payload)
        return {
            "ok": True,
            "device_id": str(device_id).strip().lower(),
            "session_id": str(session_id).strip(),
            "result": dict(response_payload.get("result") or response_payload),
        }

    async def delete_mobile_device(self, device_id: str) -> dict[str, Any]:
        """Удаляет устройство из companion registry."""
        normalized_device_id = str(device_id).strip().lower()
        status_code, payload, error = await self._request_json(
            "DELETE",
            f"/v1/mobile/devices/{normalized_device_id}",
            timeout_sec=max(5.0, self.timeout_sec),
        )
        if error or status_code not in {200, 204}:
            return self._error_payload(status_code, error, detail=payload)
        return {
            "ok": True,
            "device_id": normalized_device_id,
            "result": dict(payload.get("result") or payload),
        }

    async def push_event(
        self, session_id: str, *, event_type: str, data: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Публикует произвольное событие в realtime-поток сессии.

        Используется для отправки reasoning.suggestion / reasoning.context
        от Krab Core в активную звонковую сессию Voice Gateway.
        """
        payload = {"type": str(event_type).strip(), "data": dict(data or {})}
        status_code, response_payload, error = await self._request_json(
            "POST",
            f"/v1/sessions/{session_id}/events",
            json_payload=payload,
            timeout_sec=max(5.0, self.timeout_sec),
        )
        if error or status_code != 200 or not isinstance(response_payload, dict):
            return self._error_payload(status_code, error, detail=response_payload)
        return {
            "ok": True,
            "session_id": session_id,
            "result": dict(response_payload.get("result") or response_payload),
        }

    async def session_tts(
        self, session_id: str, *, text: str, voice: str = "default", style: str = "neutral"
    ) -> dict[str, Any]:
        """Генерирует речь и публикует tts.ready в поток сессии.

        Используется для озвучки LLM-подсказок (reasoning.suggestion)
        непосредственно в активную звонковую сессию.
        """
        payload = {
            "text": str(text).strip(),
            "voice": str(voice).strip(),
            "style": str(style).strip(),
        }
        status_code, response_payload, error = await self._request_json(
            "POST",
            f"/v1/sessions/{session_id}/tts",
            json_payload=payload,
            timeout_sec=max(10.0, self.timeout_sec),
        )
        if error or status_code != 200 or not isinstance(response_payload, dict):
            return self._error_payload(status_code, error, detail=response_payload)
        return {
            "ok": True,
            "session_id": session_id,
            "result": dict(response_payload.get("result") or response_payload),
        }
