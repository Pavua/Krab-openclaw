# -*- coding: utf-8 -*-
"""
Perceptor — STT-модуль через Krab Voice Gateway с fallback на mlx_whisper.

Цепочка транскрипции (primary → fallback):
  1. Voice Gateway /stt (http://127.0.0.1:8090) — если жив
  2. mlx_whisper локально — если Gateway недоступен

При недоступности Voice Gateway — fallback на mlx_whisper (если установлен).
Если оба backend провалились → возвращает error markup `[transcription_failed: ...]`.

Ожидаемый интерфейс bootstrap/runtime.py:
    perceptor = Perceptor(config={})
    perceptor.whisper_model      # str | None — для логирования
    perceptor.stt_isolated_worker  # bool — для логирования
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import httpx
import structlog

logger = structlog.get_logger(__name__)

# mlx_whisper model для fallback. Tiny = быстрый, small = лучше качество.
# Переопределить через env MLX_WHISPER_MODEL.
_MLX_WHISPER_MODEL_DEFAULT = "mlx-community/whisper-small-mlx"

# Префикс error markup — детектируется downstream
TRANSCRIPTION_FAILED_PREFIX = "[transcription_failed:"


class Perceptor:
    """STT/TTS-обёртка через Voice Gateway + mlx_whisper fallback."""

    # Совместимость с bootstrap/runtime.py — эти атрибуты читаются при старте
    stt_isolated_worker: bool = False
    whisper_model: str | None = None

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self._config = config or {}
        self._gateway_url = str(os.getenv("VOICE_GATEWAY_URL", "http://127.0.0.1:8090")).rstrip("/")
        self._api_key = str(
            os.getenv("KRAB_VOICE_API_KEY", "") or os.getenv("VOICE_GATEWAY_API_KEY", "")
        ).strip()
        self._mlx_model = (
            str(os.getenv("MLX_WHISPER_MODEL", _MLX_WHISPER_MODEL_DEFAULT)).strip()
            or _MLX_WHISPER_MODEL_DEFAULT
        )
        self.whisper_model = self._mlx_model  # логируется в bootstrap
        logger.info("perceptor_init", gateway_url=self._gateway_url, mlx_model=self._mlx_model)

    def _headers(self) -> dict[str, str]:
        if self._api_key:
            return {"Authorization": f"Bearer {self._api_key}"}
        return {}

    async def _gateway_alive(self) -> bool:
        """Быстрая проверка доступности Voice Gateway (timeout 2s)."""
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                resp = await client.get(f"{self._gateway_url}/health")
                return resp.status_code < 500
        except Exception:
            return False

    async def _transcribe_via_gateway(self, audio_bytes: bytes, lang: str = "auto") -> str:
        """Транскрипция через Voice Gateway /stt."""
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

    async def _transcribe_via_mlx(self, audio_path: str) -> str:
        """
        Fallback: транскрипция через mlx_whisper локально.

        Запускается в executor чтобы не блокировать event loop.
        """
        import functools

        import mlx_whisper

        def _run() -> str:
            result = mlx_whisper.transcribe(
                audio_path,
                path_or_hf_repo=self._mlx_model,
                verbose=False,
            )
            return str(result.get("text") or "").strip()

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, functools.partial(_run))

    async def transcribe_audio(self, audio_bytes: bytes, lang: str = "auto") -> str:
        """
        Отправляет аудио на Voice Gateway /stt и возвращает транскрипт.

        При недоступности шлюза возвращает пустую строку (не падает).
        Fallback на mlx_whisper не применяется — только bytes path.
        Используй transcribe() для полного fallback.
        """
        if not audio_bytes:
            return ""
        try:
            return await self._transcribe_via_gateway(audio_bytes, lang)
        except Exception as exc:
            logger.warning("perceptor_transcribe_failed", error=str(exc), backend="gateway")
            return ""

    async def _transcribe_mlx_whisper(self, audio_path: str) -> str:
        """
        Fallback транскрипция через mlx_whisper (локальный Apple Silicon).

        Возвращает транскрипт или "" при недоступности/ошибке.
        """
        try:
            import mlx_whisper  # type: ignore[import-untyped]

            result = mlx_whisper.transcribe(
                audio_path, path_or_hf_repo="mlx-community/whisper-turbo"
            )
            text = str(result.get("text") or "").strip()
            logger.info("perceptor_mlx_whisper_ok", chars=len(text))
            return text
        except ImportError:
            logger.debug("perceptor_mlx_whisper_not_installed")
            return ""
        except Exception as exc:
            logger.warning("perceptor_mlx_whisper_failed", error=str(exc))
            return ""

    async def transcribe(self, audio_path: str, model_manager: object = None) -> str:
        """
        Транскрипция с fallback: Voice Gateway → mlx_whisper.

        Если оба backend провалились → возвращает error markup:
            "[transcription_failed: voice_gateway=<err>; mlx_whisper=<err>]"

        Вызывающий код (userbot/voice_profile.py) детектирует этот markup
        и сообщает пользователю честную причину сбоя.
        model_manager не используется (транскрипция не через LLM).
        """
        import pathlib

        path = pathlib.Path(audio_path)
        if not path.exists():
            logger.warning("perceptor_transcribe_file_not_found", path=str(path))
            return (
                f"{TRANSCRIPTION_FAILED_PREFIX} voice_gateway=file_not_found; mlx_whisper=skipped]"
            )
        try:
            audio_bytes = path.read_bytes()
        except Exception as exc:
            logger.warning("perceptor_transcribe_read_failed", path=str(path), error=str(exc))
            return f"{TRANSCRIPTION_FAILED_PREFIX} voice_gateway=read_error:{exc}; mlx_whisper=skipped]"

        # Backend 1: Voice Gateway
        gw_error: str = ""
        gw_text: str = ""
        try:
            gw_text = await self.transcribe_audio(audio_bytes)
        except Exception as exc:
            gw_error = str(exc)
        if not gw_text and not gw_error:
            gw_error = "empty_response"

        if gw_text:
            return gw_text

        # Backend 2: mlx_whisper fallback
        mlx_error: str = ""
        mlx_text: str = ""
        try:
            mlx_text = await self._transcribe_mlx_whisper(str(path))
        except Exception as exc:
            mlx_error = str(exc)
        if not mlx_text and not mlx_error:
            mlx_error = "empty_response"

        if mlx_text:
            logger.info("perceptor_mlx_fallback_used", gw_error=gw_error)
            return mlx_text

        # Оба backend провалились → error markup
        markup = (
            f"{TRANSCRIPTION_FAILED_PREFIX} "
            f"voice_gateway={gw_error or 'empty'}; "
            f"mlx_whisper={mlx_error or 'empty'}]"
        )
        logger.error("perceptor_both_backends_failed", markup=markup)
        return markup

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
                return bool(payload.get("ok")) or str(payload.get("status", "")).lower() in {
                    "ok",
                    "live",
                }
        except Exception:
            return False


# Глобальный синглтон для импорта в хендлерах
perceptor = Perceptor()
