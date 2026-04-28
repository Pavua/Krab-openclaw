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
import shutil
import subprocess
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx
import structlog

from ..core.subprocess_env import clean_subprocess_env

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

            # Используем self._mlx_model (env MLX_WHISPER_MODEL), а не hardcoded turbo:
            # раньше это значение игнорировалось и owner не мог сменить fallback модель
            # без правки кода. small-mlx — sane default для RU/EN на M-серии.
            result = mlx_whisper.transcribe(audio_path, path_or_hf_repo=self._mlx_model)
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


# ---------------------------------------------------------------------------
# Video frame extraction (Bug 5 follow-up — расширяет vision pipeline на
# video / video_note / animation / sticker, чтобы не отвечать только по caption).
# ---------------------------------------------------------------------------

# Тип callable для опциональной vision-обёртки кадра
FrameDescriber = Callable[[bytes, int], Awaitable[str]]

# Поддерживаемые стратегии семплирования кадров
_VALID_SAMPLE_STRATEGIES = ("uniform", "key-frames")


def _ffmpeg_binary() -> str | None:
    """Возвращает абсолютный путь до ffmpeg или None, если бинарь недоступен.

    Используем PATH из clean_subprocess_env() — туда уже добавлены homebrew-пути,
    что важно при запуске Krab под LaunchAgent.
    """
    env = clean_subprocess_env()
    path_value = env.get("PATH", "")
    found = shutil.which("ffmpeg", path=path_value) or shutil.which("ffmpeg")
    return found


def _probe_video_duration(video_path: str) -> float | None:
    """Возвращает длительность видео в секундах через ffprobe или None при ошибке."""
    env = clean_subprocess_env()
    ffprobe = shutil.which("ffprobe", path=env.get("PATH", "")) or shutil.which("ffprobe")
    if not ffprobe:
        return None
    try:
        result = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                video_path,
            ],
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
            check=False,
        )
        if result.returncode != 0:
            return None
        raw = (result.stdout or "").strip()
        return float(raw) if raw else None
    except (subprocess.TimeoutExpired, ValueError, OSError):
        return None


def extract_video_frames(
    video_path: str,
    *,
    max_frames: int = 3,
    sample_strategy: str = "uniform",
) -> list[bytes]:
    """Извлекает до max_frames кадров из видео.

    Args:
        video_path: путь к видеофайлу (mp4/webm/gif/etc.)
        max_frames: максимум кадров (>=1, hard cap)
        sample_strategy: 'uniform' — равномерно по таймлайну,
                         'key-frames' — только I-кадры (через select='eq(pict_type,I)')

    Returns:
        Список JPEG-байт каждого кадра. Пустой список если ffmpeg недоступен,
        видео не существует, видео битое или strategy unknown — функция НЕ падает,
        логирует warning и возвращает [].
    """
    if max_frames < 1:
        max_frames = 1
    if sample_strategy not in _VALID_SAMPLE_STRATEGIES:
        logger.warning(
            "perceptor_video_invalid_strategy",
            strategy=sample_strategy,
            valid=list(_VALID_SAMPLE_STRATEGIES),
        )
        return []

    path = Path(video_path)
    if not path.exists() or not path.is_file():
        logger.warning("perceptor_video_file_missing", path=str(path))
        return []

    ffmpeg = _ffmpeg_binary()
    if not ffmpeg:
        logger.warning("perceptor_video_ffmpeg_unavailable", path=str(path))
        return []

    env = clean_subprocess_env()
    frames: list[bytes] = []

    with tempfile.TemporaryDirectory(prefix="krab_video_frames_") as tmpdir:
        out_pattern = str(Path(tmpdir) / "frame_%03d.jpg")

        if sample_strategy == "key-frames":
            # Берём только I-кадры; ffmpeg сам ограничит выводом -frames:v
            cmd = [
                ffmpeg,
                "-y",
                "-i",
                str(path),
                "-vf",
                "select='eq(pict_type,I)'",
                "-vsync",
                "vfr",
                "-frames:v",
                str(max_frames),
                "-q:v",
                "3",
                out_pattern,
            ]
        else:
            # uniform: семплируем равномерно по длительности
            duration = _probe_video_duration(str(path))
            if duration and duration > 0.05:
                # max_frames точек, центрированных по сегментам;
                # при 1 кадре — берём середину, при N — равномерно с offset
                if max_frames == 1:
                    fps_expr = f"1/{duration:.6f}"  # один кадр за всё видео
                else:
                    # один кадр на каждый отрезок duration / max_frames
                    fps_expr = f"{max_frames}/{duration:.6f}"
                cmd = [
                    ffmpeg,
                    "-y",
                    "-i",
                    str(path),
                    "-vf",
                    f"fps={fps_expr}",
                    "-frames:v",
                    str(max_frames),
                    "-q:v",
                    "3",
                    out_pattern,
                ]
            else:
                # Не смогли узнать длительность (single-frame / битое probe) —
                # просто берём первые max_frames кадра подряд. Edge case: если
                # видео содержит ровно 1 кадр (gif/sticker), вернётся [frame_001].
                cmd = [
                    ffmpeg,
                    "-y",
                    "-i",
                    str(path),
                    "-frames:v",
                    str(max_frames),
                    "-q:v",
                    "3",
                    out_pattern,
                ]

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                timeout=60,
                env=env,
                check=False,
            )
        except subprocess.TimeoutExpired:
            logger.warning("perceptor_video_ffmpeg_timeout", path=str(path))
            return []
        except OSError as exc:
            logger.warning(
                "perceptor_video_ffmpeg_oserror",
                path=str(path),
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return []

        if proc.returncode != 0:
            stderr_tail = (proc.stderr or b"").decode("utf-8", errors="replace")[-400:]
            logger.warning(
                "perceptor_video_ffmpeg_failed",
                path=str(path),
                rc=proc.returncode,
                stderr_tail=stderr_tail,
            )
            return []

        # Собираем созданные jpg в порядке имён
        for jpg in sorted(Path(tmpdir).glob("frame_*.jpg")):
            try:
                frames.append(jpg.read_bytes())
            except OSError as exc:
                logger.warning(
                    "perceptor_video_frame_read_failed",
                    file=str(jpg),
                    error=str(exc),
                )
            if len(frames) >= max_frames:
                break

    logger.info(
        "perceptor_video_frames_extracted",
        path=str(path),
        frames=len(frames),
        strategy=sample_strategy,
    )
    return frames


async def _default_frame_describer(frame: bytes, index: int) -> str:
    """Заглушка: описывает кадр без vision-провайдера.

    Bridge может передать свой `frame_describer`, который вызовет реальный
    OCR/image_analysis pipeline (через ту же модель что и фото).
    """
    return f"[видео-кадр #{index + 1}, {len(frame)} байт]"


async def process_video_message(
    file_path: str,
    caption: str | None = None,
    *,
    max_frames: int = 3,
    sample_strategy: str = "uniform",
    frame_describer: FrameDescriber | None = None,
) -> str:
    """Возвращает агрегированный текст для feed в LLM.

    Извлекает кадры → каждый прогоняется через `frame_describer`
    (по умолчанию плейсхолдер). Caption (если есть) приклеивается сверху.

    Возвращает пустую строку, если кадров нет и caption пустой —
    чтобы вызывающий код мог решить fallback'ить или дропать.
    """
    describer = frame_describer or _default_frame_describer
    caption_clean = (caption or "").strip()

    frames = await asyncio.get_event_loop().run_in_executor(
        None,
        lambda: extract_video_frames(
            file_path,
            max_frames=max_frames,
            sample_strategy=sample_strategy,
        ),
    )

    descriptions: list[str] = []
    for i, frame in enumerate(frames):
        try:
            text = await describer(frame, i)
        except Exception as exc:
            logger.warning(
                "perceptor_video_describer_failed",
                index=i,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            continue
        text = (text or "").strip()
        if text:
            descriptions.append(text)

    parts: list[str] = []
    if caption_clean:
        parts.append(f"Подпись к видео: {caption_clean}")
    if descriptions:
        parts.append("Содержимое видео по кадрам:")
        parts.extend(f"  {i + 1}. {d}" for i, d in enumerate(descriptions))
    elif not caption_clean:
        # Видео без caption и кадры не извлеклись — пустой результат
        return ""
    elif not descriptions:
        # Caption есть, но кадры пустые — даём знать LLM
        parts.append("(визуальное содержимое видео не удалось извлечь)")

    return "\n".join(parts).strip()


# Глобальный синглтон для импорта в хендлерах
perceptor = Perceptor()
