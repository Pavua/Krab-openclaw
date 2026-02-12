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
from io import StringIO

from pyrogram import filters, enums
from pyrogram.types import Message

from .auth import is_owner, is_authorized, get_owner, get_allowed_users

import structlog
logger = structlog.get_logger(__name__)


def register_handlers(app, deps: dict):
    """Регистрирует AI-обработчики."""
    router = deps["router"]
    memory = deps["memory"]
    security = deps["security"]
    agent = deps["agent"]
    rate_limiter = deps["rate_limiter"]
    safe_handler = deps["safe_handler"]

    # --- !think: Reasoning Mode ---
    @app.on_message(filters.command("think", prefixes="!"))
    @safe_handler
    async def think_command(client, message: Message):
        """Reasoning Mode: !think <запрос>"""
        if len(message.command) < 2:
            await message.reply_text(
                "🧠 О чем мне подумать? `!think Как работает квантовый компьютер?`"
            )
            return

        prompt = message.text.split(" ", 1)[1]
        notification = await message.reply_text("🧠 **Размышляю...** (Reasoning Mode)")

        context = memory.get_recent_context(message.chat.id, limit=5)

        response = await router.route_query(
            prompt=prompt,
            task_type="reasoning",
            context=context,
            chat_type=message.chat.type.name.lower(),
            is_owner=is_owner(message)
        )

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

        if len(message.command) < 2:
            await message.reply_text(
                "🧠 Опиши сложную задачу: "
                "`!smart Разработай план переезда в другую страну`"
            )
            return

        prompt = message.text.split(" ", 1)[1]
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
        if len(message.command) < 2:
            await message.reply_text(
                "💻 Опиши задачу: `!code Напиши FastAPI сервер с эндпоинтом /health`"
            )
            return

        prompt = message.text.split(" ", 1)[1]
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
            is_owner=is_owner(message)
        )

        await notification.edit_text(response)

    # --- !learn / !remember: Обучение RAG ---
    @app.on_message(filters.command(["learn", "remember"], prefixes="!"))
    @safe_handler
    async def learn_command(client, message: Message):
        """Обучение: !learn <запрос или файл или ссылка>"""
        browser_agent = deps.get("browser_agent")
        
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
            if not browser_agent:
                await message.reply_text("❌ Browser Agent не инициализирован.")
                return
            
            notif = await message.reply_text(f"🌐 Изучаю ссылку: `{url}`...")
            res = await browser_agent.browse(url)
            
            if "error" in res:
                await notif.edit_text(f"❌ Ошибка браузера: {res['error']}")
                return
            
            doc_id = router.rag.add_document(
                text=res["content"],
                metadata={"source": "web", "url": url, "title": res["title"]},
                category="web"
            )
            await notif.edit_text(f"🧠 **Ссылка изучена!**\nЗаголовок: `{res['title']}`\nID: `{doc_id}`")
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

        @app.on_message(filters.command("clone", prefixes="!"))
    @safe_handler
    async def clone_command(client, message: Message):
        """Persona Cloning: !clone [name] (Owner Only)"""
        if not is_owner(message): return
        
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
        if not is_owner(message):
            logger.warning(
                f"⛔ Unauthorized exec attempt from @{message.from_user.username}"
            )
            return

        if len(message.command) < 2:
            await message.reply_text("🐍 Введи Python код: `!exec print('hello')`")
            return

        code = message.text.split(" ", 1)[1]
        notification = await message.reply_text("🐍 **Выполняю...**")

        # Перехват stdout
        old_stdout = sys.stdout
        sys.stdout = buffer = StringIO()
        try:
            exec(code)  # noqa: S102
            output = buffer.getvalue() or "✅ Выполнено (нет вывода)"
        except Exception as e:
            output = f"❌ {type(e).__name__}: {e}\n{traceback.format_exc()[-500:]}"
        finally:
            sys.stdout = old_stdout

        if len(output) > 4000:
            output = output[:3900] + "\n...[Truncated]..."

        await notification.edit_text(f"🐍 **Результат:**\n\n```\n{output}\n```")

    # --- Авто-ответ (самый последний, ловит все текстовые) ---
    @app.on_message(filters.text & ~filters.me & ~filters.bot)
    @safe_handler
    async def auto_reply_logic(client, message: Message):
        """
        Умный автоответчик.
        Срабатывает если: ЛС / упоминание / белый список.
        """
        if message.text is None:
            return

        sender = message.from_user.username if message.from_user else "Unknown"

        # 1. Проверка через SecurityManager
        role = security.get_user_role(
            sender, message.from_user.id if message.from_user else 0
        )

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
        if message.text:
            text_lower = message.text.lower()
            is_mentioned = (
                "краб" in text_lower or 
                (me.username and f"@{me.username.lower()}" in text_lower)
            )

        # Условие ответа: ЛС ИЛИ ответ на моё ИЛИ упоминание
        should_reply = is_private or is_reply_to_me or is_mentioned

        if not should_reply:
            # В группах просто сохраняем в историю без ответа для контекста (Passive Learning)
            memory.save_message(message.chat.id, {"user": sender, "text": message.text})
            return

        # Проверка авторизации (в группах отвечаем всем если упомянут, но учитываем Stealth)
        if not is_authorized(message) and not is_mentioned:
            logger.info(f"⛔ Ignored unauthorized message from @{sender}")
            return

        # Антиспам: игнорируем слишком короткие сообщения в группах
        if not is_private and len(message.text) < 3 and not is_reply_to_me:
            return

        # Rate Limiting
        user_id = message.from_user.id if message.from_user else 0
        if not rate_limiter.is_allowed(user_id):
            logger.warning(f"🚫 Rate limited: @{sender} ({user_id})")
            return

        # 2. Синхронизируем историю (если новый чат)
        synced = await memory.sync_telegram_history(client, message.chat.id, limit=30)
        if synced:
            logger.info(f"📜 History synced for chat {message.chat.id}")

        summarizer = deps.get("summarizer")
        
        # 3. Сохраняем текущее сообщение
        memory.save_message(message.chat.id, {"user": sender, "text": message.text})
        
        # Запускаем суммаризацию в фоне (не блокируя ответ)
        if summarizer:
            asyncio.create_task(summarizer.auto_summarize(message.chat.id))

        # 4. Маршрутизация с учетом контекста и прав
        context = memory.get_recent_context(message.chat.id, limit=12)

        await client.send_chat_action(message.chat.id, action=enums.ChatAction.TYPING)

        chat_type_str = message.chat.type.name.lower()
        owner_flag = is_owner(message)

        # Создаем сообщение-заглушку
        reply_msg = await message.reply_text("🤔 **Размышляю...**")
        
        last_update = time.time()
        full_response = ""
        
        try:
            async for part in router.route_query_stream(
                prompt=message.text,
                task_type="chat",
                context=context,
                chat_type=chat_type_str,
                is_owner=owner_flag
            ):
                full_response = part
                curr_t = time.time()
                # Обновляем не чаще чем раз в 1.5 сек, чтобы не поймать FloodWait
                if curr_t - last_update > 1.5:
                    try:
                        # Добавляем курсор
                        await reply_msg.edit_text(full_response + " ▌")
                        last_update = curr_t
                    except Exception:
                        pass # Игнорируем ошибки редактирования (например, FloodWait или тот же текст)

            # Финальный штрих без курсора
            if full_response:
                await reply_msg.edit_text(full_response)
            else:
                await reply_msg.edit_text("❌ Извини, не удалось сформулировать ответ.")
        except Exception as e:
            logger.error(f"Auto-reply stream failed: {e}")
            await reply_msg.edit_text(f"❌ Произошла ошибка при генерации: {e}")
            full_response = f"Error: {e}"

        # 6. Сохраняем ответ
        memory.save_message(
            message.chat.id, {"role": "assistant", "text": full_response}
        )
