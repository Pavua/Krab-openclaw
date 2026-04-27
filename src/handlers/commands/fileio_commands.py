# -*- coding: utf-8 -*-
"""
fileio_commands — Phase 2 Wave 13 extraction (Session 27).

Команды работы с файлами:
  !ls, !read, !write, !paste, !export

Re-exported из command_handlers.py для обратной совместимости (тесты,
external imports `from src.handlers.command_handlers import handle_ls`).
"""

from __future__ import annotations

import datetime
import os
import pathlib
from typing import TYPE_CHECKING, Any

import httpx
from pyrogram.types import Message

from ...config import config as _config_baseline
from ...core.exceptions import UserInputError
from ...core.logger import get_logger
from ...mcp_client import mcp_manager as _mcp_manager_baseline

if TYPE_CHECKING:
    from ...userbot_bridge import KraabUserbot

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Dual-namespace lookup (patch через command_handlers namespace)
# ---------------------------------------------------------------------------

# Baseline-алиасы — используются если patch применён к fileio_commands напрямую
_os_baseline = os
_pathlib_baseline = pathlib


def _ch_attr(name: str, default: Any) -> Any:
    """Dual-namespace lookup: command_handlers namespace first (для monkeypatch),
    fallback к local baseline."""
    from .. import command_handlers as _ch  # noqa: PLC0415

    return getattr(_ch, name, default)


# ---------------------------------------------------------------------------
# Константы (EXPORT_VAULT_DIR патчится тестами через command_handlers)
# ---------------------------------------------------------------------------

EXPORT_VAULT_DIR = pathlib.Path("/Users/pablito/Documents/Obsidian Vault/30_Recordings/32_Chats")
EXPORT_DEFAULT_LIMIT = 100
EXPORT_MAX_LIMIT = 1000


# ---------------------------------------------------------------------------
# Приватные хелперы !export
# ---------------------------------------------------------------------------


def _sanitize_filename(name: str) -> str:
    """Убирает символы, запрещённые в именах файлов."""
    return "".join(c if c.isalnum() or c in " _-" else "_" for c in name).strip()


def _format_sender(msg: Any) -> str:
    """Возвращает отображаемое имя отправителя сообщения."""
    if msg.from_user:
        u = msg.from_user
        parts = [u.first_name or "", u.last_name or ""]
        full = " ".join(p for p in parts if p).strip()
        return full or u.username or str(u.id)
    if msg.sender_chat:
        return msg.sender_chat.title or str(msg.sender_chat.id)
    return "Unknown"


def _msg_text(msg: Any) -> str:
    """Возвращает текстовое содержимое сообщения (текст или подпись)."""
    return (msg.text or msg.caption or "").strip()


def _render_export_markdown(
    chat_title: str,
    chat_id: int,
    messages: list,
    exported_at: datetime.datetime,
) -> str:
    """Рендерит список сообщений в Markdown-формат с YAML frontmatter."""
    header = (
        "---\n"
        f"chat_title: {chat_title}\n"
        f"chat_id: {chat_id}\n"
        f"exported: {exported_at.strftime('%Y-%m-%dT%H:%M:%S')}\n"
        f"messages: {len(messages)}\n"
        "---\n"
    )

    # Группируем по дате
    days: dict[str, list] = {}
    for msg in messages:
        if msg.date is None:
            continue
        day_key = msg.date.strftime("%Y-%m-%d")
        days.setdefault(day_key, []).append(msg)

    body_parts: list[str] = []
    for day_key in sorted(days):
        body_parts.append(f"\n## {day_key}\n")
        for msg in days[day_key]:
            time_str = msg.date.strftime("%H:%M")
            sender = _format_sender(msg)
            text = _msg_text(msg)
            # Медиа без подписи
            if not text:
                if msg.photo:
                    text = "_[фото]_"
                elif msg.video:
                    text = "_[видео]_"
                elif msg.audio or msg.voice:
                    text = "_[аудио]_"
                elif msg.document:
                    text = "_[документ]_"
                elif msg.sticker:
                    text = f"_[стикер: {msg.sticker.emoji or ''}]_"
                else:
                    text = "_[медиа]_"
            body_parts.append(f"### {time_str} — {sender}\n{text}\n")

    return header + "".join(body_parts)


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


async def handle_ls(bot: "KraabUserbot", message: Message) -> None:
    """Список файлов (!ls)."""
    cfg = _ch_attr("config", _config_baseline)
    mcp = _ch_attr("mcp_manager", _mcp_manager_baseline)

    path = bot._get_command_args(message) or str(cfg.BASE_DIR)
    if ".." in path and not cfg.is_valid():
        pass
    msg = await message.reply("📂 Scanning...")
    try:
        result = await mcp.list_directory(path)
        await msg.edit(f"📂 **Files in {path}:**\n\n`{result[:3900]}`")
    except (httpx.HTTPError, OSError, ValueError, KeyError, AttributeError) as e:
        await msg.edit(f"❌ Error listing: {e}")


async def handle_read(bot: "KraabUserbot", message: Message) -> None:
    """Чтение файла (!read)."""
    cfg = _ch_attr("config", _config_baseline)
    mcp = _ch_attr("mcp_manager", _mcp_manager_baseline)

    path = bot._get_command_args(message)
    if not path:
        raise UserInputError(user_message="📂 Какой файл читать? `!read <path>`")
    if not path.startswith("/"):
        path = os.path.join(cfg.BASE_DIR, path)
    msg = await message.reply("📂 Reading...")
    try:
        content = await mcp.read_file(path)
        if len(content) > 4000:
            content = content[:1000] + "\n... [truncated]"
        await msg.edit(f"📂 **Content of {os.path.basename(path)}:**\n\n```\n{content}\n```")
    except (httpx.HTTPError, OSError, ValueError, KeyError, AttributeError) as e:
        await msg.edit(f"❌ Reading error: {e}")


async def handle_write(bot: "KraabUserbot", message: Message) -> None:
    """Запись файла (!write, опасно!)."""
    cfg = _ch_attr("config", _config_baseline)
    mcp = _ch_attr("mcp_manager", _mcp_manager_baseline)

    text = bot._get_command_args(message)
    if not text:
        raise UserInputError(user_message="📂 Формат: `!write <filename> <content>`")
    parts = text.split("\n", 1)
    if len(parts) < 2:
        parts = text.split(" ", 1)
        if len(parts) < 2:
            raise UserInputError(user_message="📂 Нет контента для записи.")
    path = parts[0].strip()
    content = parts[1]
    if not path.startswith("/"):
        path = os.path.join(cfg.BASE_DIR, path)
    result = await mcp.write_file(path, content)
    await message.reply(result)


async def handle_paste(bot: "KraabUserbot", message: Message) -> None:
    """Создать текстовый paste-файл и отправить как документ (!paste).

    Поддерживает два режима:
      !paste <текст>   — создаёт файл из аргумента
      !paste (reply)  — создаёт файл из текста исходного сообщения
    """
    cfg = _ch_attr("config", _config_baseline)

    args = bot._get_command_args(message)
    reply = getattr(message, "reply_to_message", None)

    # Определяем текст для paste
    if args:
        text = args
    elif reply and getattr(reply, "text", None):
        text = reply.text
    else:
        raise UserInputError(
            user_message=(
                "📋 Формат: `!paste <текст>` или сделай reply на сообщение\n"
                "Полезно для длинных текстов >4096 символов."
            )
        )

    # Формируем имя файла
    now = datetime.datetime.now()
    filename = now.strftime("paste_%Y-%m-%d_%H-%M.txt")
    tmpdir = pathlib.Path(cfg.BASE_DIR) / ".runtime" / "pastes"
    tmpdir.mkdir(parents=True, exist_ok=True)
    filepath = tmpdir / filename

    try:
        filepath.write_text(text, encoding="utf-8")
        await bot.client.send_document(
            message.chat.id,
            str(filepath),
            caption="📋 Paste",
        )
    except (OSError, IOError) as e:
        await message.reply(f"❌ Ошибка создания paste: {e}")
    finally:
        # Удаляем временный файл после отправки
        try:
            filepath.unlink(missing_ok=True)
        except OSError:
            pass


async def handle_export(bot: "KraabUserbot", message: Message) -> None:
    """
    !export [N|all] — экспортирует историю чата в Markdown-файл.

    !export        — последние 100 сообщений (default)
    !export 200    — последние 200 сообщений
    !export all    — все сообщения (до 1000)
    """
    # Читаем через dual-namespace чтобы тесты могли патчить EXPORT_VAULT_DIR
    export_vault_dir = _ch_attr("EXPORT_VAULT_DIR", EXPORT_VAULT_DIR)
    export_default_limit = _ch_attr("EXPORT_DEFAULT_LIMIT", EXPORT_DEFAULT_LIMIT)
    export_max_limit = _ch_attr("EXPORT_MAX_LIMIT", EXPORT_MAX_LIMIT)

    # Парсим аргумент
    raw_args = (message.text or "").split(maxsplit=1)
    arg = raw_args[1].strip() if len(raw_args) > 1 else ""

    if arg.lower() == "all":
        limit = export_max_limit
    elif arg.isdigit():
        limit = min(int(arg), export_max_limit)
    elif arg == "":
        limit = export_default_limit
    else:
        await message.reply(
            "❌ Неверный аргумент. Примеры:\n`!export` / `!export 200` / `!export all`"
        )
        return

    chat = message.chat
    chat_id = chat.id
    chat_title = getattr(chat, "title", None) or getattr(chat, "first_name", None) or str(chat_id)

    status_msg = await message.reply(f"⏳ Экспортирую {limit} сообщений из «{chat_title}»…")

    try:
        # Собираем сообщения через MTProto (get_chat_history — обратный порядок, новые первые)
        raw_msgs = []
        async for msg in bot.client.get_chat_history(chat_id, limit=limit):
            raw_msgs.append(msg)
        # Разворачиваем в хронологический порядок
        raw_msgs.reverse()
    except Exception as exc:
        logger.exception("handle_export: ошибка получения истории")
        await status_msg.edit(f"❌ Ошибка получения истории: {str(exc)[:200]}")
        return

    if not raw_msgs:
        await status_msg.edit("⚠️ Нет сообщений для экспорта.")
        return

    exported_at = datetime.datetime.now()
    md_content = _render_export_markdown(chat_title, chat_id, raw_msgs, exported_at)

    # Формируем имя файла
    safe_title = _sanitize_filename(chat_title)[:60]
    date_prefix = exported_at.strftime("%Y-%m-%d")
    filename = f"{date_prefix}_{safe_title}.md"

    # Создаём директорию если не существует
    export_vault_dir.mkdir(parents=True, exist_ok=True)
    file_path = export_vault_dir / filename

    try:
        file_path.write_text(md_content, encoding="utf-8")
    except OSError as exc:
        logger.exception("handle_export: ошибка записи файла")
        await status_msg.edit(f"❌ Ошибка записи файла: {str(exc)[:200]}")
        return

    # Отправляем файл в чат
    try:
        await bot.client.send_document(
            chat_id=chat_id,
            document=str(file_path),
            caption=(
                f"📄 Экспорт чата «{chat_title}»\nСообщений: {len(raw_msgs)}\nФайл: `{filename}`"
            ),
        )
        await status_msg.delete()
    except Exception as exc:
        logger.exception("handle_export: ошибка отправки документа")
        await status_msg.edit(
            f"✅ Файл сохранён: `{file_path}`\n⚠️ Не удалось отправить документ: {str(exc)[:200]}"
        )
