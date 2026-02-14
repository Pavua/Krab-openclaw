"""Клиент Krab Voice Gateway для OpenClaw Krab.

Зачем:
1) Дать Telegram-боту тонкий контроль звонковых сессий.
2) Не переносить голосовую бизнес-логику в бот (thin-client подход).
3) Нормализовать события stream в единый schema-формат для telemetry.
"""

from __future__ import annotations

import aiohttp
from typing import Any, Optional


class VoiceGatewayClient:
    """Async HTTP-клиент к Krab Voice Gateway."""

    def __init__(self, base_url: str = "http://127.0.0.1:8090", api_key: Optional[str] = None):
        self.base_url = base_url.rstrip("/")
        self.api_key = (api_key or "").strip()

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Krab/VoiceGatewayClient",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    async def health_check(self) -> bool:
        """Проверяет доступность gateway."""
        url = f"{self.base_url}/health"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=3) as resp:
                    return resp.status == 200
        except Exception:
            return False

    async def start_session(
        self,
        translation_mode: str = "auto_to_ru",
        notify_mode: str = "auto_on",
        tts_mode: str = "hybrid",
        source: str = "mic",
    ) -> dict[str, Any]:
        """Создаёт новую звонковую сессию."""
        payload = {
            "translation_mode": translation_mode,
            "notify_mode": notify_mode,
            "tts_mode": tts_mode,
            "source": source,
            "meta": {"started_by": "openclaw_krab"},
        }
        return await self._request("POST", "/v1/sessions", payload=payload)

    async def stop_session(self, session_id: str) -> dict[str, Any]:
        """Останавливает сессию."""
        return await self._request("DELETE", f"/v1/sessions/{session_id}")

    async def get_session(self, session_id: str) -> dict[str, Any]:
        """Возвращает состояние сессии."""
        return await self._request("GET", f"/v1/sessions/{session_id}")

    async def set_notify_mode(self, session_id: str, notify_mode: str) -> dict[str, Any]:
        """Меняет политику уведомления собеседника."""
        return await self._request("PATCH", f"/v1/sessions/{session_id}", payload={"notify_mode": notify_mode})

    async def set_translation_mode(self, session_id: str, translation_mode: str) -> dict[str, Any]:
        """Меняет режим перевода у активной сессии."""
        return await self._request(
            "PATCH",
            f"/v1/sessions/{session_id}",
            payload={"translation_mode": translation_mode},
        )

    async def get_diagnostics(self, session_id: str) -> dict[str, Any]:
        """Возвращает диагностический срез сессии (latency/counters/fallback/cache)."""
        return await self._request("GET", f"/v1/sessions/{session_id}/diagnostics")

    async def get_diagnostics_why(self, session_id: str) -> dict[str, Any]:
        """Возвращает explain-пакет: почему перевод не появился/деградировал."""
        return await self._request("GET", f"/v1/sessions/{session_id}/diagnostics/why")

    async def build_summary(self, session_id: str, max_items: int = 30) -> dict[str, Any]:
        """Запрашивает summary звонка и action items."""
        safe_max_items = max(1, min(int(max_items), 200))
        return await self._request(
            "POST",
            f"/v1/sessions/{session_id}/summary",
            payload={"max_items": safe_max_items},
        )

    async def quick_phrase(
        self,
        session_id: str,
        text: str,
        source_lang: str = "ru",
        target_lang: str = "es",
        voice: str = "default",
        style: str = "neutral",
    ) -> dict[str, Any]:
        """Мгновенная реплика для озвучки: перевод + tts.ready."""
        return await self._request(
            "POST",
            f"/v1/sessions/{session_id}/quick-phrase",
            payload={
                "text": text,
                "source_lang": source_lang,
                "target_lang": target_lang,
                "voice": voice,
                "style": style,
            },
        )

    async def list_quick_phrases(
        self,
        source_lang: str = "ru",
        target_lang: str = "es",
        category: str = "all",
        limit: int = 25,
    ) -> dict[str, Any]:
        """Возвращает библиотеку быстрых фраз."""
        safe_limit = max(1, min(int(limit), 200))
        path = (
            f"/v1/quick-phrases?source_lang={source_lang}"
            f"&target_lang={target_lang}&category={category}&limit={safe_limit}"
        )
        return await self._request("GET", path)

    async def tune_runtime(
        self,
        session_id: str,
        *,
        buffering_mode: Optional[str] = None,
        target_latency_ms: Optional[int] = None,
        vad_sensitivity: Optional[float] = None,
    ) -> dict[str, Any]:
        """Подкручивает runtime-параметры VAD/буфера."""
        payload: dict[str, Any] = {}
        if buffering_mode is not None:
            payload["buffering_mode"] = buffering_mode
        if target_latency_ms is not None:
            payload["target_latency_ms"] = int(target_latency_ms)
        if vad_sensitivity is not None:
            payload["vad_sensitivity"] = float(vad_sensitivity)
        return await self._request("PATCH", f"/v1/sessions/{session_id}/runtime", payload=payload)

    async def estimate_cost(
        self,
        *,
        country: str = "ES",
        minutes_inbound: float = 200.0,
        minutes_outbound_landline: float = 100.0,
        minutes_outbound_mobile: float = 100.0,
        minutes_media_stream: float = 400.0,
        use_live_pricing: bool = True,
    ) -> dict[str, Any]:
        """Оценивает telephony+AI стоимость через endpoint Gateway."""
        query = (
            f"/v1/telephony/cost/estimate"
            f"?country={country.upper()}"
            f"&minutes_inbound={max(0.0, float(minutes_inbound))}"
            f"&minutes_outbound_landline={max(0.0, float(minutes_outbound_landline))}"
            f"&minutes_outbound_mobile={max(0.0, float(minutes_outbound_mobile))}"
            f"&minutes_media_stream={max(0.0, float(minutes_media_stream))}"
            f"&use_live_pricing={'true' if use_live_pricing else 'false'}"
        )
        return await self._request("GET", query)

    async def _request(self, method: str, path: str, payload: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        """Универсальный HTTP-вызов с единым форматом ошибок."""
        url = f"{self.base_url}{path}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.request(
                    method,
                    url,
                    json=payload,
                    headers=self._headers(),
                    timeout=15,
                ) as resp:
                    text = await resp.text()
                    data: Any
                    try:
                        data = await resp.json(content_type=None)
                    except Exception:
                        data = {"raw": text}

                    if 200 <= resp.status < 300:
                        if isinstance(data, dict):
                            return {"ok": True, "result": data}
                        return {"ok": True, "result": {"raw": data}}
                    return {"ok": False, "error": f"http_{resp.status}", "details": data}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    async def get_stream_event(self, session_id: str, timeout_sec: float = 8.0) -> dict[str, Any]:
        """
        Читает одно событие из WS stream и возвращает в нормализованном schema.
        Используется для диагностики/контрактных smoke без постоянного consumer-loop.
        """
        ws_url = f"{self.base_url.replace('http://', 'ws://').replace('https://', 'wss://')}/v1/sessions/{session_id}/stream"
        timeout = aiohttp.ClientTimeout(total=max(1.0, float(timeout_sec)))
        try:
            async with aiohttp.ClientSession(timeout=timeout, headers=self._headers()) as session:
                async with session.ws_connect(ws_url) as ws:
                    message = await ws.receive_json()
            return {"ok": True, "event": self.normalize_stream_event(message)}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @staticmethod
    def normalize_stream_event(message: dict[str, Any]) -> dict[str, Any]:
        """
        Нормализует событие Voice Gateway к единому schema:
        - schema_version, session_id, event_type, source, severity, latency_ms, ts, data
        """
        raw = message or {}
        raw_type = str(raw.get("type", "unknown")).strip() or "unknown"
        data = raw.get("data") if isinstance(raw.get("data"), dict) else {}

        # Поддержка нескольких naming-паттернов.
        session_id = (
            str(raw.get("session_id") or data.get("session_id") or data.get("id") or "")
            .strip()
        )
        source = str(raw.get("source") or data.get("source") or "voice_gateway").strip() or "voice_gateway"
        ts = str(raw.get("ts") or data.get("ts") or "").strip()

        latency_raw = raw.get("latency_ms", data.get("latency_ms", 0))
        try:
            latency_ms = max(0, int(latency_raw))
        except Exception:
            latency_ms = 0

        severity = str(raw.get("severity") or data.get("severity") or "").strip().lower()
        if not severity:
            if raw_type.endswith(".error") or raw_type in {"error", "call.error"}:
                severity = "high"
            elif raw_type in {"call.state", "stt.partial", "translation.partial", "tts.ready"}:
                severity = "info"
            else:
                severity = "low"

        return {
            "schema_version": "1.0",
            "session_id": session_id,
            "event_type": raw_type,
            "source": source,
            "severity": severity,
            "latency_ms": latency_ms,
            "ts": ts,
            "data": data,
        }
