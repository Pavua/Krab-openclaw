# -*- coding: utf-8 -*-
"""
Handlers Package — Модульная структура обработчиков команд Krab.

Каждый модуль отвечает за свою доменную область:
- commands: статус, диагностика, помощь, логи, конфиг
- ai: авто-ответ, reasoning, агентный цикл, генерация кода
- media: аудио (STT), фото (Vision), видео, документы
- tools: поиск (scout), новости, перевод, TTS
- system: терминал, exec, git, рефакторинг, panic
- scheduling: напоминания, таймеры, screen awareness
- mac: macOS Automation Bridge
- rag: управление базой знаний
- persona: личности, голос, саммаризация

Все обработчики регистрируются через register_handlers(app, deps).
"""

import asyncio
import structlog

logger = structlog.get_logger(__name__)


# _ensure_event_loop_for_pyrogram removed
# Was causing loop mismatch with asyncio.run()



def _register_or_skip(label: str, register_func, app, deps: dict):
    """
    Регистрирует обработчик и не валит запуск, если отсутствуют optional-зависимости.
    Это важно для тестового и облегчённого профиля.
    """
    try:
        register_func(app, deps)
    except KeyError as exc:
        logger.warning("Пропуск регистрации handler-модуля: отсутствует зависимость", module=label, missing=str(exc))


def register_all_handlers(app, deps: dict):
    """
    Регистрирует все обработчики на Pyrogram-клиент.
    
    deps — словарь зависимостей (router, memory, perceptor, и т.д.),
    чтобы обработчики не импортировали глобальные переменные напрямую.
    """
    from .commands import register_handlers as reg_commands
    from .ai import register_handlers as reg_ai
    from .media import register_handlers as reg_media
    from .tools import register_handlers as reg_tools
    from .system import register_handlers as reg_system
    from .scheduling import register_handlers as reg_scheduling
    from .mac import register_handlers as reg_mac
    from .rag import register_handlers as reg_rag
    from .persona import register_handlers as reg_persona
    from .cyber import register_cyber_handlers as reg_cyber
    from .communication import register_handlers as reg_comm
    from .privacy import register_handlers as reg_privacy
    from .plugins import register_handlers as reg_plugins
    from .groups import register_handlers as reg_groups
    from .telegram_control import register_handlers as reg_telegram_control
    from .provisioning import register_handlers as reg_provisioning
    from .ops import register_handlers as reg_ops
    from .project import register_handlers as reg_project

    # Порядок важен: debug_logger должен быть зарегистрирован ПЕРВЫМ (group=-1).
    _register_or_skip("commands", reg_commands, app, deps)
    _register_or_skip("ai", reg_ai, app, deps)
    _register_or_skip("media", reg_media, app, deps)
    _register_or_skip("tools", reg_tools, app, deps)
    _register_or_skip("system", reg_system, app, deps)
    _register_or_skip("scheduling", reg_scheduling, app, deps)
    _register_or_skip("mac", reg_mac, app, deps)
    _register_or_skip("rag", reg_rag, app, deps)
    _register_or_skip("persona", reg_persona, app, deps)
    _register_or_skip("cyber", reg_cyber, app, deps)
    _register_or_skip("communication", reg_comm, app, deps)
    _register_or_skip("groups", reg_groups, app, deps)
    _register_or_skip("telegram_control", reg_telegram_control, app, deps)
    _register_or_skip("provisioning", reg_provisioning, app, deps)
    _register_or_skip("privacy", reg_privacy, app, deps)
    _register_or_skip("plugins", reg_plugins, app, deps)
    _register_or_skip("ops", reg_ops, app, deps)
    _register_or_skip("project", reg_project, app, deps)
    
    from .finance import register_handlers as reg_finance
    _register_or_skip("finance", reg_finance, app, deps)

    from .trading import register_trading_handlers as reg_trading
    _register_or_skip("trading", reg_trading, app, deps)

    from .teams import register_handlers as reg_teams
    _register_or_skip("teams", reg_teams, app, deps)
