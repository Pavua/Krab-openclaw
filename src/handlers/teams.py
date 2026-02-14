# -*- coding: utf-8 -*-
"""
Teams Handler — Универсальный хэндлер для Swarm-команд.

Единая точка входа для запуска специализированных команд агентов.
Каждая команда — это рой AI-агентов, работающих последовательно над задачей.

Доступные команды:
  !team list              — Список доступных команд
  !team osint <запрос>    — OSINT-разведка (Planner → Researcher → Analyst)
  !team content <тема>    — Контент-завод (SEO → Copywriter → Editor)
  !team dev <задача>      — Dev Squad (Architect → Coder → Critic)
  !team summary <текст>   — Резюме (Researcher → Analyst → Editor)
  !team email <тема>      — Деловое письмо (Copywriter → Stylist → Proofreader)
  !team audit <код>       — Security-аудит (Pentester → Analyst → Advisor)
  !team plan <идея>       — Бизнес-план (Strategist → Financier → Critic)

Связанные модули:
  - src/core/agent_swarm.py — определения команд и SwarmManager
  - src/handlers/trading.py — торговый рой (отдельный хэндлер !trade)
"""

import structlog
from pyrogram import filters
from pyrogram.types import Message
from src.core.agent_swarm import SwarmManager

logger = structlog.get_logger(__name__)

# Эмодзи для каждого типа команды — для красивого вывода
TEAM_EMOJI = {
    "osint": "🔍",
    "content": "✍️",
    "dev": "💻",
    "summary": "📋",
    "email": "📧",
    "audit": "🛡️",
    "planning": "📊",
    "trading": "📈",
}

# Описания для !team list
TEAM_DESCRIPTIONS = {
    "osint": "OSINT-разведка — глубокий поиск и анализ информации",
    "content": "Контент-завод — SEO-оптимизированные тексты под ключ",
    "dev": "Dev Squad — архитектура, код и ревью за один проход",
    "summary": "Резюме — Executive Summary из любого текста или URL",
    "email": "Деловое письмо — профессиональная коммуникация",
    "audit": "Security-аудит — поиск уязвимостей и рекомендации",
    "planning": "Бизнес-план — стратегия, финансы и критика",
    "trading": "Торговый рой — анализ, стратегия, риски (используй !trade)",
}


def register_handlers(app, deps: dict):
    """Регистрирует обработчик команды !team."""
    router = deps["router"]
    safe_handler = deps["safe_handler"]
    # Создаём или переиспользуем существующий SwarmManager
    swarm_manager = deps.get("swarm_manager") or SwarmManager(router)

    # Импортируем проверку владельца
    try:
        from .auth import is_owner
    except ImportError:
        def is_owner(m): return True  # Фоллбэк для тестов

    @app.on_message(filters.command("team", prefixes="!"))
    @safe_handler
    async def team_command(client, message: Message):
        """
        Универсальный хэндлер Swarm-команд.
        Формат: !team <тип> <задача>
        """
        if not is_owner(message):
            return

        args = message.text.split(None, 2)  # ['!team', 'тип', 'задача...']

        # Без аргументов или с list — показываем доступные команды
        if len(args) < 2 or args[1].lower() == "list":
            lines = ["🦀 **Доступные Swarm-команды:**\n"]
            for team_type, description in TEAM_DESCRIPTIONS.items():
                emoji = TEAM_EMOJI.get(team_type, "🤖")
                lines.append(f"{emoji} `!team {team_type}` — {description}")
            lines.append("\n💡 **Пример:** `!team content Напиши статью про AI-агентов`")
            await message.reply_text("\n".join(lines))
            return

        team_type = args[1].lower()

        # Если указан trading — перенаправляем на !trade
        if team_type == "trading":
            await message.reply_text("📈 Для торгового роя используй `!trade <данные>`")
            return

        # Проверяем что тип команды известен
        valid_types = ["osint", "content", "dev", "summary", "email", "audit", "planning"]
        if team_type not in valid_types:
            await message.reply_text(
                f"❌ Неизвестная команда: `{team_type}`\n\n"
                f"Доступные: {', '.join(valid_types)}\n"
                f"Подробнее: `!team list`"
            )
            return

        # Проверяем что задача указана
        if len(args) < 3:
            await message.reply_text(
                f"❌ Укажи задачу!\n"
                f"Пример: `!team {team_type} Твоя задача здесь`"
            )
            return

        task_description = args[2]
        emoji = TEAM_EMOJI.get(team_type, "🤖")

        # Уведомление о запуске
        notification = await message.reply_text(
            f"{emoji} **Запуск команды `{team_type.upper()}`...**\n"
            f"_Агенты приступают к работе. Это может занять 30-90 секунд._"
        )

        try:
            # Запускаем рой
            result = await swarm_manager.run_team(team_type, task_description)

            # Обрезаем, если слишком длинный для Telegram (4096 символов)
            if len(result) > 4000:
                result = result[:3950] + "\n\n... _(сокращено)_"

            await notification.edit_text(result)

        except Exception as e:
            logger.error("Swarm team task failed", team=team_type, error=str(e))
            await notification.edit_text(
                f"❌ Ошибка команды `{team_type}`: `{str(e)[:200]}`"
            )
