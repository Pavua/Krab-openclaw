# -*- coding: utf-8 -*-
"""
Perceptor — STT-модуль через Krab Voice Gateway.

Делегирует распознавание речи на Voice Gateway (/stt),
а синтез речи — в voice_engine.py.

Ожидаемый интерфейс bootstrap/runtime.py:
    perceptor = Perceptor(config={})
    perceptor.whisper_model      # str | None — для логирования
    perceptor.stt_isolated_worker  # bool — для логирования
"""

from __future__ import annotations

import os
from typing import Any

import httpx
import structlog

logger = structlog.get_logger(__name__)


class Perceptor:
    """STT/TTS-обёртка через Voice Gateway."""

    # Совместимость с bootstrap/runtime.py — эти атрибуты читаются при старте
    stt_isolated_worker: bool = False
    whisper_model: str | None = None

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self._config = config or {}
        self._gateway_url = str(
            os.getenv("VOICE_GATEWAY_URL", "http://127.0.0.1:8090")
        ).rstrip("/")
        self._api_key = str(
            os.getenv("KRAB_VOICE_API_KEY", "")
            or os.getenv("VOICE_GATEWAY_API_KEY", "")
        ).strip()
        logger.info("perceptor_init", gateway_url=self._gateway_url)

    def _headers(self) -> dict[str, str]:
        if self._api_key:
            return {"Authorization": f"Bearer {self._api_key}"}
        return {}

    async def transcribe_audio(self, audio_bytes: bytes, lang: str = "auto") -> str:
        """
        Отправляет аудио на Voice Gateway /stt и возвращает транскрипт.

        При недоступности шлюза возвращает пустую строку (не падает).
        """
        if not audio_bytes:
            return ""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    f"{self._gateway_url}/stt",
                    headers=self._headers(),
                    files={"audio": ("audio.ogg", audio_bytes, "audio/ogg")},
                    data={"language": lang} if lang and lang != "auto" else {},
                )
                resp.raise_for_status()
                payload = resp.json()
                return str(payload.get("text") or payload.get("transcript") or "").strip()
        except Exception as exc:
            logger.warning("perceptor_transcribe_failed", error=str(exc))
            return ""

    async def speak(self, text: str, voice_id: str | None = None) -> str:
        """
        Синтезирует речь через voice_engine.py.

        Возвращает путь к аудиофайлу или пустую строку при ошибке.
        """
        try:
            from ..voice_engine import text_to_speech

            filename = await text_to_speech(text, voice=voice_id)
            return filename or ""
        except Exception as exc:
            logger.warning("perceptor_speak_failed", error=str(exc))
            return ""

    async def health_check(self) -> bool:
        """True если Voice Gateway доступен."""
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(f"{self._gateway_url}/health")
                payload = resp.json()
                return bool(payload.get("ok")) or str(payload.get("status", "")).lower() in {"ok", "live"}
        except Exception:
            return False


# Глобальный синглтон для импорта в хендлерах
perceptor = Perceptor()
