# -*- coding: utf-8 -*-
"""
group_admin_commands - Phase 2 Wave 14 extraction (Session 27).

Групповые и административные команды:
  !afk, !welcome (+ handle_new_chat_members авто-handler),
  !chatmute, !slowmode, !mark, !blocked, !contacts,
  !invite, !members, !profile.

Re-exported from command_handlers.py для обратной совместимости.
"""

from __future__ import annotations

import json
import pathlib
from typing import TYPE_CHECKING

from pyrogram.types import Message

from ...core import contact_cache, telegram_resolver
from ...core.access_control import AccessLevel
from ...core.exceptions import UserInputError
from ...core.logger import get_logger

if TYPE_CHECKING:
    from ...userbot_bridge import KraabUserbot

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Welcome-config helpers
# ---------------------------------------------------------------------------

_WELCOME_FILE = pathlib.Path.home() / ".openclaw" / "krab_runtime_state" / "welcome_messages.json"
_WELCOME_TEMPLATE_VARS = "{name}, {username}, {chat}, {count}"

_WELCOME_FILE_BASELINE = _WELCOME_FILE


def _welcome_path() -> pathlib.Path:
    """Dual-namespace lookup для _WELCOME_FILE (patch surface для тестов)."""
    import sys

    self_mod = sys.modules[__name__]
    self_val = self_mod.__dict__.get("_WELCOME_FILE", _WELCOME_FILE_BASELINE)
    if self_val is not _WELCOME_FILE_BASELINE:
        return pathlib.Path(str(self_val))

    import importlib

    try:
        ch = importlib.import_module("src.handlers.command_handlers")
        ch_val = ch.__dict__.get("_WELCOME_FILE", _WELCOME_FILE_BASELINE)
        if ch_val is not _WELCOME_FILE_BASELINE:
            return pathlib.Path(str(ch_val))
    except Exception:  # noqa: BLE001
        pass
    return _WELCOME_FILE_BASELINE


def _load_welcome_config() -> dict:
    """Загружает конфиг приветствий из JSON-файла."""
    path = _welcome_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_welcome_config(data: dict) -> None:
    """Сохраняет конфиг приветствий в JSON-файл."""
    path = _welcome_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _render_welcome_text(template: str, *, name: str, username: str, chat: str, count: int) -> str:
    """Подставляет переменные в шаблон приветствия."""
    return (
        template.replace("{name}", name)
        .replace("{username}", username)
        .replace("{chat}", chat)
        .replace("{count}", str(count))
    )


# ---------------------------------------------------------------------------
# Slowmode helpers
# ---------------------------------------------------------------------------

_SLOWMODE_VALID = {0, 10, 30, 60, 300, 900, 3600}
_SLOWMODE_LABELS: dict[int, str] = {
    0: "выключен",
    10: "10 сек",
    30: "30 сек",
    60: "1 мин",
    300: "5 мин",
    900: "15 мин",
    3600: "1 час",
}

# ---------------------------------------------------------------------------
# Chatmute constant
# ---------------------------------------------------------------------------

# Таймаут mute: int32 max (~68 лет) — «навсегда»
_MUTE_FOREVER_UNTIL: int = 2_147_483_647


# ---------------------------------------------------------------------------
# !welcome
# ---------------------------------------------------------------------------


async def handle_welcome(bot: "KraabUserbot", message: Message) -> None:
    """
    !welcome — управление автоприветствием новых участников группы.

    Синтаксис:
      !welcome set <текст>   — установить шаблон (доступны: {name}, {username}, {chat}, {count})
      !welcome off           — выключить приветствие для этого чата
      !welcome status        — показать текущий шаблон и статус
      !welcome test          — отправить тестовое приветствие (preview)
    """
    chat_id = str(message.chat.id)
    raw = (message.text or "").strip()
    parts = raw.split(maxsplit=2)
    sub = parts[1].strip().lower() if len(parts) >= 2 else "status"

    cfg = _load_welcome_config()

    if sub == "set":
        if len(parts) < 3 or not parts[2].strip():
            raise UserInputError(
                user_message=(
                    "❌ Укажи текст приветствия.\n\n"
                    "Пример: `!welcome set Привет, {name}! Добро пожаловать в {chat}!`\n\n"
                    f"Доступные переменные: `{_WELCOME_TEMPLATE_VARS}`"
                )
            )
        template = parts[2].strip()
        cfg[chat_id] = {"enabled": True, "template": template}
        _save_welcome_config(cfg)
        await message.reply(
            f"✅ Приветствие для этого чата установлено:\n\n_{template}_\n\n"
            f"Переменные: `{_WELCOME_TEMPLATE_VARS}`\n"
            "`!welcome test` — проверить, `!welcome off` — выключить"
        )
        return

    if sub == "off":
        if chat_id in cfg:
            cfg[chat_id]["enabled"] = False
            _save_welcome_config(cfg)
        await message.reply("🔇 Автоприветствие для этого чата **выключено**.")
        return

    if sub in ("status", "show"):
        entry = cfg.get(chat_id)
        if not entry or not entry.get("enabled"):
            await message.reply(
                "ℹ️ Автоприветствие в этом чате **не настроено** или выключено.\n"
                f"`!welcome set <текст>` — установить\n"
                f"Переменные: `{_WELCOME_TEMPLATE_VARS}`"
            )
        else:
            await message.reply(
                f"✅ Автоприветствие **включено**:\n\n_{entry['template']}_\n\n"
                "`!welcome off` — выключить | `!welcome test` — preview"
            )
        return

    if sub == "test":
        entry = cfg.get(chat_id)
        if not entry or not entry.get("enabled"):
            raise UserInputError(
                user_message="❌ Приветствие не настроено. Сначала: `!welcome set <текст>`"
            )
        user = message.from_user
        name = getattr(user, "first_name", None) or "Новичок"
        uname = f"@{user.username}" if getattr(user, "username", None) else name
        chat_title = getattr(message.chat, "title", None) or "этом чате"
        preview = _render_welcome_text(
            entry["template"],
            name=name,
            username=uname,
            chat=chat_title,
            count=1,
        )
        await message.reply(f"🧪 **Preview приветствия:**\n\n{preview}")
        return

    raise UserInputError(
        user_message=(
            "❌ Неизвестная подкоманда.\n\n"
            "Доступно:\n"
            "`!welcome set <текст>` — установить\n"
            "`!welcome off` — выключить\n"
            "`!welcome status` — показать\n"
            "`!welcome test` — preview"
        )
    )


# ---------------------------------------------------------------------------
# handle_new_chat_members — auto-event handler (не команда пользователя)
# ---------------------------------------------------------------------------


async def handle_new_chat_members(bot: "KraabUserbot", message: Message) -> None:
    """Автоприветствие новых участников — вызывается на filters.new_chat_members."""
    chat_id = str(message.chat.id)
    cfg = _load_welcome_config()
    entry = cfg.get(chat_id)
    if not entry or not entry.get("enabled") or not entry.get("template"):
        return

    chat_title = getattr(message.chat, "title", None) or str(chat_id)
    new_members = getattr(message, "new_chat_members", None) or []
    count = len(new_members)

    for member in new_members:
        name = getattr(member, "first_name", None) or "Новичок"
        uname = f"@{member.username}" if getattr(member, "username", None) else name
        text = _render_welcome_text(
            entry["template"],
            name=name,
            username=uname,
            chat=chat_title,
            count=count,
        )
        try:
            await message.reply(text)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "welcome_send_failed",
                chat_id=chat_id,
                error=str(exc),
                error_type=type(exc).__name__,
            )


# ---------------------------------------------------------------------------
# !afk
# ---------------------------------------------------------------------------


async def handle_afk(bot: "KraabUserbot", message: Message) -> None:
    """!afk [причина] / !afk off / !afk status / !back — режим отсутствия.

    Синтаксис:
      !afk               — включить AFK (без причины)
      !afk <причина>     — включить AFK с причиной
      !afk off           — выключить AFK
      !back              — выключить AFK (алиас)
      !afk status        — показать текущий статус
    """
    import time as _time  # noqa: PLC0415

    raw = (message.text or "").strip()
    parts = raw.split(maxsplit=1)
    cmd_word = parts[0].lstrip("!/. ").lower()
    args = parts[1].strip() if len(parts) > 1 else ""

    # !back — всегда выключение
    if cmd_word == "back":
        if not bot._afk_mode:
            await message.reply("ℹ️ AFK-режим и так не активен.")
            return
        elapsed = int(_time.time() - bot._afk_since)
        mins = elapsed // 60
        secs = elapsed % 60
        time_str = f"{mins} мин {secs} с" if mins else f"{secs} с"
        bot._afk_mode = False
        bot._afk_reason = ""
        bot._afk_since = 0.0
        bot._afk_replied_chats.clear()
        await message.reply(f"👋 Добро пожаловать обратно! Отсутствовал: **{time_str}**")
        return

    # !afk off / !afk стоп
    if args.lower() in ("off", "стоп", "выкл", "выключить"):
        if not bot._afk_mode:
            await message.reply("ℹ️ AFK-режим и так не активен.")
            return
        elapsed = int(_time.time() - bot._afk_since)
        mins = elapsed // 60
        secs = elapsed % 60
        time_str = f"{mins} мин {secs} с" if mins else f"{secs} с"
        bot._afk_mode = False
        bot._afk_reason = ""
        bot._afk_since = 0.0
        bot._afk_replied_chats.clear()
        await message.reply(f"👋 AFK выключен. Отсутствовал: **{time_str}**")
        return

    # !afk status / !afk статус
    if args.lower() in ("status", "статус", "stat"):
        if not bot._afk_mode:
            await message.reply("ℹ️ AFK-режим не активен.")
        else:
            elapsed = int(_time.time() - bot._afk_since)
            mins = elapsed // 60
            secs = elapsed % 60
            time_str = f"{mins} мин {secs} с" if mins else f"{secs} с"
            reason_part = f"\n📝 Причина: {bot._afk_reason}" if bot._afk_reason else ""
            replied_count = len(bot._afk_replied_chats)
            await message.reply(
                f"🌙 **AFK активен** — отсутствую уже **{time_str}**{reason_part}\n"
                f"Автоответ отправлен в {replied_count} чат(ах)."
            )
        return

    # !afk [причина] — включить (или обновить причину если уже активен)
    if bot._afk_mode:
        bot._afk_reason = args
        reason_part = f"\n📝 Причина обновлена: {args}" if args else " Причина сброшена."
        await message.reply(f"🌙 AFK уже активен.{reason_part}")
        return

    bot._afk_mode = True
    bot._afk_reason = args
    bot._afk_since = _time.time()
    bot._afk_replied_chats.clear()
    reason_part = f"\n📝 Причина: {args}" if args else ""
    await message.reply(
        f"🌙 AFK-режим включён.{reason_part}\n"
        f"Входящие DM получат автоответ.\n"
        f"`!afk off` или `!back` — вернуться."
    )


# ---------------------------------------------------------------------------
# !mark
# ---------------------------------------------------------------------------


async def handle_mark(bot: "KraabUserbot", message: Message) -> None:
    """
    Управление статусом прочитанности чатов.

    Подкоманды:
        !mark read     — пометить текущий чат как прочитанный
        !mark unread   — пометить текущий чат как непрочитанный
        !mark readall  — пометить ВСЕ чаты как прочитанные

    Owner-only.
    """
    access_profile = bot._get_access_profile(message.from_user)
    if access_profile.level != AccessLevel.OWNER:
        raise UserInputError(user_message="🔒 `!mark` доступен только владельцу.")

    subcmd = bot._get_command_args(message).strip().lower()

    async def _reply(text: str) -> None:
        """Редактирует если сообщение от self, иначе отвечает."""
        if message.from_user and message.from_user.id == bot.me.id:
            await message.edit(text)
        else:
            await message.reply(text)

    if subcmd == "read":
        try:
            await bot.client.read_chat_history(chat_id=message.chat.id)
            await _reply("✅ Чат помечен как прочитанный.")
        except Exception as exc:  # noqa: BLE001
            await _reply(f"❌ Не удалось пометить как прочитанный: `{exc}`")

    elif subcmd == "unread":
        try:
            await bot.client.mark_chat_unread(chat_id=message.chat.id)
            await _reply("🔵 Чат помечен как непрочитанный.")
        except Exception as exc:  # noqa: BLE001
            await _reply(f"❌ Не удалось пометить как непрочитанный: `{exc}`")

    elif subcmd == "readall":
        success_count = 0
        fail_count = 0
        try:
            async for dialog in bot.client.get_dialogs():
                try:
                    await bot.client.read_chat_history(chat_id=dialog.chat.id)
                    success_count += 1
                except Exception:  # noqa: BLE001
                    fail_count += 1

            result = f"✅ Все чаты помечены как прочитанные ({success_count} чатов)."
            if fail_count:
                result += f"\n⚠️ Не удалось обработать: {fail_count}."
            await _reply(result)
        except Exception as exc:  # noqa: BLE001
            await _reply(f"❌ Ошибка при получении диалогов: `{exc}`")

    else:
        raise UserInputError(
            user_message=(
                "📖 **Управление статусом прочтения**\n\n"
                "`!mark read` — пометить текущий чат как прочитанный\n"
                "`!mark unread` — пометить как непрочитанный\n"
                "`!mark readall` — пометить ВСЕ чаты как прочитанные"
            )
        )


# ---------------------------------------------------------------------------
# !slowmode
# ---------------------------------------------------------------------------


async def handle_slowmode(bot: "KraabUserbot", message: Message) -> None:
    """!slowmode — управление slowmode в группе (требует прав администратора).

    Синтаксис:
      !slowmode <seconds>  — установить задержку (0, 10, 30, 60, 300, 900, 3600)
      !slowmode off        — выключить slowmode (= 0 секунд)
      !slowmode status     — показать текущий slowmode чата
    """
    chat = message.chat
    if chat.type.name not in ("GROUP", "SUPERGROUP", "CHANNEL"):
        raise UserInputError(user_message="❌ Slowmode доступен только в группах и каналах.")

    raw_text = (message.text or "").strip()
    parts = raw_text.split(maxsplit=1)
    arg = parts[1].strip().lower() if len(parts) > 1 else ""

    if not arg or arg == "status":
        try:
            full_chat = await bot.client.get_chat(chat.id)
            delay = getattr(full_chat, "slow_mode_delay", None) or 0
            label = _SLOWMODE_LABELS.get(delay, f"{delay} сек")
            await message.reply(
                f"🐢 **Slowmode** в `{chat.title or chat.id}`\n"
                f"Текущее значение: **{label}**\n\n"
                f"Допустимые значения: 0, 10, 30, 60, 300, 900, 3600\n"
                f"`!slowmode <сек>` — установить | `!slowmode off` — выключить"
            )
        except Exception as exc:  # noqa: BLE001
            raise UserInputError(
                user_message=f"❌ Не удалось получить информацию о чате: {exc}"
            ) from exc
        return

    if arg in ("off", "выкл", "0"):
        seconds = 0
    else:
        if not arg.isdigit():
            raise UserInputError(
                user_message=(
                    "❌ Неверный аргумент. Используй:\n"
                    "`!slowmode <сек>` — 0, 10, 30, 60, 300, 900, 3600\n"
                    "`!slowmode off` — выключить\n"
                    "`!slowmode status` — текущее значение"
                )
            )
        seconds = int(arg)
        if seconds not in _SLOWMODE_VALID:
            raise UserInputError(
                user_message=(
                    f"❌ Недопустимое значение `{seconds}`.\n"
                    f"Telegram принимает: **0, 10, 30, 60, 300, 900, 3600**"
                )
            )

    try:
        await bot.client.set_slow_mode(chat.id, seconds)
    except Exception as exc:  # noqa: BLE001
        err_str = str(exc)
        if "CHAT_ADMIN_REQUIRED" in err_str or "admin" in err_str.lower():
            raise UserInputError(
                user_message="❌ Нет прав администратора для управления slowmode."
            ) from exc
        raise UserInputError(user_message=f"❌ Ошибка установки slowmode: {exc}") from exc

    label = _SLOWMODE_LABELS.get(seconds, f"{seconds} сек")
    if seconds == 0:
        await message.reply(f"✅ Slowmode **выключен** в `{chat.title or chat.id}`.")
    else:
        await message.reply(f"🐢 Slowmode установлен: **{label}** в `{chat.title or chat.id}`.")


# ---------------------------------------------------------------------------
# !chatmute
# ---------------------------------------------------------------------------


async def handle_chatmute(bot: "KraabUserbot", message: Message) -> None:
    """Управление Telegram-уведомлениями текущего чата через MTProto.

    Команды:
      !chatmute off     — отключить уведомления (mute навсегда)
      !chatmute on      — включить уведомления
      !chatmute status  — показать текущий статус
      !chatmute         — показать справку
    """
    from pyrogram import raw as _raw  # noqa: PLC0415

    args = bot._get_command_args(message).strip().lower()
    chat_id = message.chat.id

    async def _get_peer_settings() -> dict:
        """Получить текущие настройки уведомлений для чата."""
        try:
            peer = await bot.client.resolve_peer(chat_id)
            notify_peer = _raw.types.InputNotifyPeer(peer=peer)
            result = await bot.client.invoke(
                _raw.functions.account.GetNotifySettings(peer=notify_peer)
            )
            return {
                "mute_until": getattr(result, "mute_until", 0) or 0,
            }
        except Exception:  # noqa: BLE001
            return {"mute_until": 0}

    if args in {"off", "mute", "выкл", "тихо"}:
        try:
            peer = await bot.client.resolve_peer(chat_id)
            notify_peer = _raw.types.InputNotifyPeer(peer=peer)
            settings = _raw.types.InputPeerNotifySettings(
                mute_until=_MUTE_FOREVER_UNTIL,
                silent=True,
            )
            await bot.client.invoke(
                _raw.functions.account.UpdateNotifySettings(peer=notify_peer, settings=settings)
            )
            await message.reply(
                "🔕 Уведомления в этом чате **отключены**.\n`!chatmute on` — включить обратно."
            )
        except Exception as exc:  # noqa: BLE001
            raise UserInputError(
                user_message=f"❌ Не удалось отключить уведомления: {exc}"
            ) from exc

    elif args in {"on", "unmute", "вкл", "громко"}:
        try:
            peer = await bot.client.resolve_peer(chat_id)
            notify_peer = _raw.types.InputNotifyPeer(peer=peer)
            settings = _raw.types.InputPeerNotifySettings(
                mute_until=0,
                silent=False,
            )
            await bot.client.invoke(
                _raw.functions.account.UpdateNotifySettings(peer=notify_peer, settings=settings)
            )
            await message.reply("🔔 Уведомления в этом чате **включены**.")
        except Exception as exc:  # noqa: BLE001
            raise UserInputError(user_message=f"❌ Не удалось включить уведомления: {exc}") from exc

    elif args in {"status", "статус"}:
        import time as _time_mod  # noqa: PLC0415

        s = await _get_peer_settings()
        mute_until = s.get("mute_until", 0)
        now_ts = int(_time_mod.time())

        if mute_until and mute_until > now_ts:
            if mute_until >= _MUTE_FOREVER_UNTIL:
                status_line = "🔕 **Заглушён** (навсегда)"
            else:
                import datetime as _dt_mod  # noqa: PLC0415

                dt_until = _dt_mod.datetime.fromtimestamp(mute_until)
                status_line = f"🔕 **Заглушён** до {dt_until.strftime('%d.%m.%Y %H:%M')}"
        else:
            status_line = "🔔 **Уведомления включены**"

        await message.reply(
            f"📢 Статус уведомлений чата:\n{status_line}\n\n"
            "`!chatmute off` — отключить\n"
            "`!chatmute on`  — включить"
        )

    else:
        await message.reply(
            "📢 **Управление уведомлениями чата**\n\n"
            "`!chatmute off`    — отключить уведомления\n"
            "`!chatmute on`     — включить уведомления\n"
            "`!chatmute status` — текущий статус"
        )


# ---------------------------------------------------------------------------
# !contacts
# ---------------------------------------------------------------------------

_CONTACTS_HELP = (
    "📒 **Кэш контактов** (owner-only)\n\n"
    "`!contacts` / `!contacts list` — все записи в кэше\n"
    "`!contacts search <запрос>` — нечёткий поиск по имени/alias\n"
    "`!contacts alias <@user> <псевдоним>` — добавить alias контакту\n"
    "`!contacts resolve <@user|t.me/...>` — разрезолвить и кэшировать peer"
)


def _fmt_contact_entry(entry: dict) -> str:
    """Форматирует одну запись кэша контактов в строку."""
    uname = entry.get("username") or "—"
    dn = entry.get("display_name") or "—"
    pid = entry.get("peer_id", "?")
    aliases = entry.get("aliases") or []
    alias_str = f" | aliases: {', '.join(aliases)}" if aliases else ""
    return f"• **{dn}** @{uname} | `{pid}`{alias_str}"


async def handle_contacts(bot: "KraabUserbot", message: Message) -> None:
    """
    Управление кэшем контактов. Owner-only.

    Синтаксис:
      !contacts [list]                       — все записи в кэше
      !contacts search <запрос>              — нечёткий поиск по display_name/alias
      !contacts alias <@user|id> <псевдоним> — добавить человеческий alias
      !contacts resolve <@user|t.me/...>     — разрезолвить peer и добавить в кэш
    """
    access_profile = bot._get_access_profile(message.from_user)
    if access_profile.level != AccessLevel.OWNER:
        raise UserInputError(user_message="🔒 `!contacts` доступен только владельцу.")

    args = bot._get_command_args(message).strip()

    # --- list (или без аргументов) ---
    if not args or args.lower() == "list":
        entries = contact_cache.list_all()
        if not entries:
            await message.reply("📒 Кэш контактов пуст.\n\n" + _CONTACTS_HELP)
            return
        lines = [f"📒 **Кэш контактов** — {len(entries)} записей:\n"]
        for e in entries[:30]:
            lines.append(_fmt_contact_entry(e))
        if len(entries) > 30:
            lines.append(f"\n_… и ещё {len(entries) - 30}_")
        await message.reply("\n".join(lines))
        return

    parts = args.split(maxsplit=1)
    subcmd = parts[0].lower()
    rest = parts[1].strip() if len(parts) > 1 else ""

    # --- search ---
    if subcmd == "search":
        if not rest:
            raise UserInputError(
                user_message="🔍 Укажи запрос: `!contacts search <имя или псевдоним>`"
            )
        results = contact_cache.search(rest)
        if not results:
            await message.reply(f"📭 Ничего не найдено в кэше по запросу: `{rest}`")
            return
        lines = [f"🔍 **Поиск** (`{rest}`) — {len(results)}:\n"]
        for e in results[:20]:
            lines.append(_fmt_contact_entry(e))
        if len(results) > 20:
            lines.append(f"\n_… и ещё {len(results) - 20}_")
        await message.reply("\n".join(lines))
        return

    # --- alias ---
    if subcmd == "alias":
        if not rest:
            raise UserInputError(
                user_message=(
                    "📝 Формат: `!contacts alias <@username или peer_id> <псевдоним>`\n"
                    "Пример: `!contacts alias @vasya Вася из армии`"
                )
            )
        alias_parts = rest.split(maxsplit=1)
        if len(alias_parts) < 2:
            raise UserInputError(
                user_message=(
                    "📝 Укажи @username (или peer_id) и псевдоним.\n"
                    "Пример: `!contacts alias @vasya Вася из армии`"
                )
            )
        target_raw, alias_text = alias_parts[0].strip(), alias_parts[1].strip()
        if not alias_text:
            raise UserInputError(user_message="📝 Псевдоним не может быть пустым.")

        # Ищем peer_id — сначала в кэше, затем резолвим
        cached = contact_cache.lookup(target_raw)
        if cached:
            peer_id = cached["peer_id"]
        else:
            # Пробуем извлечь числовой peer_id напрямую
            stripped = target_raw.lstrip("@")
            if stripped.lstrip("-").isdigit():
                peer_id = int(stripped)
                # Нужно иметь запись в кэше для add_alias; если её нет — создаём stub
                contact_cache.store(stripped, peer_id, stripped)
            else:
                # Резолвим через Telegram
                result = await telegram_resolver.resolve_peer(bot.client, target_raw)
                if not result.get("ok"):
                    await message.reply(
                        f"❌ Не удалось разрезолвить `{target_raw}`.\n"
                        f"Попробуй сначала `!contacts resolve {target_raw}`."
                    )
                    return
                peer_id = result["peer_id"]

        ok = contact_cache.add_alias(peer_id, alias_text)
        if ok:
            await message.reply(f"✅ Псевдоним **«{alias_text}»** добавлен к peer `{peer_id}`.")
        else:
            await message.reply(
                f"⚠️ Контакт `{target_raw}` (peer_id={peer_id}) не найден в кэше.\n"
                f"Сначала выполни `!contacts resolve {target_raw}`."
            )
        return

    # --- resolve ---
    if subcmd == "resolve":
        if not rest:
            raise UserInputError(
                user_message=(
                    "🔗 Укажи цель: `!contacts resolve <@username|t.me/username|peer_id>`"
                )
            )
        result = await telegram_resolver.resolve_peer(bot.client, rest)
        if not result.get("ok"):
            tried = ", ".join(result.get("tried_strategies") or [])
            suggestions = "\n".join(f"  — {s}" for s in (result.get("suggestions") or []))
            await message.reply(
                f"❌ Не удалось разрезолвить `{rest}`.\n"
                f"Стратегии: {tried or '—'}\n"
                f"Советы:\n{suggestions or '  нет'}"
            )
            return

        peer_id = result["peer_id"]
        username = result.get("username") or rest.lstrip("@").split("/")[-1]
        display_name = result.get("display_name") or username
        strategy = result.get("strategy_used") or "?"
        await message.reply(
            f"✅ **Разрезолвлено** (`{strategy}`):\n\n"
            f"**Имя:** {display_name}\n"
            f"**Username:** @{username}\n"
            f"**Peer ID:** `{peer_id}`\n\n"
            f"_Контакт добавлен в кэш._"
        )
        return

    raise UserInputError(user_message=_CONTACTS_HELP)


# ---------------------------------------------------------------------------
# !invite
# ---------------------------------------------------------------------------


async def handle_invite(bot: "KraabUserbot", message: Message) -> None:
    """
    Управление приглашениями в группу. Owner-only.

    Форматы:
      !invite @username             — добавить пользователя в текущую группу
      !invite link                  — создать invite link
      !invite link revoke <url>     — отозвать invite link
    """
    access_profile = bot._get_access_profile(message.from_user)
    if access_profile.level != AccessLevel.OWNER:
        raise UserInputError(user_message="🔒 `!invite` доступен только владельцу.")

    chat_id = message.chat.id
    args_raw = message.command[1:] if message.command else []

    if not args_raw:
        raise UserInputError(
            user_message=(
                "👥 **Приглашение в группу**\n\n"
                "`!invite @username` — добавить пользователя в текущую группу\n"
                "`!invite link` — создать пригласительную ссылку\n"
                "`!invite link revoke <url>` — отозвать ссылку"
            )
        )

    subcmd = args_raw[0].lower()

    if subcmd == "link":
        if len(args_raw) >= 2 and args_raw[1].lower() == "revoke":
            if len(args_raw) < 3:
                raise UserInputError(user_message="❌ Укажи ссылку: `!invite link revoke <url>`")
            link_url = args_raw[2]
            try:
                revoked = await bot.client.revoke_chat_invite_link(chat_id, link_url)
                await message.reply(f"🔒 Ссылка отозвана:\n`{revoked.invite_link}`")
            except Exception as exc:  # noqa: BLE001
                raise UserInputError(
                    user_message=f"❌ Не удалось отозвать ссылку: `{exc}`"
                ) from exc
            return

        try:
            link = await bot.client.create_chat_invite_link(chat_id)
            await message.reply(f"🔗 **Пригласительная ссылка:**\n`{link.invite_link}`")
        except Exception as exc:  # noqa: BLE001
            raise UserInputError(user_message=f"❌ Не удалось создать ссылку: `{exc}`") from exc
        return

    target = args_raw[0]
    try:
        await bot.client.add_chat_members(chat_id, target)
        await message.reply(f"✅ Пользователь `{target}` добавлен в чат.")
    except Exception as exc:  # noqa: BLE001
        raise UserInputError(user_message=f"❌ Не удалось добавить `{target}`: `{exc}`") from exc


# ---------------------------------------------------------------------------
# !blocked
# ---------------------------------------------------------------------------


async def handle_blocked(bot: "KraabUserbot", message: Message) -> None:
    """Управление заблокированными пользователями (userbot-only).

    Подкоманды:
      !blocked list             — список заблокированных
      !blocked add              — заблокировать автора reply-сообщения
      !blocked add @username    — заблокировать по username или user_id
      !blocked remove @username — разблокировать по username или user_id
    """
    args_raw = bot._get_command_args(message).strip()
    parts = args_raw.split(maxsplit=1)
    sub = parts[0].lower() if parts else ""
    arg = parts[1].strip() if len(parts) > 1 else ""

    if sub in {"list", "список", "ls", ""}:
        lines: list[str] = []
        try:
            async for user in bot.client.get_blocked():
                name = user.first_name or ""
                if user.last_name:
                    name = f"{name} {user.last_name}".strip()
                username_part = f" (@{user.username})" if user.username else ""
                lines.append(f"• `{user.id}` — {name}{username_part}")
        except Exception as exc:  # noqa: BLE001
            raise UserInputError(
                user_message=f"❌ Не удалось получить список заблокированных: {exc}"
            ) from exc

        if not lines:
            await message.reply("✅ Список заблокированных пуст.")
        else:
            text = "🚫 **Заблокированные пользователи**\n\n" + "\n".join(lines)
            await message.reply(text)
        return

    if sub in {"add", "ban", "block", "заблок"}:
        target_id: int | str | None = None

        if message.reply_to_message and not arg:
            replied = message.reply_to_message
            if replied.from_user:
                target_id = replied.from_user.id
            elif replied.sender_chat:
                target_id = replied.sender_chat.id
            else:
                raise UserInputError(user_message="❌ Не могу определить автора сообщения.")
        elif arg:
            raw = arg.lstrip("@")
            try:
                target_id = int(raw)
            except ValueError:
                target_id = raw
        else:
            raise UserInputError(
                user_message=(
                    "❌ Укажи цель: ответь на сообщение или передай `@username` / `user_id`.\n"
                    "Пример: `!blocked add @username`"
                )
            )

        try:
            await bot.client.block_user(target_id)
        except Exception as exc:  # noqa: BLE001
            raise UserInputError(
                user_message=f"❌ Не удалось заблокировать `{target_id}`: {exc}"
            ) from exc

        await message.reply(f"🚫 Пользователь `{target_id}` заблокирован.")
        return

    if sub in {"remove", "unblock", "del", "rm", "разблок"}:
        if not arg:
            raise UserInputError(
                user_message=(
                    "❌ Укажи пользователя: `!blocked remove @username` или `!blocked remove <user_id>`."
                )
            )
        raw = arg.lstrip("@")
        try:
            target_id = int(raw)
        except ValueError:
            target_id = raw

        try:
            await bot.client.unblock_user(target_id)
        except Exception as exc:  # noqa: BLE001
            raise UserInputError(
                user_message=f"❌ Не удалось разблокировать `{target_id}`: {exc}"
            ) from exc

        await message.reply(f"✅ Пользователь `{target_id}` разблокирован.")
        return

    await message.reply(
        "🚫 **Управление заблокированными**\n\n"
        "`!blocked list`             — список заблокированных\n"
        "`!blocked add` _(reply)_    — заблокировать автора сообщения\n"
        "`!blocked add @username`    — заблокировать по username/ID\n"
        "`!blocked remove @username` — разблокировать"
    )


# ---------------------------------------------------------------------------
# !profile
# ---------------------------------------------------------------------------


async def handle_profile(bot: "KraabUserbot", message: Message) -> None:
    """!profile — управление профилем userbot-аккаунта (owner-only).

    Синтаксис:
      !profile                         — показать текущий профиль
      !profile bio <текст>             — установить bio
      !profile name <first> [last]     — изменить имя
      !profile username <username>     — изменить username
    """
    access_profile = bot._get_access_profile(message.from_user)
    if access_profile.level != AccessLevel.OWNER:
        raise UserInputError(user_message="🔒 Команда доступна только владельцу.")

    raw = (message.text or "").strip()
    parts = raw.split(maxsplit=2)
    sub = parts[1].strip().lower() if len(parts) > 1 else ""

    if not sub:
        try:
            me = await bot.client.get_me()
        except Exception as exc:  # noqa: BLE001
            raise UserInputError(user_message=f"❌ Не удалось получить профиль: {exc}") from exc

        first = me.first_name or ""
        last = me.last_name or ""
        full_name = f"{first} {last}".strip()
        username = f"@{me.username}" if me.username else "—"
        user_id = me.id
        bio = getattr(me, "bio", None) or "—"
        photo_count = 0
        try:
            async for _ in bot.client.get_chat_photos("me"):
                photo_count += 1
        except Exception:  # noqa: BLE001
            photo_count = 0

        lines = [
            "👤 **Профиль аккаунта**",
            "",
            f"**Имя:** {full_name}",
            f"**Username:** {username}",
            f"**ID:** `{user_id}`",
            f"**Bio:** {bio}",
            f"**Фото:** {photo_count}",
            "",
            "`!profile bio <текст>` — изменить bio",
            "`!profile name <first> [last]` — изменить имя",
            "`!profile username <username>` — изменить username",
        ]
        await message.reply("\n".join(lines))
        return

    if sub == "bio":
        bio_text = parts[2].strip() if len(parts) > 2 else ""
        if not bio_text:
            raise UserInputError(user_message="❌ Укажи текст bio: `!profile bio <текст>`")
        try:
            await bot.client.update_profile(bio=bio_text)
        except Exception as exc:  # noqa: BLE001
            raise UserInputError(user_message=f"❌ Не удалось обновить bio: {exc}") from exc
        await message.reply(f"✅ Bio обновлено:\n{bio_text}")
        logger.info("handle_profile_bio_updated", length=len(bio_text))
        return

    if sub == "name":
        name_args = parts[2].strip() if len(parts) > 2 else ""
        if not name_args:
            raise UserInputError(user_message="❌ Укажи имя: `!profile name <first> [last]`")
        name_parts = name_args.split(maxsplit=1)
        first_name = name_parts[0]
        last_name = name_parts[1] if len(name_parts) > 1 else ""
        try:
            await bot.client.update_profile(
                first_name=first_name,
                last_name=last_name,
            )
        except Exception as exc:  # noqa: BLE001
            raise UserInputError(user_message=f"❌ Не удалось обновить имя: {exc}") from exc
        full = f"{first_name} {last_name}".strip()
        await message.reply(f"✅ Имя обновлено: **{full}**")
        logger.info("handle_profile_name_updated", first=first_name, last=last_name)
        return

    if sub == "username":
        uname = parts[2].strip().lstrip("@") if len(parts) > 2 else ""
        if not uname:
            raise UserInputError(user_message="❌ Укажи username: `!profile username <username>`")
        try:
            await bot.client.update_username(uname)
        except Exception as exc:  # noqa: BLE001
            raise UserInputError(user_message=f"❌ Не удалось обновить username: {exc}") from exc
        await message.reply(f"✅ Username обновлён: @{uname}")
        logger.info("handle_profile_username_updated", username=uname)
        return

    raise UserInputError(
        user_message=(
            "👤 **!profile — управление профилем**\n\n"
            "`!profile` — показать текущий профиль\n"
            "`!profile bio <текст>` — установить bio\n"
            "`!profile name <first> [last]` — изменить имя\n"
            "`!profile username <username>` — изменить username"
        )
    )


# ---------------------------------------------------------------------------
# !members
# ---------------------------------------------------------------------------


async def handle_members(bot: "KraabUserbot", message: Message) -> None:
    """Управление участниками группы.

    Команды:
      !members                  — количество участников
      !members list [N]         — список последних N участников (по умолчанию 10)
      !members kick             — кикнуть автора сообщения (reply)
      !members ban              — забанить автора сообщения (reply)
      !members unban @username  — разбанить пользователя по @username или user_id
    """
    chat = message.chat
    if chat.type.name not in ("GROUP", "SUPERGROUP"):
        raise UserInputError(user_message="❌ Команда `!members` работает только в группах.")

    raw_text = (message.text or "").strip()
    parts = raw_text.split(maxsplit=2)
    sub = parts[1].strip().lower() if len(parts) > 1 else ""

    if not sub:
        try:
            count = await bot.client.get_chat_members_count(chat.id)
        except Exception as exc:  # noqa: BLE001
            raise UserInputError(
                user_message=f"❌ Не удалось получить количество участников: {exc}"
            ) from exc
        await message.reply(f"👥 Участников в `{chat.title or chat.id}`: **{count}**")
        return

    if sub == "list":
        limit = 10
        if len(parts) > 2:
            try:
                limit = int(parts[2].strip())
                if limit < 1:
                    raise ValueError
                limit = min(limit, 200)
            except ValueError:
                raise UserInputError(user_message="❌ Укажи число участников: `!members list 20`")

        try:
            members_list = []
            async for m in bot.client.get_chat_members(chat.id, limit=limit):
                user = m.user
                if user is None or user.is_deleted:
                    continue
                name = user.first_name or ""
                if user.last_name:
                    name = f"{name} {user.last_name}".strip()
                username = f"@{user.username}" if user.username else f"id{user.id}"
                members_list.append(f"• {name} ({username})")
        except Exception as exc:  # noqa: BLE001
            raise UserInputError(
                user_message=f"❌ Не удалось получить список участников: {exc}"
            ) from exc

        if not members_list:
            await message.reply("❌ Список участников пуст или недоступен.")
            return

        header = f"👥 **Участники** `{chat.title or chat.id}` (последние {len(members_list)}):\n\n"
        body = "\n".join(members_list)
        full = header + body
        if len(full) <= 4096:
            await message.reply(full)
        else:
            await message.reply(header + body[:4000] + "\n…")
        return

    if sub == "kick":
        replied = getattr(message, "reply_to_message", None)
        if replied is None or replied.from_user is None:
            raise UserInputError(
                user_message="❌ Ответь на сообщение участника которого хочешь кикнуть."
            )
        target = replied.from_user
        if target.is_bot:
            raise UserInputError(user_message="❌ Нельзя кикнуть бота этой командой.")
        try:
            await bot.client.ban_chat_member(chat.id, target.id)
            await bot.client.unban_chat_member(chat.id, target.id)
        except Exception as exc:  # noqa: BLE001
            err_str = str(exc)
            if "CHAT_ADMIN_REQUIRED" in err_str or "admin" in err_str.lower():
                raise UserInputError(
                    user_message="❌ Нет прав администратора для кика участников."
                ) from exc
            raise UserInputError(user_message=f"❌ Не удалось кикнуть участника: {exc}") from exc
        name = target.first_name or str(target.id)
        await message.reply(f"👟 **{name}** кикнут из `{chat.title or chat.id}`.")
        return

    if sub == "ban":
        replied = getattr(message, "reply_to_message", None)
        if replied is None or replied.from_user is None:
            raise UserInputError(
                user_message="❌ Ответь на сообщение участника которого хочешь забанить."
            )
        target = replied.from_user
        if target.is_bot:
            raise UserInputError(user_message="❌ Нельзя забанить бота этой командой.")
        try:
            await bot.client.ban_chat_member(chat.id, target.id)
        except Exception as exc:  # noqa: BLE001
            err_str = str(exc)
            if "CHAT_ADMIN_REQUIRED" in err_str or "admin" in err_str.lower():
                raise UserInputError(
                    user_message="❌ Нет прав администратора для бана участников."
                ) from exc
            raise UserInputError(user_message=f"❌ Не удалось забанить участника: {exc}") from exc
        name = target.first_name or str(target.id)
        await message.reply(f"🔨 **{name}** забанен в `{chat.title or chat.id}`.")
        return

    if sub == "unban":
        if len(parts) < 3 or not parts[2].strip():
            raise UserInputError(
                user_message="❌ Укажи пользователя: `!members unban @username` или `!members unban 12345`"
            )
        target_str = parts[2].strip()
        if target_str.startswith("@"):
            target_ref: int | str = target_str
        else:
            try:
                target_ref = int(target_str)
            except ValueError:
                target_ref = target_str

        try:
            await bot.client.unban_chat_member(chat.id, target_ref)
        except Exception as exc:  # noqa: BLE001
            err_str = str(exc)
            if "CHAT_ADMIN_REQUIRED" in err_str or "admin" in err_str.lower():
                raise UserInputError(
                    user_message="❌ Нет прав администратора для разбана участников."
                ) from exc
            raise UserInputError(
                user_message=f"❌ Не удалось разбанить пользователя: {exc}"
            ) from exc
        await message.reply(f"✅ Пользователь `{target_str}` разбанен в `{chat.title or chat.id}`.")
        return

    raise UserInputError(
        user_message=(
            "👥 **Управление участниками группы**\n\n"
            "`!members`              — количество участников\n"
            "`!members list [N]`     — список последних N участников\n"
            "`!members kick`         — кикнуть автора (reply)\n"
            "`!members ban`          — забанить автора (reply)\n"
            "`!members unban @user`  — разбанить пользователя"
        )
    )
