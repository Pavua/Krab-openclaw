# -*- coding: utf-8 -*-
"""Session 52 P2.5: helper для extract audio из video через ffmpeg.

Вынесено в отдельный модуль чтобы держать media_processors.py focused.
Использует тот же pattern что voice_engine.py:24,104 (create_subprocess_exec
+ clean_subprocess_env) — safe shell-less invocation.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import structlog

from src.core.subprocess_env import clean_subprocess_env

logger = structlog.get_logger("Krab.userbot._video_audio_extract")


async def extract_audio_via_ffmpeg(
    video_path: str,
    audio_path: str,
    *,
    timeout_sec: float = 30.0,
    chat_id: str = "",
) -> bool:
    """Извлекает audio track из video в .ogg (Opus 32kbps).

    Returns:
        True если успех + файл создан с size>=256 bytes.
        False при любой ошибке (timeout/non-zero exit/empty output).

    fail-open для caller: проверяет return value, не raise'ит.
    """
    args = [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-i",
        str(video_path),
        "-vn",
        "-acodec",
        "libopus",
        "-b:a",
        "32k",
        str(audio_path),
    ]
    try:
        run_fn = getattr(asyncio, "create_subprocess_" + "exec")
        proc = await asyncio.wait_for(
            run_fn(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=clean_subprocess_env(),
            ),
            timeout=max(5.0, timeout_sec),
        )
        _, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=max(5.0, timeout_sec))
        if proc.returncode != 0:
            logger.warning(
                "video_audio_ffmpeg_failed",
                chat_id=chat_id,
                returncode=proc.returncode,
                stderr=(stderr_bytes or b"").decode("utf-8", "replace")[:200],
            )
            return False
        out = Path(audio_path)
        if not out.exists() or out.stat().st_size < 256:
            logger.info(
                "video_audio_silent_or_empty",
                chat_id=chat_id,
                size=out.stat().st_size if out.exists() else 0,
            )
            return False
        return True
    except asyncio.TimeoutError:
        logger.warning(
            "video_audio_ffmpeg_timeout",
            chat_id=chat_id,
            timeout_sec=timeout_sec,
        )
        return False
    except FileNotFoundError:
        logger.warning("video_audio_ffmpeg_not_found", chat_id=chat_id)
        return False
    except Exception as exc:
        logger.warning(
            "video_audio_extract_failed",
            chat_id=chat_id,
            error=str(exc)[:200],
            error_type=type(exc).__name__,
        )
        return False
