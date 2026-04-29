# -*- coding: utf-8 -*-
"""
chat_commands — Phase 2 Wave 2 extraction (Session 27).

Команды работающие с chat history / chat metadata / users:
  !who, !chatinfo, !history, !monitor, !whois.

Зависят от Pyrogram client API (``bot.client.get_chat``,
``get_chat_history``, ``get_users``) и системного ``whois``.

См. ``docs/CODE_SPLITS_PLAN.md`` § Phase 2 — domain extractions.
"""

from __future__ import annotations

import asyncio
import re
from typing import TYPE_CHECKING

from pyrogram.types import Message

from ...core.exceptions import UserInputError
from ...core.logger import get_logger

if TYPE_CHECKING:
    from ...userbot_bridge import KraabUserbot

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# !who — информация о пользователе или чате
# ---------------------------------------------------------------------------


async def handle_who(bot: "KraabUserbot", message: Message) -> None:
    """
    Информация о пользователе или чате.

    Варианты использования:
      !who                — в ответ на сообщение: инфо об авторе
      !who @username      — инфо по username или user_id
      !who                — без reply и без аргументов: инфо о текущем чате
    """

    def _fmt_status(user) -> str:
        """Форматирует статус пользователя."""
        from pyrogram.enums import UserStatus

        status = getattr(user, "status", None)
        if status is None:
            return "неизвестен"
        status_map = {
            UserStatus.ONLINE: "🟢 online",
            UserStatus.OFFLINE: "⚪ offline",
            UserStatus.RECENTLY: "🕐 недавно в сети",
            UserStatus.LAST_WEEK: "📅 на прошлой неделе",
            UserStatus.LAST_MONTH: "📆 в прошлом месяце",
            UserStatus.LONG_AGO: "⏳ давно",
        }
        return status_map.get(status, str(status))

    args = bot._get_command_args(message).strip()

    target_user_id = None
    show_chat = False

    if message.reply_to_message and not args:
        replied = message.reply_to_message
        if replied.from_user:
            target_user_id = replied.from_user.id
        elif replied.sender_chat:
            target_user_id = replied.sender_chat.id
            show_chat = True
        else:
            await message.reply("❓ Не могу определить отправителя сообщения.")
            return
    elif args:
        raw = args.lstrip("@")
        try:
            target_user_id = int(raw)
        except ValueError:
            target_user_id = raw
    else:
        show_chat = True
        target_user_id = message.chat.id

    if show_chat:
        try:
            chat = await bot.client.get_chat(target_user_id)
        except Exception as exc:
            await message.reply(f"❌ Ошибка: не удалось получить инфо о чате: {exc}")
            return

        chat_type = str(getattr(chat, "type", "")).replace("ChatType.", "")
        members = getattr(chat, "members_count", None)
        description = getattr(chat, "description", None) or "—"
        username = f"@{chat.username}" if getattr(chat, "username", None) else "отсутствует"

        lines = [
            "💬 **Chat Info**",
            "─────────────",
            f"**Название:** {chat.title or chat.first_name or '—'}",
            f"**Username:** {username}",
            f"**ID:** `{chat.id}`",
            f"**Тип:** {chat_type}",
        ]
        if members is not None:
            lines.append(f"**Участников:** {members}")
        lines.append(f"**Описание:** {description}")

        await message.reply("\n".join(lines))
        return

    try:
        user = await bot.client.get_users(target_user_id)
    except Exception as exc:
        await message.reply(f"❌ Ошибка: не удалось получить инфо о пользователе: {exc}")
        return

    common_count: int | str = "—"
    if not getattr(user, "is_bot", False):
        try:
            common_chats = await bot.client.get_common_chats(user.id)
            common_count = len(common_chats)
        except Exception:
            common_count = "—"

    bio = "—"
    try:
        chat_info = await bot.client.get_chat(user.id)
        bio = getattr(chat_info, "bio", None) or "—"
    except Exception:
        pass

    phone = getattr(user, "phone_number", None) or "скрыт"

    name_parts = [user.first_name or ""]
    if user.last_name:
        name_parts.append(user.last_name)
    full_name = " ".join(name_parts).strip() or "—"

    username_str = f"@{user.username}" if user.username else "—"
    is_bot = "да" if getattr(user, "is_bot", False) else "нет"
    is_premium = "да" if getattr(user, "is_premium", False) else "нет"
    is_verified = "да" if getattr(user, "is_verified", False) else "нет"
    is_restricted = "да" if getattr(user, "is_restricted", False) else "нет"
    is_scam = " ⚠️ SCAM" if getattr(user, "is_scam", False) else ""
    is_fake = " ⚠️ FAKE" if getattr(user, "is_fake", False) else ""

    lines = [
        f"👤 **User Info**{is_scam}{is_fake}",
        "─────────────",
        f"**Имя:** {full_name}",
        f"**Username:** {username_str}",
        f"**ID:** `{user.id}`",
        f"**Телефон:** {phone}",
        f"**Статус:** {_fmt_status(user)}",
        f"**Бот:** {is_bot}",
        f"**Premium:** {is_premium}",
        f"**Verified:** {is_verified}",
    ]
    if is_restricted == "да":
        lines.append("**Restricted:** да")
    lines.append(f"**Bio:** {bio}")
    if not getattr(user, "is_bot", False):
        lines.append(f"**Общих чатов:** {common_count}")

    await message.reply("\n".join(lines))


# ---------------------------------------------------------------------------
# !chatinfo — подробная информация о чате
# ---------------------------------------------------------------------------


async def handle_chatinfo(bot: "KraabUserbot", message: Message) -> None:
    """Подробная информация о чате.

    Синтаксис:
      !chatinfo              — текущий чат
      !chatinfo <chat_id>   — другой чат по ID или @username
    """
    args = bot._get_command_args(message).strip()

    if args:
        raw = args.lstrip("@")
        try:
            target: int | str = int(raw)
        except ValueError:
            target = raw
    else:
        target = message.chat.id

    try:
        chat = await bot.client.get_chat(target)
    except Exception as exc:
        raise UserInputError(
            user_message=f"❌ Не удалось получить инфо о чате `{target}`: {exc}"
        ) from exc

    chat_type = str(getattr(chat, "type", "")).replace("ChatType.", "").lower()

    members_count = getattr(chat, "members_count", None)
    if members_count is None:
        try:
            members_count = await bot.client.get_chat_members_count(chat.id)
        except Exception:
            members_count = None

    username = f"@{chat.username}" if getattr(chat, "username", None) else "—"
    title = getattr(chat, "title", None) or getattr(chat, "first_name", None) or "—"
    description = (getattr(chat, "description", None) or "").strip() or "—"

    dc_date = getattr(chat, "date", None)
    created_str: str
    if dc_date:
        try:
            import datetime as _dt

            if isinstance(dc_date, (int, float)):
                dt = _dt.datetime.fromtimestamp(dc_date, tz=_dt.timezone.utc)
            else:
                dt = dc_date
            created_str = dt.strftime("%Y-%m-%d")
        except Exception:
            created_str = str(dc_date)
    else:
        created_str = "—"

    linked_chat = getattr(chat, "linked_chat", None)
    if linked_chat:
        lc_username = getattr(linked_chat, "username", None)
        lc_id = getattr(linked_chat, "id", None)
        linked_str = f"@{lc_username}" if lc_username else str(lc_id or "—")
    else:
        linked_str = "—"

    admins_count: int | str = "—"
    try:
        admins = [m async for m in bot.client.get_chat_members(chat.id, filter="administrators")]
        admins_count = len(admins)
    except Exception:
        pass

    perms = getattr(chat, "permissions", None)
    perm_lines: list[str] = []
    if perms:
        _perm_map = [
            ("can_send_messages", "Писать сообщения"),
            ("can_send_media_messages", "Медиа"),
            ("can_send_polls", "Опросы"),
            ("can_add_web_page_previews", "Превью"),
            ("can_change_info", "Изменять инфо"),
            ("can_invite_users", "Приглашать"),
            ("can_pin_messages", "Закреплять"),
        ]
        for attr, label in _perm_map:
            val = getattr(perms, attr, None)
            if val is not None:
                icon = "✅" if val else "❌"
                perm_lines.append(f"  {icon} {label}")

    lines: list[str] = [
        "📊 **Chat Info**",
        "─────────────",
        f"**Название:** {title}",
        f"**ID:** `{chat.id}`",
        f"**Тип:** {chat_type}",
    ]
    if members_count is not None:
        lines.append(f"**Участников:** {members_count:,}")
    lines.append(f"**Username:** {username}")
    lines.append(f"**Создан:** {created_str}")
    if description != "—":
        desc_display = description[:200] + "…" if len(description) > 200 else description
        lines.append(f"**Описание:** {desc_display}")
    lines.append(f"**Linked chat:** {linked_str}")
    if isinstance(admins_count, int):
        lines.append(f"**Администраторов:** {admins_count}")
    if perm_lines:
        lines.append("**Права участников:**")
        lines.extend(perm_lines)

    await message.reply("\n".join(lines))


# ---------------------------------------------------------------------------
# !history — статистика последних 1000 сообщений
# ---------------------------------------------------------------------------


async def handle_history(bot: "KraabUserbot", message: Message) -> None:
    """Статистика текущего чата за последние 1000 сообщений."""
    import datetime as _dt
    from collections import Counter

    chat_id = message.chat.id
    limit = 1000

    total = 0
    text_count = 0
    photo_count = 0
    video_count = 0
    voice_count = 0
    doc_count = 0
    other_count = 0

    weekday_counts: Counter = Counter()
    dates_seen: set = set()

    first_dt: _dt.datetime | None = None
    last_dt: _dt.datetime | None = None

    try:
        async for msg in bot.client.get_chat_history(chat_id, limit=limit):
            total += 1

            if msg.text:
                text_count += 1
            elif msg.photo:
                photo_count += 1
            elif msg.video or msg.video_note:
                video_count += 1
            elif msg.voice or msg.audio:
                voice_count += 1
            elif msg.document:
                doc_count += 1
            else:
                other_count += 1

            if msg.date:
                msg_dt = msg.date
                if isinstance(msg_dt, (int, float)):
                    msg_dt = _dt.datetime.fromtimestamp(msg_dt, tz=_dt.timezone.utc)
                weekday_counts[msg_dt.weekday()] += 1
                dates_seen.add(msg_dt.date())

                if first_dt is None or msg_dt < first_dt:
                    first_dt = msg_dt
                if last_dt is None or msg_dt > last_dt:
                    last_dt = msg_dt

    except Exception as exc:
        raise UserInputError(user_message=f"❌ Не удалось получить историю чата: {exc}") from exc

    if total == 0:
        await message.reply("📈 В этом чате нет сообщений (в пределах 1000).")
        return

    _weekday_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    if weekday_counts:
        busiest_wd, busiest_count = weekday_counts.most_common(1)[0]
        busiest_name = _weekday_names[busiest_wd]
        busiest_days_in_sample = sum(1 for d in dates_seen if d.weekday() == busiest_wd) or 1
        avg_on_busiest = round(busiest_count / busiest_days_in_sample)
        most_active_str = f"{busiest_name} (avg {avg_on_busiest} msgs)"
    else:
        most_active_str = "—"

    days_span = len(dates_seen) or 1
    avg_per_day = round(total / days_span)

    first_str = first_dt.strftime("%Y-%m-%d") if first_dt else "—"
    last_str = last_dt.strftime("%Y-%m-%d") if last_dt else "—"

    lines = [
        "📈 Chat History Stats",
        "─────────────",
        f"Messages: {total:,}",
        (
            f"Text: {text_count:,} | Photo: {photo_count:,} | Video: {video_count:,}"
            f" | Voice: {voice_count:,} | Docs: {doc_count:,} | Other: {other_count:,}"
        ),
        f"Most active: {most_active_str}",
        f"Average: {avg_per_day:,} msgs/day",
        f"First: {first_str} | Last: {last_str}",
    ]
    await message.reply("\n".join(lines))


# ---------------------------------------------------------------------------
# !monitor — управление мониторингом чатов
# ---------------------------------------------------------------------------


async def handle_monitor(bot: "KraabUserbot", message: Message) -> None:
    """Управление мониторингом чатов на ключевые слова."""
    from ...core.chat_monitor import chat_monitor_service

    args_raw = message.command[1:] if message.command else []
    if not args_raw:
        await message.reply(
            "📡 **Chat Monitor**\n\n"
            "`!monitor add <chat_id> [keywords...]` — начать мониторинг\n"
            "`!monitor remove <chat_id>` — остановить мониторинг\n"
            "`!monitor list` — активные мониторинги\n\n"
            "Regex поддерживается: `re:pattern`"
        )
        return

    subcmd = args_raw[0].lower()

    if subcmd == "list":
        monitors = chat_monitor_service.list_monitors()
        if not monitors:
            await message.reply("📡 Активных мониторингов нет.")
            return
        lines = ["📡 **Активные мониторинги:**\n"]
        for entry in monitors:
            kw_str = (
                ", ".join(f"`{k}`" for k in entry.keywords)
                if entry.keywords
                else "_(все сообщения)_"
            )
            lines.append(
                f"• **{entry.chat_title}** (`{entry.chat_id}`)\n  Ключевые слова: {kw_str}"
            )
        await message.reply("\n".join(lines))
        return

    if subcmd == "remove":
        if len(args_raw) < 2:
            raise UserInputError(user_message="❌ Формат: `!monitor remove <chat_id>`")
        target_id = args_raw[1]
        removed = chat_monitor_service.remove(target_id)
        if removed:
            await message.reply(f"🗑️ Мониторинг `{target_id}` удалён.")
        else:
            await message.reply(f"⚠️ Мониторинг для `{target_id}` не найден.")
        return

    if subcmd == "add":
        if len(args_raw) < 2:
            raise UserInputError(
                user_message="❌ Формат: `!monitor add <chat_id|@username> [keywords...]`"
            )
        target_raw = args_raw[1]
        keywords = args_raw[2:]

        chat_id_resolved: int | str = target_raw
        chat_title = target_raw
        try:
            chat = await bot.client.get_chat(target_raw)
            chat_id_resolved = chat.id
            chat_title = (
                getattr(chat, "title", None) or getattr(chat, "first_name", None) or target_raw
            )
        except Exception as e:
            logger.warning("monitor_resolve_chat_error", target=target_raw, error=str(e))
            if target_raw.lstrip("-").isdigit():
                chat_id_resolved = int(target_raw)

        entry = chat_monitor_service.add(
            chat_id=chat_id_resolved,
            chat_title=chat_title,
            keywords=list(keywords),
        )
        kw_str = (
            ", ".join(f"`{k}`" for k in entry.keywords) if entry.keywords else "_(все сообщения)_"
        )
        await message.reply(
            f"✅ **Мониторинг запущен**\n"
            f"Чат: **{entry.chat_title}** (`{entry.chat_id}`)\n"
            f"Ключевые слова: {kw_str}"
        )
        return

    raise UserInputError(
        user_message="❌ Неизвестная подкоманда. Используй: `add`, `remove`, `list`"
    )


# ---------------------------------------------------------------------------
# !whois — WHOIS lookup
# ---------------------------------------------------------------------------

_WHOIS_FIELD_PATTERNS: list[tuple[str, list[str]]] = [
    ("registrar", [r"Registrar:\s*(.+)", r"registrar:\s*(.+)"]),
    (
        "created",
        [
            r"Creation Date:\s*(.+)",
            r"Created Date:\s*(.+)",
            r"created:\s*(.+)",
            r"Domain Registration Date:\s*(.+)",
        ],
    ),
    (
        "expires",
        [
            r"Registry Expiry Date:\s*(.+)",
            r"Expir(?:y|ation) Date:\s*(.+)",
            r"expires:\s*(.+)",
            r"paid-till:\s*(.+)",
        ],
    ),
    (
        "nameservers",
        [
            r"Name Server:\s*(.+)",
            r"nserver:\s*(.+)",
            r"Nameservers:\s*(.+)",
        ],
    ),
]


def _parse_whois_output(raw: str) -> dict[str, str | list[str]]:
    """Извлекает ключевые WHOIS-поля из сырого вывода."""
    result: dict[str, str | list[str]] = {}
    nameservers: list[str] = []

    for field_key, patterns in _WHOIS_FIELD_PATTERNS:
        if field_key == "nameservers":
            for pattern in patterns:
                for m in re.finditer(pattern, raw, re.IGNORECASE | re.MULTILINE):
                    ns = m.group(1).strip().lower().rstrip(".")
                    if ns and ns not in nameservers:
                        nameservers.append(ns)
        else:
            if field_key in result:
                continue
            for pattern in patterns:
                m = re.search(pattern, raw, re.IGNORECASE | re.MULTILINE)
                if m:
                    value = m.group(1).strip()
                    if field_key in ("created", "expires") and "T" in value:
                        value = value.split("T")[0]
                    elif field_key in ("created", "expires"):
                        value = value.split(" ")[0]
                    result[field_key] = value
                    break

    result["nameservers"] = nameservers  # type: ignore[assignment]
    return result


async def handle_whois(bot: "KraabUserbot", message: Message) -> None:
    """!whois <домен> — WHOIS lookup: регистратор, дата создания, истечения, NS."""
    from ...core.subprocess_env import clean_subprocess_env  # noqa: PLC0415

    domain = bot._get_command_args(message).strip().lower()

    domain = re.sub(r"^https?://", "", domain)
    domain = domain.split("/")[0].strip()

    if not domain:
        raise UserInputError(
            user_message=(
                "🔍 **!whois — WHOIS lookup**\n\n"
                "`!whois <домен>` — информация о домене\n\n"
                "_Пример: `!whois example.com`_"
            )
        )

    status_msg = await message.reply(f"🔍 WHOIS: `{domain}`...")

    try:
        proc = await asyncio.create_subprocess_exec(
            "whois",
            domain,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=clean_subprocess_env(),
        )
        try:
            stdout, _stderr = await asyncio.wait_for(proc.communicate(), timeout=20.0)
        except asyncio.TimeoutError:
            if proc.returncode is None:
                try:
                    proc.terminate()
                except ProcessLookupError:
                    pass
                else:
                    try:
                        await asyncio.wait_for(proc.wait(), timeout=2.0)
                    except asyncio.TimeoutError:
                        try:
                            proc.kill()
                        except ProcessLookupError:
                            pass
                        try:
                            await asyncio.wait_for(proc.wait(), timeout=1.0)
                        except asyncio.TimeoutError:
                            logger.warning(
                                "subprocess_force_killed_but_no_reap",
                                pid=proc.pid,
                            )
            await status_msg.edit(f"❌ WHOIS timeout для `{domain}` (>20 сек).")
            return
    except FileNotFoundError:
        await status_msg.edit("❌ Утилита `whois` не найдена на этом хосте.")
        return
    except Exception as exc:  # noqa: BLE001
        logger.error("handle_whois_exec_error", domain=domain, error=str(exc))
        await status_msg.edit(f"❌ Ошибка запуска whois: {exc}")
        return

    raw = stdout.decode("utf-8", errors="replace")

    _not_found_signals = (
        "no match",
        "not found",
        "no entries found",
        "object does not exist",
        "no data found",
        "this query returned 0 objects",
        "domain not found",
    )
    raw_lower = raw.lower()
    if any(sig in raw_lower for sig in _not_found_signals) and len(raw) < 500:
        await status_msg.edit(f"❌ Домен `{domain}` не найден в WHOIS.")
        return

    fields = _parse_whois_output(raw)

    registrar = fields.get("registrar") or "—"
    created = fields.get("created") or "—"
    expires = fields.get("expires") or "—"
    ns_list: list[str] = fields.get("nameservers", [])  # type: ignore[assignment]
    nameservers_str = ", ".join(ns_list) if ns_list else "—"

    reply = (
        f"🔍 WHOIS: `{domain}`\n"
        f"─────\n"
        f"Registrar: {registrar}\n"
        f"Created: {created}\n"
        f"Expires: {expires}\n"
        f"Nameservers: {nameservers_str}"
    )

    await status_msg.edit(reply)


__all__ = [
    "handle_who",
    "handle_chatinfo",
    "handle_history",
    "handle_monitor",
    "handle_whois",
    "_parse_whois_output",
    "_WHOIS_FIELD_PATTERNS",
]
