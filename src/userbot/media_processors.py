# -*- coding: utf-8 -*-
"""Wave 31-H: MediaProcessorsMixin — выделяет document/video media-pipeline.

Зачем:
- bridge до 31-H содержал ~5528 LOC, media-блок (3 метода + 3 константы) —
  cohesive 340 LOC обработки документов и видео.
- Mixin использует: ``self.client.download_media``, ``self._safe_edit``
  (TelegramSendUtilsMixin), ``self._describe_video_frame`` (recursive call).

Контракт:
- ``_process_document_message`` — download документ, inline-вставка для текстовых
  файлов ≤ ``_DOC_INLINE_BYTES``, иначе сохранение в tmp + path-reference.
- ``_describe_video_frame`` — vision-описание одного кадра видео (timeout 25s).
- ``_process_video_message`` — download video/animation/video_note + perceptor +
  per-frame describer + archive.db summary save.

Все методы fail-open: при любой ошибке возвращают исходный query, чтобы
не блокировать LLM-flow. Notice-сообщения (📎 / 🎞) — best-effort, ошибки игнорятся.
"""

from __future__ import annotations

import asyncio
import base64
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
import structlog

from ..config import config
from ..openclaw_client import openclaw_client

if TYPE_CHECKING:
    from pyrogram import Client
    from pyrogram.types import Message

logger = structlog.get_logger("Krab.userbot.media_processors")


class MediaProcessorsMixin:
    """Mixin: document download + video frame extraction + vision-описание."""

    # Атрибуты, которые ожидаются на host-классе (KraabUserbot):
    client: "Client | None"
    # _safe_edit — TelegramSendUtilsMixin

    # ─── Class-level constants ───────────────────────────────────────────────

    # Расширения, которые обрабатываем как plain-text (встраиваем содержимое в запрос).
    _TEXT_EXTENSIONS: frozenset[str] = frozenset(
        {
            ".txt",
            ".md",
            ".py",
            ".js",
            ".ts",
            ".jsx",
            ".tsx",
            ".json",
            ".yaml",
            ".yml",
            ".toml",
            ".ini",
            ".cfg",
            ".sh",
            ".bash",
            ".zsh",
            ".log",
            ".csv",
            ".xml",
            ".html",
            ".css",
            ".scss",
            ".sql",
            ".rs",
            ".go",
            ".java",
            ".kt",
            ".swift",
            ".c",
            ".cpp",
            ".h",
            ".hpp",
            ".rb",
            ".php",
            ".env",
            ".conf",
        }
    )
    # Максимальный размер файла и инлайн-вставки.
    _DOC_MAX_BYTES: int = 5 * 1024 * 1024  # 5 MB — не скачиваем больше
    _DOC_INLINE_BYTES: int = 80 * 1024  # 80 KB — встраиваем содержимое текстом

    # ─── Document pipeline ───────────────────────────────────────────────────

    async def _process_document_message(
        self,
        *,
        message: "Message",
        query: str,
        temp_msg: Any,
        is_self: bool,
    ) -> str | None:
        """
        Скачивает документ из Telegram и обогащает query его содержимым.

        Возвращает обновлённый query или None, если нужно прервать обработку.
        Текстовые файлы <= _DOC_INLINE_BYTES вставляются inline; более крупные
        и бинарные — сохраняются в tmp и передаются путём (MCP filesystem может прочесть).
        """
        doc = getattr(message, "document", None)
        if not doc:
            return query

        file_name: str = str(getattr(doc, "file_name", None) or "document").strip() or "document"
        mime_type: str = str(getattr(doc, "mime_type", None) or "").strip()
        file_size: int = int(getattr(doc, "file_size", 0) or 0)

        if file_size > self._DOC_MAX_BYTES:
            size_kb = file_size // 1024
            limit_kb = self._DOC_MAX_BYTES // 1024
            err = f"❌ Файл слишком большой ({size_kb} KB). Максимум {limit_kb} KB."
            if is_self:
                await self._safe_edit(message, f"🦀 {query or file_name}\n\n{err}")
            else:
                await self._safe_edit(temp_msg, err)
            return None

        notice = f"📎 *Загружаю файл {file_name}...*"
        if is_self:
            await self._safe_edit(message, f"🦀 {query or file_name}\n\n{notice}")
        else:
            await self._safe_edit(temp_msg, notice)

        doc_dir = Path(getattr(config, "DOCUMENT_DOWNLOAD_DIR", "/tmp/krab_docs"))
        doc_dir.mkdir(parents=True, exist_ok=True)
        ts_ms = int(time.time() * 1000)
        msg_id = int(getattr(message, "id", 0) or 0)
        safe_name = "".join(c for c in file_name if c.isalnum() or c in "._-")[:64] or "doc"
        doc_path = doc_dir / f"doc_{ts_ms}_{msg_id}_{safe_name}"

        download_timeout = float(getattr(config, "DOCUMENT_DOWNLOAD_TIMEOUT_SEC", 45.0))
        try:
            downloaded = await asyncio.wait_for(
                self.client.download_media(message, file_name=str(doc_path)),
                timeout=max(5.0, download_timeout),
            )
        except asyncio.TimeoutError:
            err = "❌ Таймаут загрузки файла. Попробуй отправить его ещё раз."
            if is_self:
                await self._safe_edit(message, f"🦀 {query or file_name}\n\n{err}")
            else:
                await self._safe_edit(temp_msg, err)
            return None
        except Exception as exc:
            logger.error("document_download_failed", file_name=file_name, error=str(exc))
            err = "❌ Не удалось загрузить файл. Попробуй отправить его ещё раз."
            if is_self:
                await self._safe_edit(message, f"🦀 {query or file_name}\n\n{err}")
            else:
                await self._safe_edit(temp_msg, err)
            return None

        if not downloaded:
            err = "❌ Файл не удалось скачать. Попробуй снова."
            if is_self:
                await self._safe_edit(message, f"🦀 {query or file_name}\n\n{err}")
            else:
                await self._safe_edit(temp_msg, err)
            return None

        _, ext = os.path.splitext(file_name.lower())
        is_text = ext in self._TEXT_EXTENSIONS or mime_type.startswith("text/")
        actual_size = doc_path.stat().st_size if doc_path.exists() else 0

        if is_text and actual_size <= self._DOC_INLINE_BYTES:
            try:
                content = doc_path.read_text(encoding="utf-8", errors="replace")
                doc_context = f"[Файл: {file_name}]\n```\n{content}\n```"
            except Exception as exc:
                logger.warning("document_read_failed", file_name=file_name, error=str(exc))
                doc_context = (
                    f"[Файл сохранён: {doc_path}]"
                    f" (mime: {mime_type or 'unknown'}, размер: {actual_size} байт)"
                )
        else:
            doc_context = (
                f"[Файл сохранён: {doc_path}]"
                f" (mime: {mime_type or 'unknown'}, размер: {actual_size} байт)"
            )

        return f"{doc_context}\n\n{query}".strip() if query else doc_context

    # ─── Video pipeline ──────────────────────────────────────────────────────

    async def _extract_and_transcribe_audio(
        self,
        video_path: str,
        *,
        chat_id: str,
    ) -> str:
        """Session 52 P2.5: audio track из video → Whisper transcript.

        Pipeline:
        1. ffmpeg извлекает audio в .ogg (Opus 32kbps) — helper в
           ``_video_audio_extract.extract_audio_via_ffmpeg`` (safe shell-less
           pattern, same что voice_engine.py).
        2. ``perceptor.transcribe(.ogg)`` — Voice Gateway → mlx_whisper fallback
           (existing pipeline для voice messages).
        3. Temp .ogg удаляется в finally.

        Fail-open: empty string на любую ошибку — LLM просто не получит audio
        context, не будет ложного claim что слышал.

        Env tunables:
        - ``KRAB_VIDEO_AUDIO_FFMPEG_TIMEOUT_SEC`` (default 30s)
        - ``KRAB_VIDEO_AUDIO_TRANSCRIBE_TIMEOUT_SEC`` (default 60s)
        """
        from src.modules.perceptor import perceptor  # noqa: PLC0415
        from src.userbot._video_audio_extract import (  # noqa: PLC0415
            extract_audio_via_ffmpeg,
        )

        ffmpeg_timeout = float(os.getenv("KRAB_VIDEO_AUDIO_FFMPEG_TIMEOUT_SEC", "30.0"))
        stt_timeout = float(os.getenv("KRAB_VIDEO_AUDIO_TRANSCRIBE_TIMEOUT_SEC", "60.0"))

        audio_path = Path(video_path).with_suffix(".ogg")
        try:
            ok = await extract_audio_via_ffmpeg(
                video_path,
                str(audio_path),
                timeout_sec=ffmpeg_timeout,
                chat_id=chat_id,
            )
            if not ok:
                return ""

            try:
                transcript = await asyncio.wait_for(
                    perceptor.transcribe(str(audio_path)),
                    timeout=max(10.0, stt_timeout),
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "video_audio_transcribe_timeout",
                    chat_id=chat_id,
                    timeout_sec=stt_timeout,
                )
                return ""

            transcript = (transcript or "").strip()
            if transcript.startswith("[transcription_failed"):
                logger.warning(
                    "video_audio_transcribe_returned_failure_markup",
                    chat_id=chat_id,
                    markup=transcript[:150],
                )
                return ""

            logger.info(
                "video_audio_transcribe_success",
                chat_id=chat_id,
                char_count=len(transcript),
                audio_size=audio_path.stat().st_size,
            )
            return transcript
        finally:
            try:
                if audio_path.exists():
                    audio_path.unlink()
            except Exception:  # noqa: BLE001
                pass

    async def _describe_frame_via_lmstudio(
        self,
        frame_b64: str,
        idx: int,
        *,
        chat_id: str,
        timeout_sec: float,
    ) -> str:
        """Session 52: vision describe через локальный LM Studio (Gemma 4).

        Bench Session 52: local Gemma 4 26B 4bit через LM Studio = **1.7-2.2s**
        per frame vs cloud Gemini ≥25s timeout (S51: 3/3 frames timed out).
        Решает hot-path regression от cloud vision describe.

        Env config:
        - ``KRAB_LOCAL_VISION_URL`` (default ``http://127.0.0.1:1234``)
        - ``KRAB_LOCAL_VISION_MODEL`` (default ``gemma-4-26b-a4b-it@4bit``)
        - ``LM_STUDIO_API_KEY`` (auth Bearer; existing env)

        Returns: stripped describe text, либо empty string при любой ошибке
        (perceptor пропустит этот кадр — fail-open).
        """
        prompt = (
            "Опиши кратко (1-2 предложения), что видно на кадре. "
            "Без вводных, без markdown — только описание."
        )

        url = os.getenv("KRAB_LOCAL_VISION_URL", "http://127.0.0.1:1234").rstrip("/")
        model = os.getenv("KRAB_LOCAL_VISION_MODEL", "gemma-4-26b-a4b-it@4bit")
        api_key = os.getenv("LM_STUDIO_API_KEY", "")

        body = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{frame_b64}"},
                        },
                    ],
                }
            ],
            "max_tokens": 200,
            "temperature": 0.0,
            "stream": False,
        }
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        try:
            async with httpx.AsyncClient(timeout=timeout_sec) as client:
                resp = await client.post(
                    f"{url}/v1/chat/completions",
                    json=body,
                    headers=headers,
                )
                resp.raise_for_status()
                data = resp.json()
            msg = (data.get("choices") or [{}])[0].get("message", {})
            text = msg.get("content") or msg.get("reasoning") or ""
            return text.strip()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "lmstudio_frame_describe_failed",
                idx=idx,
                error=str(exc)[:200],
                error_type=type(exc).__name__,
                url=url,
                model=model,
            )
            return ""

    async def _describe_video_frame(
        self,
        frame_bytes: bytes,
        idx: int,
        *,
        chat_id: str,
    ) -> str:
        """Краткое описание одного кадра видео через vision-модель.

        Session 52: routing logic
        - ``KRAB_LOCAL_VISION_ENABLED=1`` → local LM Studio (Gemma 4 26B)
          ~1.7-2.2s/frame. Решает cloud Gemini timeout regression (S51).
        - Иначе legacy cloud path через openclaw_client (force_cloud=True).

        Возвращает пустую строку при любой ошибке — perceptor пропустит кадр.
        """
        if not frame_bytes:
            return ""
        try:
            b64 = base64.b64encode(frame_bytes).decode("utf-8")
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "video_frame_b64_failed",
                idx=idx,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return ""

        timeout_sec = float(getattr(config, "VIDEO_FRAME_DESCRIBE_TIMEOUT_SEC", 25.0))

        # Session 52: local vision routing (default off → safe roll-out)
        if os.getenv("KRAB_LOCAL_VISION_ENABLED", "0") == "1":
            result = await self._describe_frame_via_lmstudio(
                b64,
                idx,
                chat_id=chat_id,
                timeout_sec=max(5.0, timeout_sec),
            )
            if result:
                logger.info(
                    "frame_describe_local_success",
                    idx=idx,
                    char_count=len(result),
                )
                return result
            # Empty — log + fall through to cloud (resilience)
            logger.info(
                "frame_describe_local_empty_fallthrough",
                idx=idx,
            )

        # Cloud path (existing, used when local disabled либо local empty)
        prompt = (
            "Опиши кратко (1-2 предложения), что видно на кадре. "
            "Без вводных, без markdown — только описание."
        )
        chunks: list[str] = []
        try:

            async def _consume() -> None:
                async for chunk in openclaw_client.send_message_stream(
                    message=prompt,
                    chat_id=f"{chat_id}:video-frame:{idx}",
                    images=[b64],
                    force_cloud=True,
                    disable_tools=True,
                ):
                    if chunk:
                        chunks.append(chunk)

            await asyncio.wait_for(_consume(), timeout=max(5.0, timeout_sec))
        except asyncio.TimeoutError:
            logger.warning("video_frame_describe_timeout", idx=idx, timeout_sec=timeout_sec)
            return ""
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "video_frame_describe_failed",
                idx=idx,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return ""

        return "".join(chunks).strip()

    async def _process_video_message(
        self,
        *,
        message: "Message",
        query: str,
        temp_msg: Any,
        is_self: bool,
        chat_id: str,
    ) -> str:
        """Скачивает video / video_note / animation и обогащает query содержимым.

        Кадры извлекаются через `perceptor.process_video_message`, описания —
        через `_describe_video_frame` (vision-модель). При любой ошибке возвращаем
        исходный query без модификации, чтобы не блокировать LLM-flow.
        """
        from ..modules.perceptor import process_video_message  # noqa: PLC0415

        media = (
            getattr(message, "video", None)
            or getattr(message, "video_note", None)
            or getattr(message, "animation", None)
        )
        if not media:
            return query

        max_bytes = int(getattr(config, "VIDEO_DOWNLOAD_MAX_BYTES", 50 * 1024 * 1024))
        file_size = int(getattr(media, "file_size", 0) or 0)
        if file_size and file_size > max_bytes:
            logger.info(
                "video_skipped_too_large",
                chat_id=chat_id,
                file_size=file_size,
                max_bytes=max_bytes,
            )
            return query

        notice = "🎞 *Смотрю видео...*"
        try:
            if is_self:
                await self._safe_edit(message, f"🦀 {query or '(видео)'}\n\n{notice}")
            elif temp_msg is not None and temp_msg is not message:
                await self._safe_edit(temp_msg, notice)
        except Exception:  # noqa: BLE001
            pass  # статусное сообщение — не критично

        video_dir = Path(getattr(config, "VIDEO_DOWNLOAD_DIR", "/tmp/krab_videos"))
        try:
            video_dir.mkdir(parents=True, exist_ok=True)
        except Exception as exc:  # noqa: BLE001
            logger.warning("video_dir_create_failed", error=str(exc))
            return query

        ts_ms = int(time.time() * 1000)
        msg_id = int(getattr(message, "id", 0) or 0)
        video_path = video_dir / f"vid_{ts_ms}_{msg_id}.bin"

        download_timeout = float(getattr(config, "VIDEO_DOWNLOAD_TIMEOUT_SEC", 60.0))
        try:
            await asyncio.wait_for(
                self.client.download_media(message, file_name=str(video_path)),
                timeout=max(5.0, download_timeout),
            )
        except asyncio.TimeoutError:
            logger.warning("video_download_timeout", chat_id=chat_id)
            return query
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "video_download_failed",
                chat_id=chat_id,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return query

        max_frames = int(getattr(config, "VIDEO_MAX_FRAMES", 3))

        async def _describer(frame: bytes, idx: int) -> str:
            return await self._describe_video_frame(frame, idx, chat_id=chat_id)

        # Session 52 P2.5: audio transcription из video через ffmpeg + Whisper.
        # Env gate KRAB_VIDEO_AUDIO_TRANSCRIBE_ENABLED=1 (default off для safety).
        audio_enabled = os.getenv("KRAB_VIDEO_AUDIO_TRANSCRIBE_ENABLED", "0") == "1"

        async def _audio_transcribe(vpath: str) -> str:
            if not audio_enabled:
                return ""
            return await self._extract_and_transcribe_audio(vpath, chat_id=chat_id)

        try:
            extra_context = await process_video_message(
                str(video_path),
                caption=getattr(message, "caption", None) or "",
                max_frames=max_frames,
                sample_strategy="uniform",
                frame_describer=_describer,
                audio_transcriber=_audio_transcribe if audio_enabled else None,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "video_perceptor_failed",
                chat_id=chat_id,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return query
        finally:
            try:
                if video_path.exists():
                    video_path.unlink()
            except Exception:  # noqa: BLE001
                pass

        if not extra_context:
            return query

        logger.info(
            "video_context_attached",
            chat_id=chat_id,
            context_len=len(extra_context),
            max_frames=max_frames,
        )
        # Feature E: сохраняем vision-summary в archive.db (multi-modal memory).
        # fail-open: ошибки лишь логируются, не ломают LLM-flow.
        try:
            from ..modules.perceptor import save_media_summary_to_archive  # noqa: PLC0415

            media_type = (
                "video"
                if getattr(message, "video", None) is not None
                else "video_note"
                if getattr(message, "video_note", None) is not None
                else "animation"
            )
            save_media_summary_to_archive(
                chat_id,
                getattr(message, "id", 0) or 0,
                media_type,
                extra_context,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "media_summary_archive_failed",
                chat_id=chat_id,
                error=str(exc),
                error_type=type(exc).__name__,
            )
        return f"{extra_context}\n\n{query}".strip() if query else extra_context
