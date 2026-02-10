# -*- coding: utf-8 -*-
"""
Tools Handler — Инструменты: поиск, новости, перевод, TTS.

Извлечён из main.py. Включает:
- !scout: Deep Research (Web Search)
- !nexus: Extended research report
- !news: Дайджест новостей
- !translate: Перевод RU↔EN
- !say / !voice: TTS
"""

from pyrogram import filters, enums
from pyrogram.types import Message

from .auth import is_authorized

import structlog
logger = structlog.get_logger(__name__)


def register_handlers(app, deps: dict):
    """Регистрирует обработчики инструментов."""
    router = deps["router"]
    scout = deps["scout"]
    safe_handler = deps["safe_handler"]

    # --- !scout: Deep Research ---
    @app.on_message(filters.command("scout", prefixes="!"))
    @safe_handler
    async def scout_command(client, message: Message):
        """Deep Research: !scout <тема>"""
        if len(message.command) < 2:
            await message.reply_text(
                "🔎 Что исследовать? `!scout Квантовые вычисления 2025`"
            )
            return

        query = message.text.split(" ", 1)[1]
        notification = await message.reply_text(
            f"🔎 **Исследую:** `{query}`..."
        )

        search_results = await scout.search(query)
        if not search_results:
            await notification.edit_text("❌ Ничего не найдено.")
            return

        formatted = scout.format_results(search_results)
        await notification.edit_text("🧠 **Анализирую результаты...**")

        prompt = (
            f"Проведи глубокий анализ темы '{query}' на основе этих данных:\n\n"
            f"{formatted}\n\nСделай структурированный отчёт."
        )
        analysis = await router.route_query(
            prompt,
            task_type="reasoning",
            is_private=message.chat.type == enums.ChatType.PRIVATE,
        )

        await notification.edit_text(
            f"🔎 **Deep Research: {query}**\n\n{analysis}"
        )

    # --- !nexus: Extended Research ---
    @app.on_message(filters.command("nexus", prefixes="!"))
    @safe_handler
    async def nexus_command(client, message: Message):
        """Nexus Intelligence Report: !nexus <тема>"""
        if len(message.command) < 2:
            await message.reply_text(
                "🕵️ Что исследовать? `!nexus Криптовалюты и регуляция 2025`"
            )
            return

        query = message.text.split(" ", 1)[1]
        notification = await message.reply_text("🕵️‍♂️ **Nexus Intelligence: сканирую...**")

        search_results = await scout.search(query, max_results=10)
        if search_results:
            formatted = scout.format_results(search_results)
        else:
            formatted = "Нет данных из веб-поиска."

        prompt = (
            f"Составь обширный аналитический INTELLIGENCE REPORT по теме: {query}\n\n"
            f"Источники:\n{formatted}\n\n"
            "Включи: ключевые факты, тренды, риски, прогнозы."
        )

        report = await router.route_query(
            prompt,
            task_type="reasoning",
            is_private=message.chat.type == enums.ChatType.PRIVATE,
        )

        final_text = f"🕵️‍♂️ **Nexus Intelligence Report: {query}**\n\n{report}"
        await notification.edit_text(final_text)

    # --- !news: Дайджест новостей ---
    @app.on_message(filters.command("news", prefixes="!"))
    @safe_handler
    async def news_command(client, message: Message):
        """Fresh News: !news <запрос>"""
        query = (
            "Криптовалюты"
            if len(message.command) < 2
            else message.text.split(" ", 1)[1]
        )
        notification = await message.reply_text(
            f"🗞️ Ищу свежие новости по теме `{query}`..."
        )

        news_results = await scout.search_news(query)
        if not news_results:
            await notification.edit_text("❌ Не удалось найти свежих новостей.")
            return

        formatted_news = scout.format_results(news_results)
        await notification.edit_text("🧠 **Саммари новостей...**")

        prompt = (
            f"Составь краткий дайджест самых свежих новостей по теме '{query}' "
            f"на основе этих данных:\n\n{formatted_news}\n\nБудь краток."
        )
        summary = await router.route_query(
            prompt,
            task_type="chat",
            is_private=message.chat.type == enums.ChatType.PRIVATE,
        )

        await notification.edit_text(
            f"🗞️ **Fresh News Digest: {query}**\n\n{summary}"
        )

    # --- !translate: Перевод ---
    @app.on_message(filters.command("translate", prefixes="!"))
    @safe_handler
    async def translate_command(client, message: Message):
        """Перевод текста: !translate <текст>"""
        if len(message.command) < 2:
            await message.reply_text(
                "🌐 Введи текст для перевода: `!translate Hello world`"
            )
            return

        text = message.text.split(" ", 1)[1]
        notification = await message.reply_text("🌐 **Перевожу...**")

        # Определяем направление: RU→EN или EN→RU
        prompt = (
            f"Переведи следующий текст. Если текст на русском — переведи на английский, "
            f"если на другом языке — переведи на русский.\n\nТекст: {text}"
        )

        translated = await router.route_query(prompt, task_type="chat")
        await notification.edit_text(f"🌐 **Перевод:**\n\n{translated}")

    # --- !say / !voice: TTS ---
    @app.on_message(filters.command(["say", "voice"], prefixes="!"))
    @safe_handler
    async def say_command(client, message: Message):
        """Text-to-Speech: !say <текст>"""
        if len(message.command) < 2:
            await message.reply_text("🗣️ Что сказать? `!say Привет, мир!`")
            return

        text = message.text.split(" ", 1)[1]
        notification = await message.reply_text("🔊 **Генерирую голос...**")

        try:
            from src.modules.perceptor import Perceptor

            perceptor = deps["perceptor"]
            audio_path = await perceptor.text_to_speech(text)

            if audio_path:
                await message.reply_voice(audio_path)
                await notification.edit_text("🔊 **Готово!**")
                import os
                if os.path.exists(audio_path):
                    os.remove(audio_path)
            else:
                await notification.edit_text("❌ TTS недоступен.")

        except Exception as e:
            logger.error(f"TTS error: {e}")
            await notification.edit_text(f"❌ Ошибка TTS: {e}")
