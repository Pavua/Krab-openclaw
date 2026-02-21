# -*- coding: utf-8 -*-
"""
RAG Handler — Управление базой знаний.

Извлечён из main.py (строки ~1177-1237). Включает:
- !rag — статистика
- !rag cleanup — удалить устаревшие документы
- !rag export — экспорт в JSON
- !rag search <запрос> — поиск по базе
"""

from pyrogram import filters
from pyrogram.types import Message

from .auth import is_owner

import structlog
logger = structlog.get_logger(__name__)


def register_handlers(app, deps: dict):
    """Регистрирует обработчик RAG-управления."""
    router = deps["router"]
    safe_handler = deps["safe_handler"]

    @app.on_message(filters.command("rag", prefixes="!"))
    @safe_handler
    async def rag_command(client, message: Message):
        """
        Управление RAG базой знаний.
        !rag — статистика
        !rag cleanup — удалить устаревшие документы
        !rag export — экспорт в JSON
        !rag search <запрос> — поиск по базе
        """
        if not is_owner(message):
            return

        sub = (
            message.command[1].lower() if len(message.command) > 1
            else "stats"
        )

        if sub == "stats":
            report = router.rag.format_stats_report()
            await message.reply_text(report)

        elif sub == "cleanup":
            notification = await message.reply_text(
                "🧹 **Очищаю устаревшие документы...**"
            )
            removed = router.rag.cleanup_expired()
            await notification.edit_text(
                f"🧹 **Очистка завершена!** Удалено: {removed} документов"
            )

        elif sub == "export":
            notification = await message.reply_text(
                "📦 **Экспортирую базу знаний...**"
            )
            path = router.rag.export_knowledge()
            if path:
                await notification.edit_text(
                    f"📦 **Экспорт завершён!**\nФайл: `{path}`"
                )
            else:
                await notification.edit_text("❌ Ошибка экспорта")

        elif sub == "search":
            query = (
                " ".join(message.command[2:]) if len(message.command) > 2
                else ""
            )
            if not query:
                await message.reply_text("🔍 Укажи запрос: `!rag search <текст>`")
                return

            results = router.rag.query_with_scores(query, n_results=5)
            if results:
                text = "**🔍 Результаты поиска в RAG:**\n\n"
                for i, r in enumerate(results, 1):
                    expired_mark = " ⏰" if r["expired"] else ""
                    text += (
                        f"**{i}.** [{r['category']}]{expired_mark} "
                        f"(score: {r['score']})\n"
                        f"`{r['text'][:150]}...`\n\n"
                    )
                await message.reply_text(text)
            else:
                await message.reply_text("🔍 Ничего не найдено в базе знаний.")

        else:
            await message.reply_text(
                "**🧠 RAG v2.0 — Команды:**\n\n"
                "`!rag` — Статистика\n"
                "`!rag cleanup` — Очистка устаревших\n"
                "`!rag export` — Экспорт в JSON\n"
                "`!rag search <запрос>` — Поиск\n"
            )
