# -*- coding: utf-8 -*-
"""
Media Handler — Обработчики мультимедиа: аудио, фото, видео, документы.

Извлечён из main.py (строки ~1316-1537). Отвечает за:
- Голосовые сообщения → STT (Perceptor) → авто-ответ
- Фото → Vision Analysis → RAG
- Видео / кружки → Gemini Video Analysis → RAG
- Документы (PDF, DOCX, Excel, etc.) → парсинг → RAG
"""

import os
from datetime import datetime

from pyrogram import filters, enums
from pyrogram.types import Message

from .auth import is_owner, is_authorized
import structlog
logger = structlog.get_logger(__name__)


def register_handlers(app, deps: dict):
    """Регистрирует медиа-обработчики."""
    router = deps["router"]
    memory = deps["memory"]
    perceptor = deps["perceptor"]
    safe_handler = deps["safe_handler"]
    black_box = deps.get("black_box")

    # --- Обработка документов (PDF, DOCX, Excel, etc.) ---
    @app.on_message(filters.document)
    @safe_handler
    async def handle_document(client, message: Message):
        """
        Автоматический парсинг документов.
        Поддерживает: PDF, DOCX, XLSX, CSV, TXT, JSON, Markdown, Python, etc.
        """
        is_private = message.chat.type == enums.ChatType.PRIVATE
        has_trigger = message.caption and (
            "!read" in message.caption
            or "!doc" in message.caption
            or "!parse" in message.caption
        )

        if not (is_private or has_trigger):
            return

        filename = message.document.file_name or "unknown"

        try:
            from src.utils.doc_parser import DocumentParser

            if not DocumentParser.is_supported(filename):
                return

            notification = await message.reply_text(
                f"📄 **Читаю документ:** `{filename}`..."
            )

            file_path = await message.download(
                file_name=f"artifacts/downloads/{filename}"
            )

            # Deep Analysis (Vision/Native) for PDFs if there is a question
            is_pdf = filename.lower().endswith(".pdf")
            if is_pdf and message.caption and not message.caption.startswith("!"):
                await notification.edit_text(f"🧠 **Deep Analysis (PDF Native):** `{filename}`...")
                response = await perceptor.analyze_document(file_path, router, message.caption)
                await message.reply_text(response)
                memory.save_message(
                    message.chat.id, {"role": "assistant", "text": response}
                )
                # Keep RAG indexing
                if router.rag:
                    doc_id = router.rag.add_document(
                        text=f"[Document: {filename}]\n{response}",
                        metadata={
                           "filename": filename,
                           "type": "pdf_native_analysis",
                           "chat_id": str(message.chat.id),
                           "timestamp": str(datetime.now()),
                        }
                    )
                    await notification.edit_text(f"📄 **Документ проанализирован через Vision Engine.**\nПроиндексирован: `{doc_id}`")
                else:
                    await notification.edit_text(f"📄 **Документ проанализирован через Vision Engine.**")
            else:
                # Стандартный парсинг для RAG
                text, metadata = await DocumentParser.parse(file_path)

                if text.startswith("⚠️") or text.startswith("❌"):
                    await notification.edit_text(text)
                else:
                    if router.rag:
                        doc_id = router.rag.add_document(
                            text=f"[Document: {filename}]\n{text}",
                            metadata={
                                **metadata,
                                "chat_id": str(message.chat.id),
                                "timestamp": str(datetime.now()),
                            },
                            category="document",
                        )
                        rag_info = f"🧠 Проиндексирован в RAG: `{doc_id}`\n"
                    else:
                        rag_info = ""

                    preview = text[:500] + "..." if len(text) > 500 else text
                    result_text = (
                        f"📄 **Документ проанализирован:** `{filename}`\n"
                        f"📊 Размер: {metadata.get('size_kb', '?')} KB | "
                        f"Символов: {metadata.get('chars_extracted', '?')}\n"
                        f"{rag_info}\n"
                        f"**Превью:**\n```\n{preview}\n```"
                    )

                    await notification.edit_text(result_text)

                    # Если в caption есть вопрос — отвечаем на него (для не-PDF или когда Vision не сработал)
                    if message.caption and not message.caption.startswith("!"):
                        context = memory.get_recent_context(message.chat.id, limit=5)
                        response = await router.route_query(
                            prompt=f"[Документ '{filename}']: {text[:5000]}\n\nВопрос пользователя: {message.caption}",
                            task_type="chat",
                            context=context,
                            chat_type=message.chat.type.name.lower(),
                            is_owner=is_owner(message)
                        )
                        await message.reply_text(response)
                        memory.save_message(
                            message.chat.id, {"role": "assistant", "text": response}
                        )

            # Чистим скачанный файл
            if os.path.exists(file_path):
                os.remove(file_path)

        except ImportError:
            pass  # Нет док. парсера — тихо пропускаем
        except Exception as e:
            logger.error(f"Document parsing error: {e}")

    # --- Обработка видео и кружков ---
    @app.on_message(filters.video | filters.video_note)
    @safe_handler
    async def handle_video(client, message: Message):
        """Анализ видео-контента (включая кружки) через Gemini."""
        is_private = message.chat.type == enums.ChatType.PRIVATE
        has_trigger = message.caption and (
            "!scan" in message.caption or "!video" in message.caption
        )

        if not (is_private or has_trigger):
            return

        notification = await message.reply_text("🎞️ **Смотрю видео (кружок)...**")

        try:
            media = message.video or message.video_note
            file_path = await message.download(
                file_name=f"artifacts/downloads/{media.file_unique_id}.mp4"
            )

            prompt = "Опиши подробно, что происходит на видео."
            if message.caption:
                prompt += f" Обрати внимание на: {message.caption}"

            analysis = await perceptor.analyze_video(file_path, router, prompt)

            if router.rag:
                router.rag.add_document(
                    text=f"[Video Analysis]: {analysis}",
                    metadata={
                        "source": "video",
                        "chat": str(message.chat.id),
                        "timestamp": str(datetime.now()),
                    },
                    category="vision",
                )

            await notification.edit_text(f"🎞️ **Анализ видео:**\n\n{analysis}")

            if os.path.exists(file_path):
                os.remove(file_path)

        except Exception as e:
            logger.error(f"Video handling error: {e}")
            await notification.edit_text(f"❌ Ошибка анализа видео: {e}")

    # --- Обработка голосовых / аудио ---
    @app.on_message(filters.voice | filters.audio | filters.document)
    @safe_handler
    async def handle_audio(client, message: Message):
        """Обработка голосовых сообщений через Perceptor (STT → AI ответ)."""
        # Проверяем, аудио ли это (фильтр document ловит все)
        is_audio = message.voice or message.audio or (
            message.document and message.document.mime_type
            and "audio" in message.document.mime_type
        )

        if not is_audio:
            return

        media = message.voice or message.audio or message.document
        if not media:
            return

        is_private = message.chat.type == enums.ChatType.PRIVATE
        if not (is_private or (message.caption and "!txt" in message.caption)):
            return

        logger.info(f"Processing audio from {message.chat.id}")

        file_path = await message.download(
            file_name=f"artifacts/downloads/{media.file_unique_id}.ogg"
        )

        notification = await message.reply_text("👂 Слушаю...")

        if not file_path or not os.path.exists(file_path):
            await notification.edit_text("❌ Ошибка скачивания файла.")
            return

        text = await perceptor.transcribe(file_path, router)

        memory.save_message(
            message.chat.id, {"role": "audio_transcript", "content": text}
        )

        await notification.edit_text(f"**Transcript:** `{text}`\n\n🤔 Думаю...")

        # Запрашиваем ответ AI
        from src.core.prompts import AUDIO_TRANSCRIPTION_PROMPT

        context = memory.get_recent_context(message.chat.id, limit=5)
        
        # Явный формат промпта, чтобы модель не искала поле audio_transcript
        voice_prompt = (
            f"{AUDIO_TRANSCRIPTION_PROMPT}\n\n"
            f"📥 **Входящее голосовое сообщение (Транскрипция):**\n"
            f"\"\"\"\n{text}\n\"\"\"\n\n"
            f"📝 **Инструкция:** Сфокусируйся на содержимом сообщения выше."
        )

        try:
            response_text = await asyncio.wait_for(
                router.route_query(
                    prompt=voice_prompt,
                    task_type="chat",
                    context=context,
                    chat_type=message.chat.type.name.lower(),
                    is_owner=is_owner(message)
                ),
                timeout=90
            )
        except asyncio.TimeoutError:
            response_text = "⏳ Голосовой ответ занял слишком много времени."
        except Exception as e:
            response_text = f"❌ Ошибка обработки голоса: {e}"

        await message.reply_text(response_text)
        memory.save_message(
            message.chat.id, {"role": "assistant", "text": response_text}
        )

        await notification.edit_text(f"**Transcript:**\n\n{text}")

        # AUTO-SUMMARY (Block C)
        settings = black_box.get_group_settings(message.chat.id) if black_box else {}
        if settings.get("call_auto_summary", 0):
            summary_prompt = (
                f"Сделай очень краткое резюме (1-2 предложения) следующего текста голосового сообщения:\n\n"
                f"\"{text}\""
            )
            summary = await router.route_query(
                prompt=summary_prompt,
                task_type="chat",
                context=[],
                chat_type=message.chat.type.name.lower(),
                is_owner=is_owner(message)
            )
            await message.reply_text(f"📝 **Auto-Summary:**\n{summary}")

        os.remove(file_path)

    # --- Обработка фото (Vision) ---
    @app.on_message(filters.photo)
    async def handle_vision(client, message: Message):
        """Обработка изображений (включая HEIC)."""
        is_private = message.chat.type == enums.ChatType.PRIVATE
        should_scan = (
            message.caption
            and ("!scan" in message.caption or "!vision" in message.caption)
        ) or is_private

        if not should_scan:
            return

        notification = await message.reply_text("👁️ Смотрю...")
        file_path = await message.download(
            file_name=f"artifacts/downloads/{message.photo.file_unique_id}"
        )

        await notification.edit_text("🧠 **Анализирую изображение через Vision Engine...**")
        description = await perceptor.analyze_image(
            file_path, router, prompt="Что на изображении? Опиши подробно."
        )
        memory.save_message(
            message.chat.id, {"role": "vision_analysis", "content": description}
        )

        # Индексируем в RAG (если доступен)
        if router.rag:
            router.rag.add_document(
                text=f"[Vision Scan]: {description}",
                metadata={
                    "source": "vision",
                    "chat": str(message.chat.id),
                    "timestamp": str(datetime.now()),
                },
            )

        await notification.edit_text(
            f"👁️ **Vision:** `{description}`\n\n🤔 Думаю..."
        )

        context = memory.get_recent_context(message.chat.id, limit=5)
        vision_prompt = f"[Пользователь прислал фото]: {description}. Прокомментируй или ответь на вопрос."
        if message.caption:
            vision_prompt += f"\nПодпись: {message.caption}"

        try:
            response_text = await asyncio.wait_for(
                router.route_query(
                    prompt=vision_prompt,
                    task_type="chat",
                    context=context,
                    chat_type=message.chat.type.name.lower(),
                    is_owner=is_owner(message)
                ),
                timeout=90
            )
        except asyncio.TimeoutError:
            response_text = "⏳ Анализ изображения занял слишком много времени."
        except Exception as e:
            response_text = f"❌ Ошибка анализа зрения: {e}"

        await message.reply_text(response_text)
        memory.save_message(
            message.chat.id, {"role": "assistant", "text": response_text}
        )

        await notification.edit_text(f"**Vision Analysis:**\n\n{description}")
        os.remove(file_path)
