# -*- coding: utf-8 -*-
"""
social_commands - Phase 2 Wave 6 extraction (Session 27).

Социальные / групповые операции и реакции:
  !pin, !unpin, !del, !purge, !react, !poll, !quiz, !dice,
  !sticker (save/list/del/send), !alias (set/list/del).

Зависит от Pyrogram client API (``pin_chat_message``, ``send_reaction``,
``send_poll``, ``send_dice``, ``send_sticker``, ``delete_messages``).
Re-exported from command_handlers.py for backwards compatibility.

См. ``docs/CODE_SPLITS_PLAN.md`` Phase 2 - domain extractions.
"""

from __future__ import annotations

import json
import pathlib
import time
from typing import TYPE_CHECKING

from pyrogram.types import Message

from ...config import config
from ...core.access_control import AccessLevel
from ...core.command_aliases import alias_service
from ...core.exceptions import UserInputError
from ...core.logger import get_logger

if TYPE_CHECKING:
    from ...userbot_bridge import KraabUserbot

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Sticker storage helpers
# ---------------------------------------------------------------------------

_STICKERS_FILE = pathlib.Path.home() / ".openclaw" / "krab_runtime_state" / "saved_stickers.json"

# Baseline (frozen на import) — нужен чтобы отличить override в текущем
# модуле (sc) от override в command_handlers (исторический namespace).
_STICKERS_FILE_BASELINE = _STICKERS_FILE
_CONFIG_BASELINE = config


def _stickers_path() -> pathlib.Path:
    """Resolve path с поддержкой обоих namespace (Phase 2 dual-patch).

    Тесты могут патчить либо ``social_commands._STICKERS_FILE`` (новый),
    либо ``command_handlers._STICKERS_FILE`` (исторический). Берём
    override из того, кто отличается от baseline.
    """
    import sys

    self_mod = sys.modules[__name__]
    self_val = self_mod.__dict__.get("_STICKERS_FILE", _STICKERS_FILE_BASELINE)
    if self_val is not _STICKERS_FILE_BASELINE:
        return self_val
    try:
        from .. import command_handlers as _ch

        ch_val = getattr(_ch, "_STICKERS_FILE", _STICKERS_FILE_BASELINE)
        if ch_val is not _STICKERS_FILE_BASELINE:
            return ch_val
    except Exception:  # noqa: BLE001
        pass
    return self_val


def _load_stickers() -> dict[str, str]:
    """Загружает словарь {name: file_id} из JSON-файла."""
    path = _stickers_path()
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        pass
    return {}


def _save_stickers(data: dict[str, str]) -> None:
    """Сохраняет словарь {name: file_id} в JSON-файл."""
    path = _stickers_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# !pin / !unpin
# ---------------------------------------------------------------------------


async def handle_pin(bot: "KraabUserbot", message: Message) -> None:
    """
    Закрепляет сообщение в чате (!pin в ответ на сообщение).

    Owner-only. Опциональный флаг `silent` подавляет системное уведомление.
    """
    access_profile = bot._get_access_profile(message.from_user)
    if access_profile.level != AccessLevel.OWNER:
        raise UserInputError(user_message="🔒 `!pin` доступен только владельцу.")

    target = message.reply_to_message
    if target is None:
        raise UserInputError(user_message="📌 Ответь на сообщение, которое хочешь закрепить.")

    # Флаг silent — подавляет системное уведомление о закреплении
    args = bot._get_command_args(message).strip().lower()
    silent = args == "silent"

    try:
        await bot.client.pin_chat_message(
            chat_id=message.chat.id,
            message_id=target.id,
            disable_notification=silent,
        )
        note = " (без уведомления)" if silent else ""
        reply = f"📌 Сообщение закреплено{note}."
    except Exception as exc:
        reply = f"❌ Не удалось закрепить: `{exc}`"

    if message.from_user and message.from_user.id == bot.me.id:
        await message.edit(reply)
    else:
        await message.reply(reply)


async def handle_unpin(bot: "KraabUserbot", message: Message) -> None:
    """
    Открепляет сообщение в чате (!unpin).

    - `!unpin` в ответ на сообщение — открепляет конкретное сообщение.
    - `!unpin all` — открепляет все сообщения в чате.
    Owner-only.
    """
    access_profile = bot._get_access_profile(message.from_user)
    if access_profile.level != AccessLevel.OWNER:
        raise UserInputError(user_message="🔒 `!unpin` доступен только владельцу.")

    args = bot._get_command_args(message).strip().lower()

    try:
        if args == "all":
            # Открепляем все сообщения в чате
            await bot.client.unpin_all_chat_messages(chat_id=message.chat.id)
            reply = "📌 Все сообщения откреплены."
        else:
            # Открепляем конкретное сообщение (reply) или последнее закреплённое
            target = message.reply_to_message
            if target is None:
                raise UserInputError(
                    user_message=(
                        "📌 Ответь на сообщение, которое хочешь открепить, "
                        "или используй `!unpin all`."
                    )
                )
            await bot.client.unpin_chat_message(
                chat_id=message.chat.id,
                message_id=target.id,
            )
            reply = "📌 Сообщение откреплено."
    except UserInputError:
        raise
    except Exception as exc:
        reply = f"❌ Не удалось открепить: `{exc}`"

    if message.from_user and message.from_user.id == bot.me.id:
        await message.edit(reply)
    else:
        await message.reply(reply)


# ---------------------------------------------------------------------------
# !del / !purge — удаление собственных сообщений
# ---------------------------------------------------------------------------


async def handle_del(bot: "KraabUserbot", message: Message) -> None:
    """
    !del [N] — удаляет последние N сообщений Краба в текущем чате.

    По умолчанию N=1. Максимум 100 за раз.
    Включает само сообщение с командой !del.
    """
    access_profile = bot._get_access_profile(message.from_user)
    if access_profile.level != AccessLevel.OWNER:
        raise UserInputError(user_message="🚫 Только owner может удалять сообщения.")

    raw = bot._get_command_args(message).strip()
    try:
        n = int(raw) if raw else 1
    except ValueError:
        raise UserInputError(user_message="❌ Использование: `!del [N]` — N должно быть числом.")

    if n < 1 or n > 100:
        raise UserInputError(user_message="❌ N должно быть от 1 до 100.")

    chat_id = message.chat.id
    bot_id = bot.me.id

    # Удаляем саму команду !del сразу
    try:
        await message.delete()
    except Exception:
        pass

    # Собираем историю и ищем сообщения Краба
    collected: list[int] = []
    try:
        async for msg in bot.client.get_chat_history(chat_id, limit=200):
            if msg.from_user and msg.from_user.id == bot_id:
                collected.append(msg.id)
                if len(collected) >= n:
                    break
    except Exception as exc:
        logger.warning("handle_del_history_error", error=str(exc))

    if not collected:
        return

    try:
        await bot.client.delete_messages(chat_id, message_ids=collected)
        logger.info("handle_del_done", chat_id=chat_id, count=len(collected))
    except Exception as exc:
        logger.warning("handle_del_failed", error=str(exc))


async def handle_purge(bot: "KraabUserbot", message: Message) -> None:
    """
    !purge — удаляет ВСЕ сообщения Краба в текущем чате за последний час.

    Проходит историю за 60 минут, собирает ID сообщений бота и удаляет пачками.
    """
    access_profile = bot._get_access_profile(message.from_user)
    if access_profile.level != AccessLevel.OWNER:
        raise UserInputError(user_message="🚫 Только owner может использовать !purge.")

    chat_id = message.chat.id
    bot_id = bot.me.id
    cutoff = time.time() - 3600  # 1 час назад

    # Удаляем саму команду
    try:
        await message.delete()
    except Exception:
        pass

    collected: list[int] = []
    try:
        async for msg in bot.client.get_chat_history(chat_id, limit=500):
            if msg.date and msg.date.timestamp() < cutoff:
                break
            if msg.from_user and msg.from_user.id == bot_id:
                collected.append(msg.id)
    except Exception as exc:
        logger.warning("handle_purge_history_error", error=str(exc))

    if not collected:
        return

    # Удаляем пачками по 100 (лимит Telegram)
    chunk_size = 100
    deleted_total = 0
    for i in range(0, len(collected), chunk_size):
        chunk = collected[i : i + chunk_size]
        try:
            await bot.client.delete_messages(chat_id, message_ids=chunk)
            deleted_total += len(chunk)
        except Exception as exc:
            logger.warning("handle_purge_chunk_error", error=str(exc))

    logger.info("handle_purge_done", chat_id=chat_id, deleted=deleted_total)


# ---------------------------------------------------------------------------
# !react — реакция на сообщение
# ---------------------------------------------------------------------------


async def handle_react(bot: "KraabUserbot", message: Message) -> None:
    """
    !react <emoji> — поставить реакцию на сообщение.

    Должна быть ответом на сообщение (reply). Ставит указанный emoji
    как реакцию на то сообщение, которому адресован reply.
    Только для owner.

    Примеры:
        !react 👍          — лайк
        !react ❤️          — сердечко
        !react 🔥          — огонь
    """
    # Dual-namespace lookup config (Phase 2): тесты могут патчить либо
    # ``social_commands.config``, либо ``command_handlers.config``.
    # Берём override из того namespace, где значение отличается от _CONFIG_BASELINE.
    _config = config
    try:
        from .. import command_handlers as _ch

        ch_cfg = getattr(_ch, "config", _CONFIG_BASELINE)
        if ch_cfg is not _CONFIG_BASELINE and _config is _CONFIG_BASELINE:
            _config = ch_cfg
    except Exception:  # noqa: BLE001
        pass
    if not bool(getattr(_config, "TELEGRAM_REACTIONS_ENABLED", True)):
        await message.reply("⚠️ Реакции отключены (TELEGRAM_REACTIONS_ENABLED=0).")
        return

    raw_args = bot._get_command_args(message).strip()
    if not raw_args:
        raise UserInputError(
            user_message="🎭 Формат: `!react <emoji>` (в reply на нужное сообщение)\n"
            "Пример: `!react 👍`"
        )

    emoji = raw_args.strip()

    # Определяем целевое сообщение: reply → target, иначе само сообщение
    target = message.reply_to_message if message.reply_to_message else message
    chat_id_int = int(target.chat.id)
    msg_id_int = int(target.id)

    try:
        await bot.client.send_reaction(
            chat_id=chat_id_int,
            message_id=msg_id_int,
            emoji=emoji,
        )
        # Тихо удаляем команду (best-effort) — не захламляем чат
        try:
            await message.delete()
        except Exception:  # noqa: BLE001
            pass
    except Exception as exc:  # noqa: BLE001
        err_text = str(exc)[:200]
        logger.warning(
            "handle_react_failed",
            chat_id=chat_id_int,
            message_id=msg_id_int,
            emoji=emoji,
            error=err_text,
        )
        await message.reply(f"❌ Не удалось поставить реакцию `{emoji}`: {err_text}")


# ---------------------------------------------------------------------------
# !poll / !quiz — опросы
# ---------------------------------------------------------------------------


async def handle_poll(bot: "KraabUserbot", message: Message) -> None:
    """
    Быстрое создание опросов в чате.

    Синтаксис:
      !poll <вопрос> | <вариант1> | <вариант2> [| ...]
      !poll anonymous <вопрос> | <вариант1> | <вариант2> [| ...]
    Минимум 2, максимум 10 вариантов.
    """
    raw = bot._get_command_args(message).strip()

    if not raw or raw.lower() in {"help", "помощь"}:
        raise UserInputError(
            user_message=(
                "📊 **!poll — создание опроса**\n\n"
                "`!poll Вопрос? | Вариант 1 | Вариант 2`\n"
                "`!poll anonymous Вопрос? | Вариант 1 | Вариант 2`\n\n"
                "Минимум 2, максимум 10 вариантов. Разделитель — `|`."
            )
        )

    # Определяем режим анонимности
    is_anonymous = False
    if raw.lower().startswith("anonymous "):
        is_anonymous = True
        raw = raw[len("anonymous ") :].strip()

    # Разбираем вопрос и варианты
    parts = [p.strip() for p in raw.split("|")]
    if len(parts) < 3:
        raise UserInputError(
            user_message="❌ Нужно минимум 2 варианта. Синтаксис: `!poll Вопрос? | Вариант 1 | Вариант 2`"
        )

    question = parts[0]
    options = parts[1:]

    if len(options) > 10:
        raise UserInputError(user_message="❌ Максимум 10 вариантов ответа.")

    if not question:
        raise UserInputError(user_message="❌ Вопрос не может быть пустым.")

    # Удаляем исходное сообщение с командой
    try:
        await message.delete()
    except Exception:  # noqa: BLE001
        pass

    await bot.client.send_poll(
        chat_id=message.chat.id,
        question=question,
        options=options,
        is_anonymous=is_anonymous,
    )
    logger.info(
        "handle_poll_sent", question=question, options_count=len(options), anonymous=is_anonymous
    )


async def handle_quiz(bot: "KraabUserbot", message: Message) -> None:
    """
    Создание квиза (опрос с правильным ответом).

    Синтаксис:
      !quiz <вопрос> | <правильный ответ> | <неправильный1> [| ...]
    Первый вариант всегда правильный. Минимум 2, максимум 10 вариантов.
    """
    raw = bot._get_command_args(message).strip()

    if not raw or raw.lower() in {"help", "помощь"}:
        raise UserInputError(
            user_message=(
                "🧠 **!quiz — создание квиза**\n\n"
                "`!quiz Вопрос? | Правильный ответ | Неправильный 1 | Неправильный 2`\n\n"
                "Первый вариант — правильный. Минимум 2, максимум 10 вариантов."
            )
        )

    parts = [p.strip() for p in raw.split("|")]
    if len(parts) < 3:
        raise UserInputError(
            user_message="❌ Нужно минимум 2 варианта. Синтаксис: `!quiz Вопрос? | Правильный | Неправильный`"
        )

    question = parts[0]
    options = parts[1:]

    if len(options) > 10:
        raise UserInputError(user_message="❌ Максимум 10 вариантов ответа.")

    if not question:
        raise UserInputError(user_message="❌ Вопрос не может быть пустым.")

    # Удаляем исходное сообщение с командой
    try:
        await message.delete()
    except Exception:  # noqa: BLE001
        pass

    await bot.client.send_poll(
        chat_id=message.chat.id,
        question=question,
        options=options,
        type="quiz",
        correct_option_id=0,  # первый вариант — правильный
        is_anonymous=False,
    )
    logger.info("handle_quiz_sent", question=question, options_count=len(options))


# ---------------------------------------------------------------------------
# !dice — анимированные кубики
# ---------------------------------------------------------------------------


async def handle_dice(bot: "KraabUserbot", message: Message) -> None:
    """
    Отправка анимированных Telegram dice (кубик/дартс/футбол/баскетбол/боулинг/слот).

    Синтаксис:
      !dice            → 🎲 кубик (по умолчанию)
      !dice dart       → 🎯 дартс
      !dice ball       → ⚽ футбол
      !dice basket     → 🏀 баскетбол
      !dice bowl       → 🎳 боулинг
      !dice slot       → 🎰 слот-машина
    """
    # Карта alias → эмодзи
    _DICE_ALIASES: dict[str, str] = {  # noqa: N806 — легаси emoji map
        "": "🎲",
        "dice": "🎲",
        "dart": "🎯",
        "darts": "🎯",
        "ball": "⚽",
        "football": "⚽",
        "soccer": "⚽",
        "basket": "🏀",
        "basketball": "🏀",
        "bowl": "🎳",
        "bowling": "🎳",
        "slot": "🎰",
        "slots": "🎰",
        "casino": "🎰",
    }

    raw = bot._get_command_args(message).strip().lower()

    emoji = _DICE_ALIASES.get(raw)
    if emoji is None:
        raise UserInputError(
            user_message=(
                "🎲 **!dice — анимированные кубики**\n\n"
                "`!dice` — 🎲 кубик\n"
                "`!dice dart` — 🎯 дартс\n"
                "`!dice ball` — ⚽ футбол\n"
                "`!dice basket` — 🏀 баскетбол\n"
                "`!dice bowl` — 🎳 боулинг\n"
                "`!dice slot` — 🎰 слот-машина"
            )
        )

    # Удаляем команду (best-effort)
    try:
        await message.delete()
    except Exception:  # noqa: BLE001
        pass

    await bot.client.send_dice(chat_id=message.chat.id, emoji=emoji)
    logger.info("handle_dice_sent", emoji=emoji, chat_id=message.chat.id)


# ---------------------------------------------------------------------------
# !sticker — управление стикерами
# ---------------------------------------------------------------------------


async def handle_sticker(bot: "KraabUserbot", message: Message) -> None:
    """
    !sticker save <name> — сохранить стикер (в ответ на стикер)
    !sticker <name>      — отправить сохранённый стикер
    !sticker list        — показать список сохранённых стикеров
    !sticker del <name>  — удалить стикер из коллекции
    """
    raw_args = bot._get_command_args(message).strip()
    parts = raw_args.split(None, 1)

    # --- !sticker list ---
    if not parts or parts[0].lower() == "list":
        stickers = _load_stickers()
        if not stickers:
            await message.reply(
                "📭 Нет сохранённых стикеров. Используй `!sticker save <name>` в ответ на стикер."
            )
            return
        lines = [f"• `{name}`" for name in sorted(stickers)]
        await message.reply("🗂 **Сохранённые стикеры:**\n" + "\n".join(lines))
        return

    subcommand = parts[0].lower()

    # --- !sticker save <name> ---
    if subcommand == "save":
        if len(parts) < 2 or not parts[1].strip():
            raise UserInputError(user_message="❌ Укажи имя: `!sticker save <name>`")
        name = parts[1].strip().lower()

        # Ищем стикер в replied сообщении
        replied = message.reply_to_message
        if replied is None or replied.sticker is None:
            raise UserInputError(user_message="❌ Ответь на стикер командой `!sticker save <name>`")

        file_id = replied.sticker.file_id
        stickers = _load_stickers()
        stickers[name] = file_id
        _save_stickers(stickers)
        await message.reply(f"✅ Стикер `{name}` сохранён!")
        return

    # --- !sticker del <name> ---
    if subcommand == "del":
        if len(parts) < 2 or not parts[1].strip():
            raise UserInputError(user_message="❌ Укажи имя: `!sticker del <name>`")
        name = parts[1].strip().lower()
        stickers = _load_stickers()
        if name not in stickers:
            raise UserInputError(user_message=f"❌ Стикер `{name}` не найден.")
        del stickers[name]
        _save_stickers(stickers)
        await message.reply(f"🗑 Стикер `{name}` удалён.")
        return

    # --- !sticker <name> — отправить стикер ---
    name = parts[0].lower()
    stickers = _load_stickers()
    if name not in stickers:
        raise UserInputError(user_message=f"❌ Стикер `{name}` не найден. Список: `!sticker list`")
    file_id = stickers[name]
    await bot.client.send_sticker(message.chat.id, file_id)
    # Удаляем исходную команду, чтобы не засорять чат
    try:
        await message.delete()
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# !alias — пользовательские алиасы команд
# ---------------------------------------------------------------------------


async def handle_alias(bot: "KraabUserbot", message: Message) -> None:
    """
    Управление пользовательскими алиасами команд.

    Использование:
      !alias set <имя> <команда>   — создать алиас (напр. !alias set t !translate)
      !alias list                  — показать все алиасы
      !alias del <имя>             — удалить алиас
    """
    del bot
    raw = str(message.text or "").split(maxsplit=2)
    # raw[0] = "!alias", raw[1] = subcommand, raw[2] = остаток

    if len(raw) < 2:
        await message.reply(
            "**Алиасы команд**\n\n"
            "`!alias set <имя> <команда>` — создать алиас\n"
            "`!alias list` — список алиасов\n"
            "`!alias del <имя>` — удалить алиас\n\n"
            "Пример: `!alias set t !translate` → затем `!t привет` = `!translate привет`"
        )
        return

    sub = raw[1].lower()

    if sub == "list":
        await message.reply(alias_service.format_list())

    elif sub == "set":
        if len(raw) < 3:
            raise UserInputError(user_message="Формат: `!alias set <имя> <команда>`")
        # raw[2] = "<имя> <команда>"
        parts = raw[2].split(None, 1)
        if len(parts) < 2:
            raise UserInputError(
                user_message="Формат: `!alias set <имя> <команда>`\n"
                "Пример: `!alias set t !translate`"
            )
        alias_name, alias_cmd = parts[0], parts[1]
        ok, msg = alias_service.add(alias_name, alias_cmd)
        await message.reply(msg)

    elif sub in ("del", "delete", "rm", "remove"):
        if len(raw) < 3:
            raise UserInputError(user_message="Формат: `!alias del <имя>`")
        alias_name = raw[2].strip()
        ok, msg = alias_service.remove(alias_name)
        await message.reply(msg)

    else:
        raise UserInputError(
            user_message=f"Неизвестная подкоманда `{sub}`.\nДоступно: `set`, `list`, `del`"
        )


__all__ = [
    "_STICKERS_FILE",
    "_load_stickers",
    "_save_stickers",
    "handle_alias",
    "handle_del",
    "handle_dice",
    "handle_pin",
    "handle_poll",
    "handle_purge",
    "handle_quiz",
    "handle_react",
    "handle_sticker",
    "handle_unpin",
]
