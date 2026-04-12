# -*- coding: utf-8 -*-
"""
Единый реестр Telegram-команд Краба с метаданными.

Используется:
- Owner panel /api/commands (GET список, GET по имени)
- !help — генерация справки из реестра

Категории:
  basic       — базовая справка, диагностика
  ai          — AI-запросы, перевод, суммаризация
  models      — управление моделями
  translator  — автопереводчик
  swarm       — рой агентов
  costs       — расходы и бюджет
  notes       — заметки, закладки, память
  management  — управление сообщениями
  modes       — голос, тишина, фильтры
  users       — пользователи и доступ
  scheduler   — планировщик и напоминания
  system      — macOS, браузер, файлы
  dev         — Dev / AI CLI
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar


@dataclass(frozen=True)
class CommandInfo:
    """Метаданные одной команды."""

    name: str
    category: str
    description: str
    usage: str
    owner_only: bool = False
    aliases: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "category": self.category,
            "description": self.description,
            "owner_only": self.owner_only,
            "aliases": list(self.aliases),
            "usage": self.usage,
        }


# ---------------------------------------------------------------------------
# Реестр
# ---------------------------------------------------------------------------

_COMMANDS: list[CommandInfo] = [
    # ── basic ────────────────────────────────────────────────────────────────
    CommandInfo(
        name="help",
        category="basic",
        description="Справка по всем командам",
        usage="!help [команда]",
        aliases=["h"],
    ),
    CommandInfo(
        name="stats",
        category="basic",
        description="Статистика текущей сессии (токены, запросы)",
        usage="!stats",
    ),
    CommandInfo(
        name="health",
        category="basic",
        description="Диагностика всех подсистем",
        usage="!health",
    ),
    CommandInfo(
        name="status",
        category="basic",
        description="Статус всех подсистем Краба",
        usage="!status",
    ),
    CommandInfo(
        name="context",
        category="basic",
        description="Управление контекстом чата",
        usage="!context [clear|save]",
    ),
    CommandInfo(
        name="diagnose",
        category="basic",
        description="Детальная диагностика подключений",
        usage="!diagnose",
    ),
    CommandInfo(
        name="panel",
        category="basic",
        description="Owner panel (:8080)",
        usage="!panel",
        owner_only=True,
    ),
    CommandInfo(
        name="clear",
        category="basic",
        description="Очистить историю диалога",
        usage="!clear",
    ),

    # ── ai ───────────────────────────────────────────────────────────────────
    CommandInfo(
        name="ask",
        category="ai",
        description="Спросить AI о сообщении (reply → AI отвечает)",
        usage="!ask [вопрос]",
    ),
    CommandInfo(
        name="translate",
        category="ai",
        description="Перевод текста (reply или аргумент)",
        usage="!translate [язык]",
    ),
    CommandInfo(
        name="summary",
        category="ai",
        description="Суммаризация последних N сообщений",
        usage="!summary [N]",
    ),
    CommandInfo(
        name="catchup",
        category="ai",
        description="Кратко о пропущенном с момента последнего визита",
        usage="!catchup",
    ),
    CommandInfo(
        name="search",
        category="ai",
        description="Веб-поиск Brave",
        usage="!search <запрос>",
        aliases=["s"],
    ),
    CommandInfo(
        name="weather",
        category="ai",
        description="Текущая погода через web_search",
        usage="!weather [город]",
    ),
    CommandInfo(
        name="report",
        category="ai",
        description="Расширенный исследовательский отчёт",
        usage="!report <тема>",
    ),
    CommandInfo(
        name="img",
        category="ai",
        description="Описание фото через AI vision (reply на фото)",
        usage="!img [вопрос]",
        owner_only=True,
    ),

    # ── models ───────────────────────────────────────────────────────────────
    CommandInfo(
        name="model",
        category="models",
        description="Управление маршрутизацией модели",
        usage="!model [local|cloud|auto|set <id>|load <name>|unload|scan]",
        owner_only=True,
    ),
    CommandInfo(
        name="role",
        category="models",
        description="Смена системного ролевого промпта",
        usage="!role [name|list]",
        owner_only=True,
    ),
    CommandInfo(
        name="reasoning",
        category="models",
        description="Просмотр/очистка reasoning-trace",
        usage="!reasoning [show|clear]",
        owner_only=True,
    ),

    # ── translator ───────────────────────────────────────────────────────────
    CommandInfo(
        name="translator",
        category="translator",
        description="Управление автопереводчиком",
        usage="!translator on|off|status|history|lang <from>-<to>|mode <mode>|session start|stop|pause",
        owner_only=True,
    ),

    # ── swarm ────────────────────────────────────────────────────────────────
    CommandInfo(
        name="swarm",
        category="swarm",
        description="Мультиагентный рой: research, summary, schedule, teams, memory",
        usage="!swarm <team> <задача>|research <тема>|summary|teams|schedule|memory|jobs|task|artifacts|listen|channels|setup",
        owner_only=True,
    ),

    # ── costs ────────────────────────────────────────────────────────────────
    CommandInfo(
        name="costs",
        category="costs",
        description="Отчёт расходов по провайдерам",
        usage="!costs [detail]",
        owner_only=True,
    ),
    CommandInfo(
        name="budget",
        category="costs",
        description="Просмотр/установка дневного бюджета",
        usage="!budget [сумма]",
        owner_only=True,
    ),
    CommandInfo(
        name="digest",
        category="costs",
        description="Weekly digest активности",
        usage="!digest",
        owner_only=True,
    ),

    # ── notes ────────────────────────────────────────────────────────────────
    CommandInfo(
        name="memo",
        category="notes",
        description="Заметка в Obsidian (reply или аргумент)",
        usage="!memo <текст>",
    ),
    CommandInfo(
        name="note",
        category="notes",
        description="Голосовая заметка (reply на голосовое сообщение)",
        usage="!note",
    ),
    CommandInfo(
        name="bookmark",
        category="notes",
        description="Закладка на сообщение",
        usage="!bookmark",
        aliases=["bm"],
    ),
    CommandInfo(
        name="export",
        category="notes",
        description="Экспорт N последних сообщений чата",
        usage="!export [N]",
    ),
    CommandInfo(
        name="remember",
        category="notes",
        description="Запомнить факт в память",
        usage="!remember <текст>",
    ),
    CommandInfo(
        name="recall",
        category="notes",
        description="Вспомнить факт из памяти",
        usage="!recall <запрос>",
    ),
    CommandInfo(
        name="memory",
        category="notes",
        description="Последние записи памяти",
        usage="!memory recent",
    ),

    # ── management ───────────────────────────────────────────────────────────
    CommandInfo(
        name="pin",
        category="management",
        description="Закрепить сообщение (reply)",
        usage="!pin",
        owner_only=True,
    ),
    CommandInfo(
        name="unpin",
        category="management",
        description="Открепить сообщение (reply)",
        usage="!unpin",
        owner_only=True,
    ),
    CommandInfo(
        name="del",
        category="management",
        description="Удалить N последних сообщений (default 1)",
        usage="!del [N]",
        owner_only=True,
    ),
    CommandInfo(
        name="purge",
        category="management",
        description="Очистить историю бота в чате",
        usage="!purge",
        owner_only=True,
    ),
    CommandInfo(
        name="autodel",
        category="management",
        description="Автоудаление через N секунд (0 = выключить)",
        usage="!autodel <сек>",
        owner_only=True,
    ),
    CommandInfo(
        name="fwd",
        category="management",
        description="Переслать сообщение (reply)",
        usage="!fwd <chat_id>",
        owner_only=True,
    ),
    CommandInfo(
        name="collect",
        category="management",
        description="Собрать N сообщений чата в один текст",
        usage="!collect [N]",
    ),
    CommandInfo(
        name="react",
        category="management",
        description="Поставить реакцию (reply)",
        usage="!react <эмодзи>",
    ),
    CommandInfo(
        name="schedule",
        category="management",
        description="Отложенные сообщения",
        usage="!schedule [list|cancel|add]",
        owner_only=True,
    ),

    # ── modes ────────────────────────────────────────────────────────────────
    CommandInfo(
        name="voice",
        category="modes",
        description="Управление голосовыми ответами и TTS",
        usage="!voice on|off|toggle|block|unblock|speed <0.75..2.5>|voice <edge-tts-id>",
        owner_only=True,
    ),
    CommandInfo(
        name="тишина",
        category="modes",
        description="Режим тишины (без AI-ответов)",
        usage="!тишина [мин|стоп|глобально|расписание HH:MM-HH:MM|статус]",
        owner_only=True,
        aliases=["silence"],
    ),
    CommandInfo(
        name="chatban",
        category="modes",
        description="Заблокировать обработку чата",
        usage="!chatban [chat_id]",
        owner_only=True,
    ),
    CommandInfo(
        name="notify",
        category="modes",
        description="Tool narrations (🔍 Ищу... 📸 Скриншот...)",
        usage="!notify on|off",
        owner_only=True,
    ),
    CommandInfo(
        name="cap",
        category="modes",
        description="Матрица capabilities чатов",
        usage="!cap [name on|off|reset]",
        owner_only=True,
    ),

    # ── users ────────────────────────────────────────────────────────────────
    CommandInfo(
        name="who",
        category="users",
        description="Информация о пользователе или чате",
        usage="!who [@user|reply]",
    ),
    CommandInfo(
        name="acl",
        category="users",
        description="Управление full/partial доступом",
        usage="!acl",
        owner_only=True,
        aliases=["access"],
    ),
    CommandInfo(
        name="alias",
        category="users",
        description="Алиасы команд",
        usage="!alias [add|del|list]",
        owner_only=True,
    ),
    CommandInfo(
        name="inbox",
        category="users",
        description="Owner inbox / escalation",
        usage="!inbox [list|ack|done|approve|reject|task]",
        owner_only=True,
    ),

    # ── scheduler ────────────────────────────────────────────────────────────
    CommandInfo(
        name="remind",
        category="scheduler",
        description="Поставить напоминание",
        usage="!remind <время> | <текст>",
    ),
    CommandInfo(
        name="reminders",
        category="scheduler",
        description="Список активных напоминаний",
        usage="!reminders",
    ),
    CommandInfo(
        name="rm_remind",
        category="scheduler",
        description="Удалить напоминание по id",
        usage="!rm_remind <id>",
        owner_only=True,
    ),
    CommandInfo(
        name="cronstatus",
        category="scheduler",
        description="Статус cron scheduler",
        usage="!cronstatus",
        owner_only=True,
    ),
    CommandInfo(
        name="monitor",
        category="scheduler",
        description="Мониторинг чатов",
        usage="!monitor [add|del|list|status]",
        owner_only=True,
    ),
    CommandInfo(
        name="watch",
        category="scheduler",
        description="Proactive watch / owner-digest",
        usage="!watch status|now",
        owner_only=True,
    ),

    # ── system ───────────────────────────────────────────────────────────────
    CommandInfo(
        name="sysinfo",
        category="system",
        description="Информация о хосте (CPU/RAM/диск)",
        usage="!sysinfo",
        owner_only=True,
    ),
    CommandInfo(
        name="ls",
        category="system",
        description="Список файлов",
        usage="!ls [path]",
        owner_only=True,
    ),
    CommandInfo(
        name="read",
        category="system",
        description="Чтение файла",
        usage="!read <path>",
        owner_only=True,
    ),
    CommandInfo(
        name="write",
        category="system",
        description="Запись файла",
        usage="!write <file> <content>",
        owner_only=True,
    ),
    CommandInfo(
        name="mac",
        category="system",
        description="macOS автоматизация (clipboard/notify/apps/finder/notes/reminders/calendar)",
        usage="!mac clipboard|notify|apps|finder|notes|reminders|calendar",
        owner_only=True,
    ),
    CommandInfo(
        name="screenshot",
        category="system",
        description="Снимок Chrome / OCR / статус CDP",
        usage="!screenshot [ocr [lang]|health]",
        owner_only=True,
    ),
    CommandInfo(
        name="hs",
        category="system",
        description="Hammerspoon bridge",
        usage="!hs <команда>",
        owner_only=True,
    ),
    CommandInfo(
        name="web",
        category="system",
        description="Управление браузером",
        usage="!web [status|open|close]",
        owner_only=True,
    ),
    CommandInfo(
        name="browser",
        category="system",
        description="CDP browser bridge",
        usage="!browser [cdp|status]",
        owner_only=True,
    ),

    # ── dev ──────────────────────────────────────────────────────────────────
    CommandInfo(
        name="agent",
        category="dev",
        description="Управление агентами",
        usage="!agent new <name> <prompt>|list|swarm [loop N] <тема>",
        owner_only=True,
    ),
    CommandInfo(
        name="codex",
        category="dev",
        description="OpenAI Codex CLI",
        usage="!codex <задача>",
        owner_only=True,
    ),
    CommandInfo(
        name="gemini",
        category="dev",
        description="Gemini CLI",
        usage="!gemini <задача>",
        owner_only=True,
    ),
    CommandInfo(
        name="claude_cli",
        category="dev",
        description="Claude Code CLI",
        usage="!claude_cli <задача>",
        owner_only=True,
    ),
    CommandInfo(
        name="opencode",
        category="dev",
        description="OpenCode CLI",
        usage="!opencode <задача>",
        owner_only=True,
    ),
    CommandInfo(
        name="shop",
        category="dev",
        description="Mercadona Playwright scraper",
        usage="!shop <url>",
        owner_only=True,
    ),
    CommandInfo(
        name="config",
        category="dev",
        description="Просмотр/установка настроек",
        usage="!config|!set <KEY> <VAL>",
        owner_only=True,
        aliases=["set"],
    ),
    CommandInfo(
        name="restart",
        category="dev",
        description="Перезапуск бота",
        usage="!restart",
        owner_only=True,
    ),
    CommandInfo(
        name="qr",
        category="basic",
        description="Генерация QR-кода из текста или URL",
        usage="!qr <текст|URL>  или ответь на сообщение",
        owner_only=True,
    ),
]


# ---------------------------------------------------------------------------
# Публичное API реестра
# ---------------------------------------------------------------------------

class CommandRegistry:
    """Единый реестр команд. Используется API и !help."""

    # Упорядоченный список категорий для вывода
    CATEGORY_ORDER: ClassVar[list[str]] = [
        "basic",
        "ai",
        "models",
        "translator",
        "swarm",
        "costs",
        "notes",
        "management",
        "modes",
        "users",
        "scheduler",
        "system",
        "dev",
    ]

    def __init__(self, commands: list[CommandInfo]) -> None:
        self._commands = commands
        # Индекс по имени (включая алиасы)
        self._by_name: dict[str, CommandInfo] = {}
        for cmd in commands:
            self._by_name[cmd.name] = cmd
            for alias in cmd.aliases:
                self._by_name[alias] = cmd

    def all(self) -> list[CommandInfo]:
        """Все команды (уникальные, без дублей алиасов)."""
        return list(self._commands)

    def get(self, name: str) -> CommandInfo | None:
        """Поиск по имени или алиасу. Возвращает None если не найдено."""
        key = name.lstrip("!").lower()
        return self._by_name.get(key)

    def categories(self) -> list[str]:
        """Список категорий в порядке вывода."""
        present = {cmd.category for cmd in self._commands}
        return [c for c in self.CATEGORY_ORDER if c in present]

    def by_category(self, category: str) -> list[CommandInfo]:
        """Команды одной категории."""
        return [cmd for cmd in self._commands if cmd.category == category]

    def to_api_response(self) -> dict:
        """Полный ответ для GET /api/commands."""
        cmds = [cmd.to_dict() for cmd in self._commands]
        return {
            "ok": True,
            "total": len(cmds),
            "commands": cmds,
            "categories": self.categories(),
        }


# Глобальный синглтон
registry = CommandRegistry(_COMMANDS)
