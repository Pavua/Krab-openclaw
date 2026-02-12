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

    # Порядок важен: debug_logger должен быть зарегистрирован ПЕРВЫМ (group=-1)
    reg_commands(app, deps)
    reg_ai(app, deps)
    reg_media(app, deps)
    reg_tools(app, deps)
    reg_system(app, deps)
    reg_scheduling(app, deps)
    reg_mac(app, deps)
    reg_rag(app, deps)
    reg_persona(app, deps)
    reg_cyber(app, deps)
    reg_comm(app, deps)
    reg_groups(app, deps)
    reg_privacy(app, deps)
    reg_plugins(app, deps)
    
    from .finance import register_handlers as reg_finance
    reg_finance(app, deps)
