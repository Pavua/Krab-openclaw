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

import structlog
logger = structlog.get_logger(__name__)

async def _process_auto_reply(client, message: Message, deps: dict):
    """
    Умный автоответчик v2 (Omni-channel).
    Вынесен из register_handlers для тестируемости.
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
    # В ЛС больше НЕ требуем explicit authorization (кроме заблокированных)
    role = security.get_user_role(sender, message.from_user.id if message.from_user else 0)
    
    if role == "blocked":
            logger.info(f"⛔ Blocked user {sender} tried to interact.")
            return

    if role == "stealth_restricted":
        logger.info(f"🕶️ Stealth Mode: Ignored message from @{sender}")
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
    text_content = message.text or message.caption or ""
    
    if text_content:
        text_lower = text_content.lower()
        is_mentioned = (
            "краб" in text_lower or 
            (me.username and f"@{me.username.lower()}" in text_lower)
        )

    # Config: Allow group replies without mention?
    allow_group_replies = True
    if config_manager:
        allow_group_replies = config_manager.get("group_chat.allow_replies", True)

    # Условие ответа:
    # 1. ЛС (Private) -> Всегда отвечаем (если не заблокирован)
    # 2. Группы -> Если упомянут ИЛИ (ответ на сообщение бота И разрешено в конфиге) ИЛИ (авторизован - owner/admin)
    should_reply = False
    if is_private:
        should_reply = True
    elif is_mentioned:
        should_reply = True
    elif is_reply_to_me and allow_group_replies:
        should_reply = True

    if not should_reply:
        # В группах просто сохраняем в историю без ответа для контекста (Passive Learning)
        logger.debug(f"🤫 Message from @{sender} in {message.chat.type} ignored (no mention/reply).")
        memory.save_message(message.chat.id, {"user": sender, "text": text_content})
        return

    # Антиспам: игнорируем слишком короткие текстовые сообщения в группах, если это не реплай и не медиа
    if not is_private and len(text_content) < 2 and not is_reply_to_me and not message.photo and not message.voice:
        logger.debug(f"🔇 Anti-spam: Ignored too short message from @{sender}")
        return

    # Rate Limiting
    user_id = message.from_user.id if message.from_user else 0
    if not rate_limiter.is_allowed(user_id):
        logger.warning(f"🚫 Rate limited: @{sender} ({user_id})")
        return

    await client.send_chat_action(message.chat.id, action=enums.ChatAction.TYPING)

    # 2. Обработка мультимедиа (Vision / Voice)
    visual_context = ""
    transcribed_text = ""
    is_voice_response_needed = False
    temp_files = []

    try:
        # --- PHOTO (Vision) ---
        if message.photo:
            if not perceptor:
                await message.reply_text("❌ Vision module (Perceptor) недоступен.")
                return

            await client.send_chat_action(message.chat.id, action=enums.ChatAction.UPLOAD_PHOTO)
            notif = await message.reply_text("👁️ **Смотрю...**")
            
            # Скачиваем фото (in-memory or temp file)
            # Pyrogram method download() returns path
            photo_path = await message.download()
            temp_files.append(photo_path)
            
            # Анализируем через Perceptor (Gemini Vision)
            await notif.edit_text("🧠 **Анализирую изображение через Vision Engine...**")
            vision_result = await perceptor.analyze_image(photo_path, router, prompt="Опиши это изображение подробно на русском языке.")
            
            if vision_result and not vision_result.startswith("Ошибка"):
                visual_context = f"[VISION ANALYSIS]: User sent a photo. Description: {vision_result}"
                await notif.edit_text("📝 **Формирую ответ...**")
                await asyncio.sleep(0.5) # Маленькая пауза для плавности
                await notif.delete()
            else:
                await notif.edit_text(f"❌ Не удалось распознать изображение: {vision_result}")
                visual_context = "[VISION ERROR]: Failed to analyze photo."

        # --- VOICE (STT) ---
        elif message.voice:
            if not perceptor:
                await message.reply_text("❌ Voice module (Perceptor) недоступен.")
                return

            await client.send_chat_action(message.chat.id, action=enums.ChatAction.RECORD_AUDIO)
            notif = await message.reply_text("👂 **Слушаю...**")
            
            voice_path = await message.download()
            temp_files.append(voice_path)
            
            # Транскрибация (Whisper via Perceptor)
            transcribed_text = await perceptor.transcribe(voice_path, router)
            
            if transcribed_text and not transcribed_text.startswith("Ошибка"):
                is_voice_response_needed = True # Reply with voice if spoken to
                await notif.delete()
            else:
                await notif.edit_text("❌ Не удалось распознать речь.")
                return

    except Exception as e:
        logger.error(f"Media processing error: {e}")
        await message.reply_text(f"⚠️ Ошибка обработки медиа: {e}")
    finally:
        # Cleanup temp files
        for p in temp_files:
            try:
                if os.path.exists(p): os.remove(p)
            except: pass

    # Формируем итоговый промпт
    final_prompt = text_content
    if transcribed_text:
            final_prompt = f"{transcribed_text} (Voice Input)"
    
    if visual_context:
        final_prompt = f"{visual_context}\n\nUser Says: {final_prompt}"

    # 3. Синхронизируем историю
    synced = await memory.sync_telegram_history(client, message.chat.id, limit=30)
    
    # 4. Сохраняем текущее (обогащенное) сообщение
    memory.save_message(message.chat.id, {"user": sender, "text": final_prompt})
    
    if summarizer:
        asyncio.create_task(summarizer.auto_summarize(message.chat.id))

    # 5. Маршрутизация
    context = memory.get_recent_context(message.chat.id, limit=12)
    
    reply_msg = await message.reply_text("🤔 **Думаю...**")
    
    full_response = ""
    last_update = 0
    
    async def run_streaming():
        nonlocal full_response, last_update
        async for part in router.route_query_stream(
            prompt=final_prompt,
            task_type="chat",
            context=context,
            chat_type=message.chat.type.name.lower(),
            is_owner=is_owner(message)
        ):
            full_response = part
            curr_t = time.time()
            if curr_t - last_update > 1.5:
                try:
                    # Используем message.chat.id для редактирования
                    await reply_msg.edit_text(full_response + " ▌")
                    last_update = curr_t
                except Exception: pass

    try:
        # Защитный таймаут 300 секунд
        await asyncio.wait_for(run_streaming(), timeout=300)
    except asyncio.TimeoutError:
        logger.error("Auto-reply timeout reached (300s)")
        await reply_msg.edit_text("⏳ Превышено время ожидания ответа (300с). Попробуй еще раз.")
        full_response = "Error: Timeout"
    except Exception as e:
        logger.error(f"Auto-reply stream failed: {e}")
        await reply_msg.edit_text(f"❌ Ошибка: {e}")
        full_response = f"Error: {e}"

    if full_response:
        # Логируем размер и время (последнее берется из router логов обычно)
        logger.info(f"Final AI response ready. Length: {len(full_response)} chars.")
        
        # Убираем лишние теги для текста в Telegram, если они там остались
        clean_display_text = full_response
        
        # Разделение на части по 4000 символов (лимит Telegram ~4096)
        MAX_LEN = 4000
        if len(clean_display_text) > MAX_LEN:
            chunks = [clean_display_text[i:i+MAX_LEN] for i in range(0, len(clean_display_text), MAX_LEN)]
            await reply_msg.edit_text(chunks[0])
            for chunk in chunks[1:]:
                await message.reply_text(chunk)
        else:
            await reply_msg.edit_text(clean_display_text)
        
        # --- TTS Response (Voice Mode) ---
        if is_voice_response_needed and perceptor:
            # Фильтруем технические отказы и ошибки, чтобы не озвучивать "Извини, я не могу..."
            error_keywords = ["извини", "не могу", "ошибка", "error", "failed", "не удалось"]
            clean_lower = full_response.lower()
            is_error_response = any(kw in clean_lower for kw in error_keywords) and len(full_response) < 100

            if not is_error_response:
                await client.send_chat_action(message.chat.id, action=enums.ChatAction.RECORD_AUDIO)
                # Generate speech
                tts_file = await perceptor.speak(full_response)
                
                if tts_file and os.path.exists(tts_file):
                    await message.reply_voice(tts_file, caption="🗣️ **AI Voice Reply**")
                    # Clean up TTS file
                    try:
                        os.remove(tts_file)
                    except: pass
            else:
                logger.info("🚫 TTS skipped: response looks like an error or refusal.")
    else:
        await reply_msg.edit_text("❌ Извини, не удалось сформулировать ответ.")

    # 6. Сохраняем ответ
    memory.save_message(
        message.chat.id, {"role": "assistant", "text": full_response}
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

        notification = await message.reply_text("🧠 **Размышляю...** (Reasoning Mode)")

        context = memory.get_recent_context(message.chat.id, limit=5)

        try:
            response = await asyncio.wait_for(
                router.route_query(
                    prompt=prompt,
                    task_type="reasoning",
                    context=context,
                    chat_type=message.chat.type.name.lower(),
                    is_owner=is_owner(message),
                    confirm_expensive=confirm_expensive,
                ),
                timeout=180 # Для reasoning даем больше времени
            )
            await notification.edit_text(response)
        except asyncio.TimeoutError:
            response = "⏳ Размышление заняло слишком много времени (более 3 мин). Попробуй упростить запрос."
            await notification.edit_text(response)
        except Exception as e:
            response = f"❌ Ошибка размышления: {e}"
            await notification.edit_text(response)

        await notification.edit_text(response)
        memory.save_message(message.chat.id, {"role": "assistant", "text": response})

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
        """Генерация изображения: !img <описание>"""
        if not is_authorized(message): return
        
        prompt = " ".join(message.command[1:])
        if not prompt:
            await message.reply_text("❌ Введи описание картинки: `!img котик в космосе`")
            return
            
        notification = await message.reply_text("🎨 **Генерирую шедевр...** (Imagen 3)")
        
        image_gen = deps.get("image_gen")
        if not image_gen:
             await notification.edit_text("❌ Ошибка: Image Manager не инициализирован.")
             return

        image_path = await image_gen.generate(prompt)
        
        if image_path and os.path.exists(image_path):
            await notification.delete()
            await message.reply_photo(
                photo=image_path,
                caption=f"🎨 **Запрос:** `{prompt}`\nEngine: `Imagen 3 / Cloud`"
            )
            os.remove(image_path)
        else:
            await notification.edit_text("❌ Не удалось сгенерировать изображение. Попробуй позже.")

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

        await notification.edit_text(f"🐍 **Результат:**\n\n```\n{output}\n```")
        await _danger_audit(message, "exec", "ok", code[:300])

    # --- Авто-ответ (самый последний, ловит все текстовые/фото/голосовые) ---
    @app.on_message((filters.text | filters.photo | filters.voice) & ~filters.me & ~filters.bot)
    @safe_handler
    async def auto_reply_logic(client, message: Message):
        """
        Умный автоответчик v2 (Omni-channel).
        Делегирует исполнение в _process_auto_reply.
        """
        await _process_auto_reply(client, message, deps)
