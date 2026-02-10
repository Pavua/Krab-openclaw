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
            is_private=message.chat.type == enums.ChatType.PRIVATE,
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
            is_private=message.chat.type == enums.ChatType.PRIVATE,
        )

        await notification.edit_text(response)

    # --- !learn: Обучение RAG ---
    @app.on_message(filters.command("learn", prefixes="!"))
    @safe_handler
    async def learn_command(client, message: Message):
        """Обучение: !learn <факт или информация>"""
        if len(message.command) < 2:
            await message.reply_text("🧠 Чему научить? `!learn Python был создан Гвидо ван Россумом в 1991`")
            return

        fact = message.text.split(" ", 1)[1]

        # Добавляем в RAG
        doc_id = router.rag.add_document(
            text=fact,
            metadata={
                "source": "user_learn",
                "user": message.from_user.username if message.from_user else "unknown",
                "chat_id": str(message.chat.id),
            },
            category="learned",
        )

        await message.reply_text(
            f"🧠 **Запомнил!** (RAG ID: `{doc_id}`)\n\n_{fact[:200]}_"
        )

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

        # Проверка авторизации
        if not is_authorized(message):
            logger.info(f"⛔ Ignored unauthorized message from @{sender}")
            return

        # Rate Limiting
        user_id = message.from_user.id if message.from_user else 0
        if not rate_limiter.is_allowed(user_id):
            logger.warning(f"🚫 Rate limited: @{sender} ({user_id})")
            return

        # 2. Сохраняем контекст
        memory.save_message(message.chat.id, {"user": sender, "text": message.text})

        # 3. Маршрутизация
        context = memory.get_recent_context(message.chat.id, limit=10)

        await client.send_chat_action(message.chat.id, action=enums.ChatAction.TYPING)

        response_text = await router.route_query(
            prompt=message.text,
            task_type="chat",
            context=context,
            is_private=message.chat.type == enums.ChatType.PRIVATE,
        )

        # 4. Отвечаем
        await message.reply_text(response_text)

        # 5. Сохраняем ответ
        memory.save_message(
            message.chat.id, {"role": "assistant", "text": response_text}
        )
