# -*- coding: utf-8 -*-
"""
Единый реестр Telegram-команд Краба с метаданными.

Используется:
- Owner panel /api/commands (GET список, GET по имени)
- !help — генерация справки из реестра
- /api/commands/usage — аналитика использования команд

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
  system      — macOS, браузер, файлы, шифрование
  files       — медиафайлы (фото/видео/документ)
  dev         — Dev / AI CLI, отладка, eval
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

import structlog

_log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Аналитика использования команд
# ---------------------------------------------------------------------------

_command_usage: dict[str, int] = {}
_usage_file = Path("~/.openclaw/krab_runtime_state/command_usage.json").expanduser()


def bump_command(name: str) -> None:
    """Инкрементирует счётчик вызова команды."""
    _command_usage[name] = _command_usage.get(name, 0) + 1


def get_usage() -> dict[str, int]:
    """Возвращает счётчики, отсортированные по убыванию."""
    return dict(sorted(_command_usage.items(), key=lambda x: -x[1]))


def save_usage() -> None:
    """Сохраняет счётчики на диск (вызывается периодически и при остановке)."""
    try:
        _usage_file.parent.mkdir(parents=True, exist_ok=True)
        _usage_file.write_text(json.dumps(_command_usage, indent=2, ensure_ascii=False))
    except Exception as exc:  # noqa: BLE001
        _log.warning("command_usage_save_failed", error=str(exc))


def load_usage() -> None:
    """Загружает счётчики с диска при старте."""
    global _command_usage  # noqa: PLW0603
    try:
        _command_usage = json.loads(_usage_file.read_text())
        _log.info("command_usage_loaded", commands=len(_command_usage))
    except FileNotFoundError:
        _command_usage = {}
    except json.JSONDecodeError as exc:
        _log.warning("command_usage_corrupt", error=str(exc))
        _command_usage = {}


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
        name="explain",
        category="ai",
        description="Объяснение кода простым языком через AI",
        usage="!explain <код>  или reply на сообщение с кодом",
    ),
    CommandInfo(
        name="news",
        category="ai",
        description="Топ-5 новостей через AI — тема или язык (ru/en)",
        usage="!news [тема|ru|en]",
    ),
    CommandInfo(
        name="weather",
        category="ai",
        description="Текущая погода через web_search",
        usage="!weather [город]",
    ),
    CommandInfo(
        name="rate",
        category="ai",
        description="Курс криптовалюты или акции через AI (цена, 24h%, капитализация)",
        usage="!rate <тикер> [тикер2 ...]",
        aliases=["курс"],
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
    CommandInfo(
        name="ocr",
        category="ai",
        description="Извлечение текста из изображения через AI vision (reply на фото)",
        usage="!ocr [подсказка]",
        owner_only=True,
    ),
    CommandInfo(
        name="media",
        category="files",
        description="Скачивание медиафайлов (фото/видео/документ/аудио). Reply на медиа.",
        usage="!media [save|info]",
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
        name="paste",
        category="notes",
        description="Отправить длинный текст как файл-документ (>4096 символов)",
        usage="!paste <текст>  или reply на сообщение",
    ),
    CommandInfo(
        name="remember",
        category="notes",
        description="Запомнить факт в память",
        usage="!remember <текст>",
    ),
    CommandInfo(
        name="confirm",
        category="notes",
        description="Подтвердить persistent-запись памяти (owner-only)",
        usage="!confirm <hash>",
        owner_only=True,
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
        name="archive",
        category="management",
        description="Архивировать текущий чат (list — список архива)",
        usage="!archive [list]",
        owner_only=True,
    ),
    CommandInfo(
        name="unarchive",
        category="management",
        description="Разархивировать текущий чат",
        usage="!unarchive",
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
        name="blocked",
        category="users",
        description="Управление заблокированными пользователями",
        usage="!blocked [list|add|remove]",
        owner_only=True,
    ),
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
        name="cron",
        category="scheduler",
        description="Управление OpenClaw cron jobs (list/enable/disable/run/status)",
        usage="!cron [list|enable|disable|run|status] [<name>]",
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
    CommandInfo(
        name="chatinfo",
        category="system",
        description="Подробная информация о чате",
        usage="!chatinfo [chat_id|@username]",
        owner_only=False,
    ),
    CommandInfo(
        name="history",
        category="system",
        description="Статистика чата (последние 1000 сообщений)",
        usage="!history",
        owner_only=False,
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
    CommandInfo(
        name="convert",
        category="basic",
        description="Конвертер единиц: длина, масса, объём, скорость, температура",
        usage="!convert <число> <из> <в>  (напр. !convert 100 km mi)",
    ),
    CommandInfo(
        name="template",
        category="notes",
        description="Шаблоны сообщений с подстановкой переменных",
        usage=(
            "!template save <name> <text>  — сохранить\n"
            "!template <name> [val1 val2]  — отправить (с подстановкой)\n"
            "!template list                — список\n"
            "!template del <name>          — удалить"
        ),
        owner_only=True,
    ),
    CommandInfo(
        name="say",
        category="management",
        description="Тихая отправка сообщения от имени юзербота (команда удаляется)",
        usage=(
            "!say <текст>              — отправить в текущий чат\n"
            "!say <chat_id> <текст>   — отправить в другой чат"
        ),
        owner_only=True,
    ),

    # ── users (дополнительные) ───────────────────────────────────────────────
    CommandInfo(
        name="scope",
        category="users",
        description="Управление ACL-правами: просмотр уровня доступа, grant/revoke",
        usage="!scope | !scope grant <user_id> full|partial | !scope revoke <user_id> | !scope list",
    ),
    CommandInfo(
        name="profile",
        category="users",
        description="Профиль пользователя Telegram: аватар, статистика, биография",
        usage="!profile [@user|reply]",
    ),
    CommandInfo(
        name="contacts",
        category="users",
        description="Список контактов Telegram (поиск, статистика)",
        usage="!contacts [поиск]",
        owner_only=True,
    ),
    CommandInfo(
        name="invite",
        category="users",
        description="Пригласить пользователя в группу/канал",
        usage="!invite <@user|id> [chat_id]",
        owner_only=True,
    ),
    CommandInfo(
        name="members",
        category="users",
        description="Список участников чата с фильтрацией",
        usage="!members [N] [admins|bots|recent]",
        owner_only=True,
    ),
    CommandInfo(
        name="whois",
        category="users",
        description="WHOIS-поиск домена или IP-адреса через AI",
        usage="!whois <domain|IP>",
    ),

    # ── management (дополнительные) ──────────────────────────────────────────
    CommandInfo(
        name="mark",
        category="management",
        description="Пометить сообщение тегом для быстрого поиска",
        usage="!mark [tag]  (reply на сообщение)",
        owner_only=True,
    ),
    CommandInfo(
        name="chatmute",
        category="management",
        description="Замутить уведомления чата на N минут (0 = снять мут)",
        usage="!chatmute [мин]",
        owner_only=True,
    ),
    CommandInfo(
        name="slowmode",
        category="management",
        description="Включить slow mode в группе (задержка в секундах)",
        usage="!slowmode <сек>  (0 = выключить)",
        owner_only=True,
    ),

    # ── modes (дополнительные) ───────────────────────────────────────────────
    CommandInfo(
        name="afk",
        category="modes",
        description="Режим Away From Keyboard: авто-ответ при упоминании",
        usage="!afk [сообщение] | !afk off",
        owner_only=True,
    ),
    CommandInfo(
        name="typing",
        category="modes",
        description="Имитировать typing action в чате",
        usage="!typing [сек]",
        owner_only=True,
    ),

    # ── notes (дополнительные) ───────────────────────────────────────────────
    CommandInfo(
        name="todo",
        category="notes",
        description="Список задач: добавить, показать, отметить выполненными",
        usage="!todo [add <текст>|list|done <N>|clear]",
    ),
    CommandInfo(
        name="quote",
        category="notes",
        description="Сохранить/показать цитату (reply на сообщение)",
        usage="!quote [save|list|random|del <id>]",
    ),
    CommandInfo(
        name="snippet",
        category="notes",
        description="Сниппеты кода: сохранить и отправить с подсветкой синтаксиса",
        usage="!snippet save <name> <lang> <code> | !snippet <name> | !snippet list",
        owner_only=True,
    ),

    # ── ai (дополнительные) ──────────────────────────────────────────────────
    CommandInfo(
        name="urban",
        category="ai",
        description="Определение слова из Urban Dictionary через AI + web_search",
        usage="!urban <слово>",
    ),
    CommandInfo(
        name="define",
        category="ai",
        description="Определение слова из словаря (через AI)",
        usage="!define <слово>",
    ),
    CommandInfo(
        name="poll",
        category="ai",
        description="Создать опрос в чате",
        usage="!poll <вопрос> | <вариант1> | <вариант2> ...",
    ),
    CommandInfo(
        name="quiz",
        category="ai",
        description="AI-генерированная викторина по теме",
        usage="!quiz <тема>",
        owner_only=True,
    ),
    CommandInfo(
        name="tts",
        category="ai",
        description="Text-to-speech: преобразовать текст в голосовое сообщение",
        usage="!tts <текст>  или reply на сообщение",
        owner_only=True,
    ),

    # ── system (дополнительные) ──────────────────────────────────────────────
    CommandInfo(
        name="version",
        category="system",
        description="Версия Краба: git commit, branch, Python, Pyrogram, OpenClaw",
        usage="!version",
    ),
    CommandInfo(
        name="uptime",
        category="system",
        description="Аптайм Краба и системный uptime macOS",
        usage="!uptime",
    ),
    CommandInfo(
        name="log",
        category="system",
        description="Последние N строк лог-файла Краба",
        usage="!log [N] [error|warn|info]",
        owner_only=True,
    ),
    CommandInfo(
        name="sticker",
        category="system",
        description="Информация о стикере (reply): pack, emoji, file_id",
        usage="!sticker  (reply на стикер)",
    ),

    # ── dev (дополнительные) ─────────────────────────────────────────────────
    CommandInfo(
        name="debug",
        category="dev",
        description="Отладочная сводка: tasks, sessions, GC, last error (owner-only)",
        usage="!debug [sessions|tasks|gc]",
        owner_only=True,
    ),
    CommandInfo(
        name="backup",
        category="dev",
        description="Экспорт всех persistent данных Краба в ZIP-архив",
        usage="!backup [list]",
        owner_only=True,
    ),
    CommandInfo(
        name="eval",
        category="dev",
        description="Выполнить произвольный Python-код (owner-only)",
        usage="!eval <python код>",
        owner_only=True,
    ),
    CommandInfo(
        name="run",
        category="dev",
        description="Запустить shell-скрипт или команду (owner-only)",
        usage="!run <команда>",
        owner_only=True,
    ),
    CommandInfo(
        name="grep",
        category="dev",
        description="Поиск паттерна по тексту или reply (regex поддерживается)",
        usage="!grep <паттерн> [текст]  или reply на сообщение",
        owner_only=True,
    ),
    CommandInfo(
        name="json",
        category="dev",
        description="Форматировать/валидировать JSON",
        usage="!json <json-строка>  или reply на сообщение",
    ),
    CommandInfo(
        name="yt",
        category="dev",
        description="Информация о YouTube-видео или плейлисте",
        usage="!yt <url|id>",
    ),

    # ── basic (дополнительные) ───────────────────────────────────────────────
    CommandInfo(
        name="calc",
        category="basic",
        description="Калькулятор: математические выражения (поддерживает функции)",
        usage="!calc <выражение>",
    ),
    CommandInfo(
        name="rand",
        category="basic",
        description="Случайное число, выбор из списка или перемешивание",
        usage="!rand [N] | !rand <a> <b> | !rand pick item1 item2 ...",
    ),
    CommandInfo(
        name="dice",
        category="basic",
        description="Бросок кубика(ов): стандартные нотации (2d6, d20)",
        usage="!dice [NdM]",
    ),
    CommandInfo(
        name="time",
        category="basic",
        description="Текущее время в разных часовых поясах",
        usage="!time [город|timezone]",
    ),
    CommandInfo(
        name="len",
        category="basic",
        description="Длина текста в символах, словах и байтах",
        usage="!len <текст>  или reply на сообщение",
    ),
    CommandInfo(
        name="hash",
        category="basic",
        description="Хэш текста или файла (MD5, SHA1, SHA256, SHA512)",
        usage="!hash [алгоритм] <текст>  или reply на файл",
    ),
    CommandInfo(
        name="b64",
        category="basic",
        description="Base64 кодирование/декодирование",
        usage="!b64 encode|decode <текст>",
    ),
    CommandInfo(
        name="ip",
        category="basic",
        description="Геолокация IP-адреса и ASN-информация",
        usage="!ip <IP-адрес>",
    ),
    CommandInfo(
        name="dns",
        category="basic",
        description="DNS-запрос: A, AAAA, MX, TXT, CNAME записи",
        usage="!dns <домен> [тип]",
    ),
    CommandInfo(
        name="ping",
        category="basic",
        description="Ping хоста (ICMP или TCP)",
        usage="!ping <host> [порт]",
    ),
    CommandInfo(
        name="currency",
        category="basic",
        description="Конвертация валют через AI",
        usage="!currency <сумма> <из> <в>  (напр. !currency 100 USD EUR)",
    ),

    # ── management (утилиты текста) ──────────────────────────────────────────
    CommandInfo(
        name="sed",
        category="management",
        description="Замена текста по паттерну s/старое/новое/ (reply на сообщение)",
        usage="!sed s/старое/новое/  (reply на сообщение)",
    ),
    CommandInfo(
        name="diff",
        category="management",
        description="Diff двух текстов: unified-формат",
        usage="!diff <текст1> --- <текст2>  или reply + аргумент",
    ),
    CommandInfo(
        name="regex",
        category="management",
        description="Проверить регулярное выражение против текста",
        usage="!regex <паттерн> <текст>  или reply на сообщение",
    ),
    CommandInfo(
        name="tag",
        category="management",
        description="Тегировать участников группы (упомянуть @всех или список)",
        usage="!tag [all|admins|<user1> <user2>]",
        owner_only=True,
    ),
    CommandInfo(
        name="link",
        category="management",
        description="Создать invite-link чата или получить ссылку на сообщение",
        usage="!link [сообщение]  (reply на сообщение для permalink)",
        owner_only=True,
    ),
    CommandInfo(
        name="top",
        category="management",
        description="Лидерборд активности чата по количеству сообщений",
        usage="!top [N] | !top week | !top all",
    ),
    CommandInfo(
        name="welcome",
        category="management",
        description="Настройка приветственного сообщения для новых участников",
        usage="!welcome <текст> | !welcome off | !welcome status",
        owner_only=True,
    ),

    # ── system (утилиты шифрования) ──────────────────────────────────────────
    CommandInfo(
        name="encrypt",
        category="system",
        description="Зашифровать текст паролем (AES-256)",
        usage="!encrypt <пароль> <текст>",
        owner_only=True,
    ),
    CommandInfo(
        name="decrypt",
        category="system",
        description="Расшифровать текст паролем (AES-256)",
        usage="!decrypt <пароль> <зашифрованный текст>",
        owner_only=True,
    ),
    CommandInfo(
        name="color",
        category="system",
        description="Конвертация и просмотр цвета: HEX, RGB, HSL",
        usage="!color <#HEX|rgb(r,g,b)|название>",
    ),
    CommandInfo(
        name="emoji",
        category="system",
        description="Информация об эмодзи: код, название, категория",
        usage="!emoji <эмодзи>  или !emoji search <название>",
    ),

    # ── scheduler (дополнительные) ───────────────────────────────────────────
    CommandInfo(
        name="timer",
        category="scheduler",
        description="Таймер с уведомлением по истечении: list, cancel",
        usage="!timer <время> [метка] | !timer list | !timer cancel [id]",
    ),
    CommandInfo(
        name="stopwatch",
        category="scheduler",
        description="Секундомер: start, stop, lap, reset",
        usage="!stopwatch start|stop|lap|reset",
    ),

    # ── ai (media/vision) ────────────────────────────────────────────────────
    CommandInfo(
        name="spam",
        category="modes",
        description="Антиспам: блокировать/разблокировать пользователя за спам",
        usage="!spam block <@user|id> | !spam unblock <id> | !spam list",
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
        "files",
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
