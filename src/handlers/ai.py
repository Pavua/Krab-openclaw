# -*- coding: utf-8 -*-
"""
AI Handler — Обработчики команд, связанных с AI: авто-ответ, reasoning, агентный цикл.

Извлечён из main.py. Включает:
- auto_reply_logic: умный автоответчик на входящие текстовые сообщения
- !think: Reasoning Mode (глубокое размышление)
- !smart: Agent Workflow (автономное решение задач)
- !code: генерация кода
- !learn: обучение RAG
- !exec: Python REPL (Owner only)
"""

import os
import sys
import time
import asyncio
import traceback
import shlex
from io import StringIO

from pyrogram import filters, enums
from pyrogram.types import Message

from .auth import is_owner, is_authorized, is_superuser
from ..core.markdown_sanitizer import sanitize_markdown_for_telegram, strip_backticks_from_content

import structlog
logger = structlog.get_logger(__name__)

def _timeout_from_env(name: str, default_value: int) -> int:
    """Возвращает таймаут из env с безопасным fallback."""
    raw = os.getenv(name, str(default_value)).strip()
    try:
        parsed = int(raw)
        return parsed if parsed > 0 else default_value
    except Exception:
        return default_value


AUTO_REPLY_TIMEOUT_SECONDS = _timeout_from_env("AUTO_REPLY_TIMEOUT_SECONDS", 900)
THINK_TIMEOUT_SECONDS = _timeout_from_env("THINK_TIMEOUT_SECONDS", 420)


def _sanitize_model_output(text: str, router=None) -> str:
    """Удаляет служебные маркеры модели перед отправкой в Telegram."""
    if hasattr(router, "_sanitize_model_text"):
        try:
            return router._sanitize_model_text(text)
        except Exception:
            pass
    if not text:
        return ""
    
    import re
    cleaned = str(text)
    # Удаляем всё в формате <|...|>
    cleaned = re.sub(r"<\|.*?\|>", "", cleaned)
    # Удаляем классические токены
    for token in ("</s>", "<s>", "<br>"):
        cleaned = cleaned.replace(token, "")
    return cleaned.strip()


def _is_voice_reply_requested(text: str) -> bool:
    """Определяет, просит ли пользователь голосовой ответ текстом."""
    lowered = (text or "").strip().lower()
    if not lowered:
        return False
    triggers = (
        "ответь голосом",
        "голосом ответь",
        "скажи голосом",
        "озвучь",
        "voice reply",
        "reply by voice",
        "respond with voice",
        "расскажи",
        "сказку",
        "спой",
        "поговори со мной",
    )
    return any(token in lowered for token in triggers)


def _message_content_hint(msg: Message) -> str:
    """Возвращает короткий текстовый дескриптор любого типа сообщения."""
    text = _sanitize_model_output(msg.text or msg.caption or "")
    if text:
        return text
    if msg.voice:
        return "[VOICE] Голосовое сообщение"
    if msg.audio:
        title = ""
        if msg.audio and getattr(msg.audio, "title", None):
            title = f" ({msg.audio.title})"
        return f"[AUDIO] Аудио{title}"
    if msg.sticker:
        emoji = getattr(msg.sticker, "emoji", "") or ""
        return f"[STICKER] {emoji}".strip()
    if msg.animation:
        return "[GIF] Анимация"
    if msg.video:
        return "[VIDEO] Видео"
    if msg.photo:
        return "[PHOTO] Изображение"
    if msg.document:
        name = getattr(msg.document, "file_name", "") or ""
        return f"[DOCUMENT] {name}".strip()
    if msg.poll:
        question = getattr(msg.poll, "question", "") or ""
        return f"[POLL] {question}".strip()
    media_type = getattr(getattr(msg, "media", None), "value", "")
    if media_type:
        return f"[{str(media_type).upper()}] Медиа-сообщение"
    return ""


async def set_message_reaction(client, chat_id: int, message_id: int, emoji: str):
    """Ставит реакцию (emoji) на сообщение."""
    try:
        # В Pyrogram v2+ send_reaction принимает emoji как строку
        await client.send_reaction(chat_id, message_id, emoji)
    except Exception as e:
        logger.debug(f"Reaction failed: {e}")


async def _process_auto_reply(client, message: Message, deps: dict):
    """
    Умный автоответчик v3 (Omni-channel + Reactions + Multimodal).
    """
    security = deps["security"]
    rate_limiter = deps["rate_limiter"]
    memory = deps["memory"]
    router = deps["router"]
    config_manager = deps.get("config_manager")
    perceptor = deps.get("perceptor")
    summarizer = deps.get("summarizer")
    
    sender = message.from_user.username if message.from_user else "Unknown"

    # 1. Проверка через SecurityManager
    role = security.get_user_role(sender, message.from_user.id if message.from_user else 0)
    
    if role == "blocked":
            return

    if role == "stealth_restricted":
        return

    # 2. Логика срабатывания (Smart Reply v2.0)
    is_private = message.chat.type == enums.ChatType.PRIVATE
    is_reply_to_me = (
        message.reply_to_message and 
        message.reply_to_message.from_user and 
        message.reply_to_message.from_user.is_self
    )
    
    me = await client.get_me()
    is_mentioned = False
    text_content = _message_content_hint(message)
    
    if text_content:
        text_lower = text_content.lower()
        is_mentioned = (
            "краб" in text_lower or 
            (me.username and f"@{me.username.lower()}" in text_lower)
        )

    allow_group_replies = True
    if config_manager:
        allow_group_replies = config_manager.get("group_chat.allow_replies", True)

    should_reply = False
    if is_private:
        should_reply = True
    elif is_mentioned:
        should_reply = True
    elif is_reply_to_me and allow_group_replies:
        should_reply = True

    if not should_reply:
        memory.save_message(message.chat.id, {"user": sender, "text": text_content})
        return

    # Антиспам
    has_rich_media = bool(
        message.photo or message.voice or message.audio or 
        message.sticker or message.animation or message.video or message.document
    )
    if not is_private and len(text_content) < 2 and not is_reply_to_me and not has_rich_media:
        return

    # Rate Limiting
    user_id = message.from_user.id if message.from_user else 0
    if not rate_limiter.is_allowed(user_id):
        return

    # 2. Обработка мультимедиа (Vision / Voice / Video / Docs / Stickers)
    visual_context = ""
    transcribed_text = ""
    is_voice_response_needed = _is_voice_reply_requested(text_content)
    temp_files = []

    try:
        # --- STICKER ---
        if message.sticker:
            emoji = message.sticker.emoji or "🎨"
            visual_context = f"[USER SENT A STICKER: {emoji}]"
            # Для стикеров можно сразу поставить реакцию "глаза" или "сердце"
            await set_message_reaction(client, message.chat.id, message.id, "👀")

        # --- PHOTO (Vision) ---
        elif message.photo:
            if not perceptor:
                await message.reply_text("❌ Vision module недоступен.")
                return
            await client.send_chat_action(message.chat.id, action=enums.ChatAction.UPLOAD_PHOTO)
            photo_path = await message.download()
            temp_files.append(photo_path)
            vision_result = await perceptor.analyze_image(photo_path, router, prompt="Опиши это изображение подробно на русском языке.")
            vision_result = _sanitize_model_output(vision_result or "", router)
            if vision_result and not vision_result.startswith("Ошибка"):
                visual_context = f"[VISION ANALYSIS]: User sent a photo. Description: {vision_result}"
            else:
                visual_context = "[VISION ERROR]: Failed to analyze photo."

        # --- VOICE / AUDIO (STT) ---
        elif message.voice or message.audio:
            if not perceptor:
                await message.reply_text("❌ Voice module недоступен.")
                return
            await client.send_chat_action(message.chat.id, action=enums.ChatAction.RECORD_AUDIO)
            audio_path = await message.download()
            temp_files.append(audio_path)
            transcribed_text = _sanitize_model_output(await perceptor.transcribe(audio_path, router), router)
            if transcribed_text and not transcribed_text.startswith("Ошибка"):
                if message.voice:
                    is_voice_response_needed = True
            else:
                return

        # --- VIDEO / GIF (Deep Analysis) ---
        elif message.video or message.animation:
            if not perceptor:
                await message.reply_text("❌ Vision module недоступен.")
                return
            await client.send_chat_action(message.chat.id, action=enums.ChatAction.UPLOAD_VIDEO)
            notif = await message.reply_text("🎬 **Смотрю...**")
            media_path = await message.download()
            temp_files.append(media_path)
            # Для GIF/Video используем Gemini Video Analysis
            video_result = _sanitize_model_output(
                await perceptor.analyze_video(
                    media_path,
                    router,
                    prompt="Опиши очень кратко (1-2 предложения), что происходит на видео/гифке. Какой основной посыл или эмоция?",
                ),
                router,
            )
            if video_result and not video_result.startswith("Ошибка"):
                visual_context = f"[MEDIA ANALYSIS]: {video_result}"
                await notif.delete()
            else:
                await notif.edit_text(f"❌ Ошибка анализа: {video_result}")
                visual_context = "[MEDIA ERROR]: Failed to analyze video/gif."

        # --- DOCUMENT ---
        elif message.document:
            if not perceptor:
                await message.reply_text("❌ Document module недоступен.")
                return
            await client.send_chat_action(message.chat.id, action=enums.ChatAction.UPLOAD_DOCUMENT)
            notif = await message.reply_text("📄 **Читаю...**")
            doc_path = await message.download()
            temp_files.append(doc_path)
            doc_result = _sanitize_model_output(
                await perceptor.analyze_document(
                    doc_path,
                    router,
                    prompt="Сделай краткий обзор документа на русском.",
                ),
                router,
            )
            if doc_result and not doc_result.startswith("Ошибка"):
                visual_context = f"[DOCUMENT ANALYSIS]: {doc_result}"
                await notif.delete()
            else:
                await notif.edit_text(f"❌ Ошибка: {doc_result}")
                visual_context = "[DOCUMENT ERROR]: Failed to analyze document."

    except Exception as e:
        logger.error(f"Media processing error: {e}")
    finally:
        for p in temp_files:
            try:
                if os.path.exists(p): os.remove(p)
            except: pass

    # Context gathering
    reply_context = ""
    if message.reply_to_message:
        reply_author = "Unknown"
        if message.reply_to_message.from_user:
            reply_author = f"@{message.reply_to_message.from_user.username}" if message.reply_to_message.from_user.username else (message.reply_to_message.from_user.first_name or "User")
        reply_text = _message_content_hint(message.reply_to_message)
        if reply_text:
            reply_context = f"[REPLY CONTEXT from {reply_author}]: {reply_text}"

    # Final prompt
    final_prompt = text_content
    if transcribed_text:
        final_prompt = f"{transcribed_text} (Voice Input)"
    if visual_context:
        final_prompt = f"{visual_context}\n\nUser Says: {final_prompt}"
    if reply_context:
        final_prompt = f"{reply_context}\n\n{final_prompt}"

    # Sync & Save
    await memory.sync_telegram_history(client, message.chat.id, limit=30)
    memory.save_message(message.chat.id, {"user": sender, "text": final_prompt})
    
    if summarizer:
        asyncio.create_task(summarizer.auto_summarize(message.chat.id))

    # Routing
    context = memory.get_token_aware_context(message.chat.id, max_tokens=3000)
    
    # Typing indicator
    await client.send_chat_action(message.chat.id, action=enums.ChatAction.TYPING)
    reply_msg = await message.reply_text("🤔 **Думаю...**")
    
    full_response = ""
    last_update = 0
    
    async def run_streaming():
        nonlocal full_response, last_update
        try:
            async for part in router.route_stream(
                prompt=final_prompt,
                task_type="chat",
                context=context,
                chat_type=message.chat.type.name.lower(),
                is_owner=is_owner(message)
            ):
                full_response += part
                curr_t = time.time()
                # Плавное обновление (раз в 1.8 сек)
                if curr_t - last_update > 1.8:
                    try:
                        # Закрываем незакрытые блоки кода, чтобы Pyrogram не ругался
                        safe_text = sanitize_markdown_for_telegram(full_response + " ▌")
                        await reply_msg.edit_text(safe_text)
                        last_update = curr_t
                    except Exception: pass
        except Exception as e:
            logger.error(f"Streaming error occurred: {e}")
            # Если у нас уже есть какой-то текст, мы не пробрасываем ошибку дальше,
            # чтобы пользователь получил хотя бы часть ответа.
            if not full_response:
                raise e
            else:
                 full_response += f"\n\n⚠️ [Стрим прерван: {e}]"

    try:
        await asyncio.wait_for(run_streaming(), timeout=AUTO_REPLY_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        logger.warning(f"Timeout reaching model for chat {message.chat.id}")
        if not full_response:
             await reply_msg.edit_text("⌛ **Время ожидания истекло.** Попробуйте еще раз.")
             return
    except Exception as e:
        logger.error(f"Auto-reply critical failure: {e}")
        if not full_response:
            await reply_msg.edit_text(f"❌ Ошибка: {e}")
            return

    if full_response:
        clean_display_text = _sanitize_model_output(full_response, router)
        
        # Интеллектуальная реакция: если ответ начинается с эмодзи, ставим его как реакцию
        import re
        emoji_match = re.match(r"^([\U00010000-\U0010ffff])", clean_display_text)
        if emoji_match:
            await set_message_reaction(client, message.chat.id, message.id, emoji_match.group(1))
        
        # Отправка ответа
        MAX_LEN = 4000
        if len(clean_display_text) > MAX_LEN:
            chunks = [clean_display_text[i:i+MAX_LEN] for i in range(0, len(clean_display_text), MAX_LEN)]
            await reply_msg.edit_text(chunks[0])
            for chunk in chunks[1:]:
                await message.reply_text(chunk)
        else:
            await reply_msg.edit_text(clean_display_text)
        
        # TTS Implementation
        if is_voice_response_needed and perceptor:
            error_keywords = ["извини", "не могу", "ошибка", "не удалось"]
            if not any(kw in clean_display_text[:100].lower() for kw in error_keywords):
                logger.info(f"🎤 Requesting TTS for chat {message.chat.id}")
                await client.send_chat_action(message.chat.id, action=enums.ChatAction.RECORD_AUDIO)
                
                try:
                    tts_file = await perceptor.speak(clean_display_text)
                    if tts_file and os.path.exists(tts_file):
                        await message.reply_voice(tts_file, caption="🗣️ **Voice Reply**")
                        logger.info(f"✅ Voice reply sent to {message.chat.id}")
                        try: os.remove(tts_file)
                        except: pass
                    else:
                        logger.warning(f"⚠️ TTS failed to generate file for {message.chat.id}")
                        await message.reply_text("🗣️ *[Ошибка озвучки: не удалось сгенерировать аудио]*")
                except Exception as tts_exc:
                    logger.error(f"❌ TTS Error in ai.py: {tts_exc}")
                    await message.reply_text(f"🗣️ *[Ошибка TTS: {str(tts_exc)[:100]}]*")
            else:
                logger.info("🔇 Skipping TTS for error message/refusal.")
    else:
        await reply_msg.edit_text("❌ Пустой ответ.")

    # Save Assistant Message
    memory.save_message(
        message.chat.id, {"role": "assistant", "text": _sanitize_model_output(full_response, router)}
    )



def register_handlers(app, deps: dict):
    """Регистрирует AI-обработчики."""
    router = deps["router"]
    memory = deps["memory"]
    security = deps["security"]
    agent = deps["agent"]
    rate_limiter = deps["rate_limiter"]
    safe_handler = deps["safe_handler"]

    def _extract_prompt_and_confirm_flag(message_text: str) -> tuple[str, bool]:
        """
        Разбирает команду и выделяет:
        - пользовательский prompt,
        - флаг подтверждения дорогого прогона (`--confirm-expensive` / `--confirm` / `confirm`).
        """
        raw = message_text or ""
        try:
            argv = shlex.split(raw)
        except ValueError:
            argv = raw.split()

        if len(argv) < 2:
            return "", False

        confirm_expensive = False
        payload_tokens: list[str] = []
        for token in argv[1:]:
            normalized = token.strip().lower()
            if normalized in {"--confirm-expensive", "--confirm", "confirm"}:
                confirm_expensive = True
                continue
            payload_tokens.append(token)

        prompt = " ".join(payload_tokens).strip()
        return prompt, confirm_expensive

    async def _danger_audit(message: Message, action: str, status: str, details: str = ""):
        """Логирует опасные действия в Saved Messages и владельцу для аудита."""
        sender = message.from_user.username if message.from_user else "unknown"
        chat_title = message.chat.title or "private"
        chat_id = message.chat.id
        payload = (
            f"🛡️ **Danger Audit**\n"
            f"- action: `{action}`\n"
            f"- status: `{status}`\n"
            f"- sender: `@{sender}`\n"
            f"- chat: `{chat_title}` (`{chat_id}`)\n"
        )
        if details:
            payload += f"- details: `{details[:800]}`\n"
        try:
            await app.send_message("me", payload)
        except Exception:
            pass
        try:
            await app.send_message("@p0lrd", payload)
        except Exception:
            pass

    # --- !think: Reasoning Mode ---
    @app.on_message(filters.command("think", prefixes="!"))
    @safe_handler
    async def think_command(client, message: Message):
        """Reasoning Mode: !think <запрос>"""
        prompt, confirm_expensive = _extract_prompt_and_confirm_flag(message.text or "")
        if not prompt:
            await message.reply_text(
                "🧠 О чем мне подумать? `!think Как работает квантовый компьютер?`\n"
                "Для критичных задач: добавь `--confirm-expensive`."
            )
            return

        # notification = await message.reply_text("🧠 **Размышляю...** (Reasoning Mode)") # Убираем лишнее

        context = memory.get_token_aware_context(message.chat.id, max_tokens=10000)

        full_response = ""
        last_update = 0
        
        reply_msg = await message.reply_text("🤔 **Размышляю...**")

        try:
            async for chunk in router.route_stream(
                prompt=prompt, # Changed from 'query' to 'prompt'
                task_type="reasoning",
                context=context,
                chat_type=message.chat.type.name.lower(),
                is_owner=is_owner(message),
                confirm_expensive=confirm_expensive, # Added confirm_expensive
            ):
                full_response += chunk
                curr_t = time.time()
                if curr_t - last_update > 2.0:
                    try:
                        # Закрываем незакрытые блоки кода при стриминге reasoning
                        safe_text = sanitize_markdown_for_telegram(full_response + " ▌")
                        await reply_msg.edit_text(safe_text)
                        last_update = curr_t
                    except Exception: pass
            
            await reply_msg.edit_text(_sanitize_model_output(full_response, router)) # Sanitize here
        except asyncio.TimeoutError: # Moved timeout handling here
            full_response = (
                f"⏳ Размышление заняло слишком много времени (>{THINK_TIMEOUT_SECONDS}с). "
                "Попробуй упростить запрос."
            )
        memory.save_message(message.chat.id, {"role": "assistant", "text": _sanitize_model_output(full_response, router)})

    # --- !smart: Агентный цикл (Phase 6) ---
    @app.on_message(filters.command("smart", prefixes="!"))
    @safe_handler
    async def smart_command(client, message: Message):
        """Agent Workflow: !smart <задача>"""
        if not security.can_execute_command(
            message.from_user.username, message.from_user.id, "user"
        ):
            return

        prompt, confirm_expensive = _extract_prompt_and_confirm_flag(message.text or "")
        if not prompt:
            await message.reply_text(
                "🧠 Опиши сложную задачу: "
                "`!smart Разработай план переезда в другую страну`"
            )
            return

        # Confirm-step для потенциально дорогих критичных сценариев.
        require_confirm = bool(getattr(router, "require_confirm_expensive", False))
        profile = (
            router.classify_task_profile(prompt, "reasoning")
            if hasattr(router, "classify_task_profile")
            else "chat"
        )
        is_critical = profile in {"security", "infra", "review"}
        if require_confirm and is_critical and not confirm_expensive:
            await message.reply_text(
                "⚠️ Для критичной задачи нужен confirm-step.\n"
                "Повтори с `!smart --confirm-expensive <задача>`."
            )
            return

        notification = await message.reply_text("🕵️ **Agent:** Инициализирую воркфлоу...")

        result = await agent.solve_complex_task(prompt, message.chat.id)

        await notification.edit_text(result)
        memory.save_message(message.chat.id, {"role": "assistant", "text": result})

    @app.on_message(filters.command("bg", prefixes="!"))
    @safe_handler
    async def bg_command(client, message: Message):
        """Background Task: !bg <задача>"""
        if not is_authorized(message): return

        if len(message.command) < 2:
            await message.reply_text("⏳ Опиши фоновую задачу: `!bg проведи глубокое исследование по X`")
            return

        prompt = message.text.split(" ", 1)[1]
        task_queue = deps["task_queue"]
        
        # Создаем корутину для выполнения
        coro = agent.solve_complex_task(prompt, message.chat.id)
        
        task_id = await task_queue.enqueue(f"Agent solve: {prompt[:30]}", message.chat.id, coro)
        
        await message.reply_text(f"🚀 Задача запущена в фоне!\nID: `{task_id}`\nЯ пришлю уведомление, когда закончу.")

    # --- !swarm: Swarm Intelligence (Phase 10) ---
    @app.on_message(filters.command("swarm", prefixes="!"))
    @safe_handler
    async def swarm_command(client, message: Message):
        """Swarm Intelligence: !swarm <запрос>"""
        if not is_authorized(message): return
        
        if len(message.command) < 2:
            await message.reply_text("🐝 Опиши задачу для Роя: `!swarm проанализируй рынок и поищи новости`")
            return

        query = message.text.split(" ", 1)[1]
        notification = await message.reply_text("🐝 **Swarm Intelligence:** Активация агентов...")

        tools = deps["tools"]
        # Вызываем автономное решение (включая консилиум если есть триггер)
        result = await tools.swarm.autonomous_decision(query)
        
        if result is None:
             # Fallback на обычный ответ если рой не знает что делать
             result = await router.route_query(
                 prompt=query, 
                 task_type='chat',
                 chat_type=message.chat.type.name.lower(),
                 is_owner=is_owner(message)
             )

        await notification.edit_text(result)
        memory.save_message(message.chat.id, {"role": "assistant", "text": result})

    # --- !code: Генерация кода ---
    @app.on_message(filters.command("code", prefixes="!"))
    @safe_handler
    async def code_command(client, message: Message):
        """Генерация кода: !code <описание>"""
        prompt, confirm_expensive = _extract_prompt_and_confirm_flag(message.text or "")
        if not prompt:
            await message.reply_text(
                "💻 Опиши задачу: `!code Напиши FastAPI сервер с эндпоинтом /health`"
            )
            return

        notification = await message.reply_text("💻 **Генерирую код...**")

        code_prompt = (
            f"Напиши код по запросу: {prompt}\n\n"
            "Формат: только код с комментариями, без лишних объяснений. "
            "Язык программирования — определи из контекста."
        )

        response = await router.route_query(
            prompt=code_prompt,
            task_type="coding",
            chat_type=message.chat.type.name.lower(),
            is_owner=is_owner(message),
            confirm_expensive=confirm_expensive,
        )

        await notification.edit_text(response)

    # --- !learn / !remember: Обучение RAG ---
    @app.on_message(filters.command(["learn", "remember"], prefixes="!"))
    @safe_handler
    async def learn_command(client, message: Message):
        """Обучение: !learn <запрос или файл или ссылка>"""
        browser_agent = deps.get("browser_agent")
        openclaw = deps.get("openclaw_client")
        
        # 1. Если есть файл
        if message.document:
            file_name = message.document.file_name.lower()
            if not (file_name.endswith(('.txt', '.pdf', '.md'))):
                await message.reply_text("❌ Поддерживаются только .txt, .pdf и .md")
                return
            
            notif = await message.reply_text(f"📄 Читаю файл `{file_name}`...")
            path = await message.download()
            
            content = ""
            if file_name.endswith('.pdf'):
                try:
                    import PyPDF2
                    with open(path, 'rb') as f:
                        reader = PyPDF2.PdfReader(f)
                        content = "\n".join([page.extract_text() for page in reader.pages])
                except Exception as e:
                    content = f"Error reading PDF: {e}"
            else:
                with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
            
            os.remove(path)
            
            if len(content) < 10:
                await notif.edit_text("❌ Файл пуст или не читается.")
                return
            
            doc_id = router.rag.add_document(
                text=content,
                metadata={"source": "file", "filename": file_name},
                category="document"
            )
            await notif.edit_text(f"🧠 **Файл изучен!**\nID: `{doc_id}`\nСимволов: {len(content)}")
            return

        # 2. Если есть ссылка
        if len(message.command) > 1 and message.command[1].startswith('http'):
            url = message.command[1]
            notif = await message.reply_text(f"🌐 Изучаю ссылку: `{url}`...")
            content_text = ""
            title = url

            # OpenClaw-first: web_fetch, локальный браузер только fallback.
            if openclaw:
                fetched = await openclaw.invoke_tool("web_fetch", {"url": url})
                if not fetched.get("error"):
                    try:
                        content_text = fetched.get("content", [{}])[0].get("text", "")[:20000]
                        title = fetched.get("details", {}).get("title", title)
                    except Exception:
                        content_text = ""

            if not content_text and browser_agent:
                res = await browser_agent.browse(url)
                if "error" not in res:
                    content_text = res.get("content", "")
                    title = res.get("title", title)

            if not content_text:
                await notif.edit_text("❌ Не удалось получить содержимое страницы.")
                return

            doc_id = router.rag.add_document(
                text=content_text,
                metadata={"source": "web", "url": url, "title": title},
                category="web"
            )
            await notif.edit_text(f"🧠 **Ссылка изучена!**\nЗаголовок: `{title}`\nID: `{doc_id}`")
            return

        # 3. Обычный текст
        if len(message.command) < 2:
            await message.reply_text("🧠 Чему научить? `!learn Python был создан Гвидо ван Россумом` или отправь файл/ссылку.")
            return

        fact = message.text.split(" ", 1)[1]
        doc_id = router.rag.add_document(
            text=fact,
            metadata={
                "source": "user_learn",
                "user": message.from_user.username if message.from_user else "unknown",
                "chat_id": str(message.chat.id),
            },
            category="learning",
        )
        await message.reply_text(f"🧠 **Сохранено в память.** ID: `{doc_id}`")

    @app.on_message(filters.command("clone", prefixes="!"))
    @safe_handler
    async def clone_command(client, message: Message):
        """Persona Cloning: !clone [name] (Owner Only)"""
        if not is_owner(message):
            return
        
        name = message.command[1] if len(message.command) > 1 else "Digital Twin"
        notif = await message.reply_text(f"👯 **Инициализирую клонирование личности `{name}`...**")
        
        # 1. Сбор данных из RAG (сообщения пользователя)
        await notif.edit_text("🔎 **Шаг 1/3:** Собираю образцы твоего стиля из памяти...")
        query = f"сообщения от @{message.from_user.username}"
        samples = router.rag.query(query, n_results=15, category="learning")
        
        if not samples or len(samples) < 50:
            # Fallback: пробуем искать в общей категории
            samples = router.rag.query(query, n_results=15)

        if not samples or len(samples) < 50:
             await notif.edit_text("❌ **Ошибка:** Недостаточно данных в памяти для анализа стиля. Пообщайся со мной побольше!")
             return

        # 2. Анализ стиля через LLM
        await notif.edit_text("📊 **Шаг 2/3:** Анализирую паттерны речи и лингвистический профиль...")
        analysis_prompt = (
            f"Проанализируй стиль общения пользователя на основе этих примеров:\n\n{samples}\n\n"
            "Твоя задача: Составить краткий 'System Prompt' (на русском), который позволит другой LLM "
            f"имитировать этого пользователя. Назови его '{name}'. "
            "Учти: тональность, любимые слова, использование эмодзи, длину предложений, уровень формальности. "
            "Ответь ТОЛЬКО текстом промпта, начинающимся с 'Ты — цифровой двойник...'"
        )
        
        custom_prompt = await router.route_query(
            prompt=analysis_prompt,
            task_type="chat",
            is_owner=True
        )

        # 3. Регистрация личности
        await notif.edit_text("💾 **Шаг 3/3:** Сохраняю новую личность в ядро...")
        persona_manager = deps["persona_manager"]
        pid = f"clone_{name.lower().replace(' ', '_')}"
        persona_manager.add_custom_persona(
            pid=pid,
            name=f"Клон: {name}",
            prompt=custom_prompt,
            desc=f"Цифровой двойник, созданный на основе анализа @{message.from_user.username}"
        )
        
        await notif.edit_text(
            f"✅ **Клонирование завершено!**\n\n"
            f"🆔 ID: `{pid}`\n"
            f"🎭 Имя: `Клон: {name}`\n\n"
            f"Чтобы активировать, введи: `!persona set {pid}`"
        )

    # --- !rag: Статистика и поиск по базе знаний ---
    @app.on_message(filters.command(["rag", "search"], prefixes="!"))
    @safe_handler
    async def rag_command(client, message: Message):
        """Инфо и поиск по RAG: !rag [запрос]"""
        if len(message.command) < 2:
            report = router.rag.format_stats_report()
            await message.reply_text(report)
            return

        query = message.text.split(" ", 1)[1]
        results = router.rag.query_with_scores(query, n_results=3)
        
        if not results:
            await message.reply_text("🔎 Ничего не найдено.")
            return
        
        resp = f"🔎 **Результаты поиска по запросу: `{query}`**\n\n"
        for i, res in enumerate(results, 1):
            expired = "⚠️ (Устарело)" if res['expired'] else ""
            resp += f"{i}. [{res['category']}] Score: {res['score']} {expired}\n"
            resp += f"_{res['text'][:200]}..._\n\n"
        
        await message.reply_text(resp)

    # --- !forget: Очистить историю чата ---
    @app.on_message(filters.command("forget", prefixes="!"))
    @safe_handler
    async def forget_command(client, message: Message):
        """Очистка истории текущего чата."""
        if not is_authorized(message): return
        
        memory.clear_history(message.chat.id)
        await message.reply_text("🧹 **Память чата очищена.**")

    # --- !img / !draw: Генерация изображений ---
    @app.on_message(filters.command(["img", "draw"], prefixes="!"))
    @safe_handler
    async def img_command(client, message: Message):
        """Генерация изображения: !img <описание> (local/cloud + выбор модели)."""
        if not is_authorized(message): return

        image_gen = deps.get("image_gen")
        if not image_gen:
            await message.reply_text("❌ Ошибка: Image Manager не инициализирован.")
            return

        try:
            tokens = shlex.split(message.text or "")
        except ValueError:
            tokens = (message.text or "").split()

        args = tokens[1:] if len(tokens) > 1 else []
        if not args:
            await message.reply_text(
                "🎨 Использование:\n"
                "`!img <промпт>`\n"
                "`!img --model <alias> <промпт>`\n"
                "`!img --local <промпт>` или `!img --cloud <промпт>`\n"
                "`!img models` — список генераторов\n"
                "`!img cost [alias]` — ориентировочная стоимость"
            )
            return

        head = args[0].strip().lower()
        if head in {"models", "list"}:
            if not hasattr(image_gen, "list_models"):
                await message.reply_text("⚠️ В этой версии image manager нет каталога моделей.")
                return
            rows = await image_gen.list_models()
            lines = ["**🎨 Image Models:**", ""]
            for row in rows:
                icon = "🟢" if row.get("available") else "🔴"
                cost = row.get("cost_per_image_usd")
                cost_text = f"~${cost}/img" if cost is not None else "n/a"
                reason = f" ({row.get('reason')})" if row.get("reason") else ""
                lines.append(
                    f"{icon} `{row.get('alias')}` — {row.get('title')} | {row.get('channel')}/{row.get('provider')} | {cost_text}{reason}"
                )
            lines.append("\n_Выбор модели:_ `!img --model <alias> <промпт>`")
            await message.reply_text("\n".join(lines))
            return

        if head == "cost":
            if not hasattr(image_gen, "estimate_cost"):
                await message.reply_text("⚠️ В этой версии image manager нет калькулятора стоимости.")
                return
            if len(args) >= 2:
                aliases = [args[1]]
            else:
                aliases = list(getattr(image_gen, "model_specs", {}).keys())
            lines = ["**💸 Image Cost (ориентировочно):**", ""]
            for alias in aliases:
                info = image_gen.estimate_cost(alias, images=1)
                if not info.get("ok"):
                    lines.append(f"- `{alias}`: ❌ {info.get('error')}")
                    continue
                unit = info.get("unit_cost_usd")
                if unit is None:
                    lines.append(f"- `{alias}`: n/a")
                else:
                    lines.append(f"- `{alias}`: ~`${unit}` за изображение")
            await message.reply_text("\n".join(lines))
            return

        model_alias = None
        prefer_local = None
        aspect_ratio = "1:1"
        prompt_tokens: list[str] = []
        idx = 0
        while idx < len(args):
            token = args[idx]
            lowered = token.strip().lower()
            if lowered in {"--model", "-m"} and idx + 1 < len(args):
                model_alias = args[idx + 1].strip()
                idx += 2
                continue
            if lowered == "--local":
                prefer_local = True
                idx += 1
                continue
            if lowered == "--cloud":
                prefer_local = False
                idx += 1
                continue
            if lowered in {"--ar", "--aspect"} and idx + 1 < len(args):
                aspect_ratio = args[idx + 1].strip()
                idx += 2
                continue
            prompt_tokens.append(token)
            idx += 1

        prompt = " ".join(prompt_tokens).strip()
        if not prompt:
            await message.reply_text("❌ Введи описание картинки: `!img котик в космосе`")
            return

        notification = await message.reply_text("🎨 **Генерирую изображение...**")

        if hasattr(image_gen, "generate_with_meta"):
            result = await image_gen.generate_with_meta(
                prompt=prompt,
                aspect_ratio=aspect_ratio,
                model_alias=model_alias,
                prefer_local=prefer_local,
            )
            image_path = result.get("path")
        else:
            result = {"ok": False, "error": "legacy_image_manager"}
            image_path = await image_gen.generate(prompt, aspect_ratio=aspect_ratio)
            if image_path:
                result = {
                    "ok": True,
                    "path": image_path,
                    "model_alias": model_alias or "legacy",
                    "channel": "cloud",
                    "provider": "legacy",
                    "model_id": "legacy",
                    "cost_estimate_usd": None,
                }

        if result.get("ok") and image_path and os.path.exists(image_path):
            await notification.delete()
            cost = result.get("cost_estimate_usd")
            cost_text = f"~`${cost}`" if cost is not None else "n/a"
            caption = (
                f"🎨 **Запрос:** `{prompt}`\\n"
                f"Model: `{result.get('model_alias', '-')}`\\n"
                f"Channel: `{result.get('channel', '-')}` | Provider: `{result.get('provider', '-')}`\\n"
                f"Cost est.: {cost_text}"
            )
            await message.reply_photo(photo=image_path, caption=caption)
            os.remove(image_path)
            return

        details = result.get("details")
        details_text = f"\n{details}" if details else ""
        await notification.edit_text(
            "❌ Не удалось сгенерировать изображение.\\n"
            f"Причина: `{result.get('error', 'unknown')}`{details_text}\\n"
            "_Проверь `!img models` и настройки ключей/workflow._"
        )

    # --- !exec: Python REPL (Owner only, опасная команда) ---
    @app.on_message(filters.command("exec", prefixes="!"))
    @safe_handler
    async def exec_command(client, message: Message):
        """Python REPL: !exec <code> (Owner Only)"""
        if not is_superuser(message):
            logger.warning(
                f"⛔ Unauthorized exec attempt from @{message.from_user.username}"
            )
            return

        if message.chat.type != enums.ChatType.PRIVATE:
            await message.reply_text("⛔ `!exec` разрешен только в личных сообщениях.")
            await _danger_audit(message, "exec", "blocked", "non-private-chat")
            return

        if len(message.command) < 2:
            await message.reply_text("🐍 Введи Python код: `!exec print('hello')`")
            return

        code = message.text.split(" ", 1)[1]
        notification = await message.reply_text("🐍 **Выполняю...**")

        # Перехват stdout
        old_stdout = sys.stdout
        sys.stdout = buffer = StringIO()
        # Контент для REPL (пробрасываем внутренности для отладки)
        exec_globals = {
            "client": client,
            "ctx": client,
            "message": message,
            "msg": message,
            "deps": deps,
            "router": router,
            "mr": router,
            "lms": router,
            "sys": sys,
            "os": os,
            "asyncio": asyncio,
            "logger": logger,
            "traceback": traceback,
        }
        
        try:
            exec(code, exec_globals)  # noqa: S102
            output = buffer.getvalue() or "✅ Выполнено (нет вывода)"
        except Exception as e:
            output = f"❌ {type(e).__name__}: {e}\n{traceback.format_exc()[-500:]}"
        finally:
            sys.stdout = old_stdout

        if len(output) > 4000:
            output = output[:3900] + "\n...[Truncated]..."

        # Очищаем вывод от вложенных бэктиков, которые ломают markdown
        safe_output = strip_backticks_from_content(output)
        await notification.edit_text(f"🐍 **Результат:**\n\n```\n{safe_output}\n```")
        await _danger_audit(message, "exec", "ok", code[:300])

    # --- Авто-ответ (самый последний, ловит текст + медиа) ---
    @app.on_message(
        (
            filters.text
            | filters.photo
            | filters.voice
            | filters.audio
            | filters.sticker
            | filters.animation
            | filters.video
            | filters.document
        )
        & ~filters.me
        & ~filters.bot
    )
    @safe_handler
    async def auto_reply_logic(client, message: Message):
        """
        Умный автоответчик v2 (Omni-channel).
        Делегирует исполнение в _process_auto_reply.
        """
        await _process_auto_reply(client, message, deps)
