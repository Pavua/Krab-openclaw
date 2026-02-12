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
    # scout = deps["scout"]  # Deprecated
    safe_handler = deps["safe_handler"]
    openclaw = deps["openclaw_client"]

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
        # Единая логика для Deep Research / Nexus Intelligence
        await _process_research_task(
            client=client,
            message=message,
            openclaw=openclaw,
            query=query,
            mode="scout"
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

        # Единая логика для Deep Research / Nexus Intelligence
        await _process_research_task(
            client=client,
            message=message,
            openclaw=openclaw,
            query=query,
            mode="nexus"
        )

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

        # Use OpenClaw for news search (via web_search tool)
        logger.info(f"News Search via OpenClaw: {query}")
        
        try:
            # 1. Search recent news
            search_results = await openclaw.invoke_tool("web_search", {
                "query": f"news about {query}", 
                "count": 5,
                "freshness": "pd" # Past Day (Brave specific, might need check if supported by OpenClaw wrapper)
            })
            
            results_data = search_results.get("details", {}).get("results", [])
            # Fallback parsing if needed (same as in execute_agent_task)
            if not results_data and "content" in search_results:
                 try:
                     import json
                     text = search_results["content"][0]["text"]
                     parsed = json.loads(text)
                     results_data = parsed.get("results", [])
                 except:
                     pass

            if not results_data:
                await notification.edit_text("❌ Не удалось найти свежих новостей через OpenClaw.")
                return

            formatted_news = ""
            for i, res in enumerate(results_data, 1):
                if isinstance(res, dict):
                    title = res.get('title', 'No Title').replace("<<<EXTERNAL_UNTRUSTED_CONTENT>>>", "").strip()
                    url = res.get('url', '#')
                    date = res.get('published', 'Unknown date')
                    formatted_news += f"{i}. [{title}]({url}) ({date})\n"
                else:
                    formatted_news += f"{i}. {str(res)}\n"
            
            await notification.edit_text("🧠 **Анализирую новости...**")

            prompt = (
                f"Составь краткий дайджест самых свежих новостей по теме '{query}' "
                f"на основе этих заголовков:\n\n{formatted_news}\n\n"
                "Выдели главное. Используй Markdown."
            )
            
            # Use OpenClaw LLM for summary too, to be consistent? 
            # Or keep local Router? The user wants to replace local AI.
            # Let's use OpenClaw Chat Completions.
            messages = [{"role": "user", "content": prompt}]
            summary = await openclaw.chat_completions(messages)

            await notification.edit_text(
                f"🗞️ **Fresh News Digest: {query}**\n\n{summary}"
            )
            
        except Exception as e:
            logger.error(f"News command error: {e}")
            await notification.edit_text(f"❌ Ошибка: {e}")

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

    # --- !browse: Browser Automation (Phase 9.2) ---
    @app.on_message(filters.command("browse", prefixes="!"))
    @safe_handler
    async def browse_command(client, message: Message):
        """Browser: !browse <url>"""
        browser_agent = deps.get("browser_agent")
        
        if not browser_agent:
            await message.reply_text("❌ Browser Agent не инициализирован. Убедитесь, что установлен playwright.")
            return

        if len(message.command) < 2:
            await message.reply_text("🌐 Какой URL открыть? Пример: `!browse https://example.com`")
            return
            
        url = message.text.split(" ", 1)[1]
        notification = await message.reply_text(f"🌐 **Навигация:** `{url}`...")
        
        try:
            result = await browser_agent.browse(url)
            
            if "error" in result:
                await notification.edit_text(f"❌ Ошибка загрузки: {result['error']}")
                return
            
            # Отправка скриншота
            screenshot_path = result.get("screenshot_path")
            if screenshot_path:
                await message.reply_photo(
                    photo=screenshot_path,
                    caption=f"📄 **{result['title']}**\n🔗 `{result['url']}`"
                )
                
            # Отправка контента (первые 3000 символов)
            content_snippet = result.get("content", "")[:3000]
            if len(result.get("content", "")) > 3000:
                content_snippet += "\n... [далее обрезано]"
                
            await notification.edit_text(
                f"📄 **Content Preview:**\n\n```text\n{content_snippet}\n```"
            )
            
        except Exception as e:
            logger.error(f"Browse command error: {e}")
            await notification.edit_text(f"❌ Критическая ошибка браузера: {e}")

    # --- !screenshot: Web Screenshot ---
    @app.on_message(filters.command("screenshot", prefixes="!"))
    @safe_handler
    async def screenshot_command(client, message: Message):
        """Screenshot: !screenshot <url>"""
        browser_agent = deps.get("browser_agent")

        if not browser_agent:
            await message.reply_text("❌ Browser Agent не инициализирован.")
            return

        if len(message.command) < 2:
            await message.reply_text("📸 Какой URL снять? Пример: `!screenshot https://google.com`")
            return

        url = message.text.split(" ", 1)[1]
        notification = await message.reply_text(f"📸 **Снимаю страницу:** `{url}`...")

        try:
            path = await browser_agent.screenshot_only(url)
            
            if path and path.endswith(".png"):
                await message.reply_photo(photo=path, caption=f"📸 Screenshot: {url}")
                await notification.delete()
            else:
                await notification.edit_text(f"❌ Не удалось сделать скриншот.")
        except Exception as e:
            logger.error(f"Screenshot error: {e}")
            await notification.edit_text(f"❌ Ошибка: {e}")
    # --- Helper Functions ---
    async def _process_research_task(client, message, openclaw, query: str, mode: str = "scout"):
        """
        Delegates research task to OpenClaw Engine.
        """
        icon = "🔎" if mode == "scout" else "🕵️‍♂️"
        title = "OpenClaw Scout" if mode == "scout" else "Nexus Intelligence"
        
        notification = await message.reply_text(
            f"{icon} **{title}: Transmitting to Engine...** `{query}`"
        )

        try:
            # Determine agent based on mode
            agent_id = "research_deep" if mode == "nexus" else "research_fast"
            
            # Execute via OpenClaw Client
            response = await openclaw.execute_agent_task(query, agent_id=agent_id)
            
            # Send result
            await notification.edit_text(
                f"{icon} **{title}: Report**\n\n{response}"
            )
            
        except Exception as e:
            logger.error(f"OpenClaw Request failed: {e}")
            await notification.edit_text(f"❌ **Engine Error:** {e}")
