# -*- coding: utf-8 -*-
"""
admin_commands — Phase 2 Wave 11 extraction (Session 27).

Административные / конфигурационные команды и их private helpers:
  !config, !set, !acl, !scope, !reasoning, !role, !notify,
  !chatban, !cap, !silence, !archive, !unarchive.

Что НЕ перенесено:
- ``handle_model`` — логически ближе к model_commands, не admin.
- ``_AUTODEL_STATE_KEY`` — определён в command_handlers (источник истины
  для этого worktree-базиса); здесь берётся через lazy lookup ``_ch``.

Re-exported из command_handlers.py для обратной совместимости.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pyrogram.types import Message

from ...config import config
from ...core.access_control import (
    PARTIAL_ACCESS_COMMANDS,
    AccessLevel,
    get_effective_owner_label,
    load_acl_runtime_state,
    normalize_subject,
    update_acl_subject,
)
from ...core.chat_ban_cache import chat_ban_cache
from ...core.exceptions import UserInputError
from ...core.logger import get_logger
from ...employee_templates import ROLES, list_roles
from .. import command_handlers as _ch  # lazy proxy — патчабельный namespace

if TYPE_CHECKING:
    from ...userbot_bridge import KraabUserbot

logger = get_logger(__name__)

##############################################################################
# !config — просмотр и изменение технических настроек
##############################################################################

# (config_key, описание)
_CONFIG_GROUPS: list[tuple[str, list[tuple[str, str]]]] = [
    (
        "Модель и routing",
        [
            ("MODEL", "Основная модель"),
            ("FORCE_CLOUD", "Принудительный cloud-маршрут"),
            ("LOCAL_FALLBACK_ENABLED", "Fallback cloud→local при ошибках"),
            ("LOCAL_PREFERRED_MODEL", "Локальная модель (LM Studio)"),
            ("LOCAL_PREFERRED_VISION_MODEL", "Локальная vision-модель"),
            ("SINGLE_LOCAL_MODEL_MODE", "Держать одну локальную модель"),
            ("GUARDED_IDLE_UNLOAD", "Guarded idle-unload локальной модели"),
            ("GUARDED_IDLE_UNLOAD_GRACE_SEC", "Пауза перед idle-unload (сек)"),
            ("RESTORE_PREFERRED_ON_IDLE_UNLOAD", "Восстановить preferred после unload"),
        ],
    ),
    (
        "Таймауты и retry",
        [
            ("OPENCLAW_CHUNK_TIMEOUT_SEC", "Таймаут chunk стриминга (сек)"),
            ("OPENCLAW_FIRST_CHUNK_TIMEOUT_SEC", "Таймаут первого chunk (сек)"),
            ("OPENCLAW_PHOTO_FIRST_CHUNK_TIMEOUT_SEC", "Таймаут первого chunk фото (сек)"),
            ("OPENCLAW_AUTO_RETRY_COUNT", "Кол-во auto-retry при ошибках"),
            ("OPENCLAW_AUTO_RETRY_DELAY_SEC", "Задержка auto-retry (сек)"),
            ("OPENCLAW_PROGRESS_NOTICE_INITIAL_SEC", "Первый progress-notice (сек)"),
            ("OPENCLAW_PROGRESS_NOTICE_REPEAT_SEC", "Повтор progress-notice (сек)"),
        ],
    ),
    (
        "Userbot и Telegram",
        [
            ("USERBOT_MAX_OUTPUT_TOKENS", "Макс. токенов ответа (текст)"),
            ("USERBOT_PHOTO_MAX_OUTPUT_TOKENS", "Макс. токенов ответа (фото)"),
            ("USERBOT_FORCE_CLOUD_FOR_PHOTO", "Cloud-маршрут для фото"),
            ("TELEGRAM_STREAM_UPDATE_INTERVAL_SEC", "Интервал stream UI (сек)"),
            ("TELEGRAM_STREAM_SHOW_REASONING", "Показывать reasoning в stream"),
            ("TELEGRAM_REACTIONS_ENABLED", "Реакции 👀✅❌"),
            ("TELEGRAM_MESSAGE_BATCH_WINDOW_SEC", "Окно склейки сообщений (сек)"),
            ("TELEGRAM_SESSION_HEARTBEAT_SEC", "Heartbeat MTProto (сек)"),
            ("TOOL_NARRATION_ENABLED", "Tool narration в Telegram"),
        ],
    ),
    (
        "Фоновые задачи",
        [
            ("SCHEDULER_ENABLED", "Планировщик reminders/cron"),
            ("DEFERRED_ACTION_GUARD_ENABLED", "Guard deferred-actions"),
            ("SWARM_AUTONOMOUS_ENABLED", "Автономные задачи свёрма"),
            ("SILENCE_DEFAULT_MINUTES", "Tишина по умолчанию (мин)"),
            ("OWNER_AUTO_SILENCE_MINUTES", "Авто-тишина при owner-write (мин)"),
        ],
    ),
    (
        "Доступ и безопасность",
        [
            ("OWNER_USERNAME", "Username владельца (fallback)"),
            ("NON_OWNER_SAFE_MODE_ENABLED", "Safe-mode для гостей"),
            ("GUEST_TOOLS_DISABLED", "Запрет tools для GUEST"),
            ("FORWARD_UNKNOWN_INCOMING", "Пересылать неизвестные входящие"),
            ("AI_DISCLOSURE_ENABLED", "Дисклеймер ИИ в начале диалога"),
            ("MANUAL_BLOCKLIST", "Чёрный список (usernames/IDs)"),
        ],
    ),
    (
        "Голос",
        [
            ("VOICE_MODE_DEFAULT", "Voice-режим по умолчанию"),
            ("VOICE_REPLY_SPEED", "Скорость TTS"),
            ("VOICE_REPLY_VOICE", "TTS-голос"),
            ("VOICE_REPLY_DELIVERY", "Режим доставки (text+voice/voice/text)"),
        ],
    ),
    (
        "История диалога",
        [
            ("HISTORY_WINDOW_MESSAGES", "Окно cloud-истории (сообщений)"),
            ("LOCAL_HISTORY_WINDOW_MESSAGES", "Окно local-истории (сообщений)"),
            ("RETRY_HISTORY_WINDOW_MESSAGES", "Окно retry-истории (сообщений)"),
        ],
    ),
    (
        "Сеть и прокси",
        [
            ("TOR_ENABLED", "Tor SOCKS5 прокси"),
            ("TOR_SOCKS_PORT", "Порт Tor SOCKS5"),
            ("BROWSER_FOCUS_TAB", "Фокус вкладки браузера"),
            ("LM_STUDIO_URL", "URL LM Studio"),
            ("OPENCLAW_URL", "URL OpenClaw Gateway"),
        ],
    ),
    (
        "Прочее",
        [
            ("DEFAULT_WEATHER_CITY", "Город погоды по умолчанию"),
            ("MAX_RAM_GB", "Лимит RAM (GB)"),
            ("LOG_LEVEL", "Уровень логирования"),
            ("GEMINI_PAID_KEY_ENABLED", "Платный Gemini API ключ"),
        ],
    ),
]

# Плоский индекс key→описание для быстрого поиска
_CONFIG_KEY_DESC: dict[str, str] = {k: desc for _, group in _CONFIG_GROUPS for k, desc in group}


def _render_config_value(key: str) -> str:
    """Возвращает строковое представление значения ключа конфига."""
    val = getattr(config, key, None)
    if val is None:
        return "—"
    if isinstance(val, (list, frozenset)):
        items = list(val)
        if not items:
            return "(пусто)"
        return ", ".join(str(i) for i in items)
    return str(val)


def _render_config_all() -> str:
    """Форматирует полный вывод !config."""
    lines: list[str] = ["**Конфигурация Краба** — все настройки", ""]
    for group_name, keys in _CONFIG_GROUPS:
        lines.append(f"**{group_name}**")
        for key, desc in keys:
            val = _render_config_value(key)
            lines.append(f"  `{key}` = `{val}` — {desc}")
        lines.append("")
    lines.append("Использование:")
    lines.append("`!config` — показать все")
    lines.append("`!config <KEY>` — одна настройка")
    lines.append("`!config <KEY> <value>` — установить")
    return "\n".join(lines)


async def handle_config(bot: "KraabUserbot", message: Message) -> None:
    """
    Просмотр и редактирование технических настроек Краба.

    !config                 — все ключевые настройки (сгруппировано)
    !config <KEY>           — показать значение одной настройки
    !config <KEY> <value>   — установить значение

    Принимает прямые CONFIG-ключи в любом регистре.
    Отличие от !set: !config охватывает ВСЕ системные настройки,
    !set — user-friendly алиасы.
    """
    raw_args = bot._get_command_args(message).strip() if hasattr(bot, "_get_command_args") else ""

    # Режим 1: показать все настройки
    if not raw_args:
        await message.reply(_render_config_all())
        return

    parts = raw_args.split(maxsplit=1)
    key_input = parts[0].upper()
    has_value = len(parts) > 1
    value_str = parts[1] if has_value else ""

    # Режим 2: показать одну настройку
    if not has_value:
        if hasattr(config, key_input):
            val = _render_config_value(key_input)
            desc = _CONFIG_KEY_DESC.get(key_input, "")
            suffix = f" — {desc}" if desc else ""
            await message.reply(f"`{key_input}` = `{val}`{suffix}")
        else:
            raise UserInputError(
                user_message=(
                    f"❓ Настройка `{key_input}` не найдена.\n"
                    "`!config` — показать все доступные настройки."
                )
            )
        return

    # Режим 3: установить значение
    if not hasattr(config, key_input):
        raise UserInputError(
            user_message=(
                f"❓ Настройка `{key_input}` не найдена.\n"
                "`!config` — показать все доступные настройки."
            )
        )

    ok = config.update_setting(key_input, value_str)
    if ok:
        new_val = _render_config_value(key_input)
        await message.reply(f"✅ `{key_input}` = `{new_val}`")
    else:
        await message.reply(f"❌ Не удалось обновить `{key_input}`. Проверь значение.")


##############################################################################
# !set — user-friendly алиасы для управления настройками
##############################################################################

_SET_ALIASES: dict[str, str] = {
    "stream_interval": "TELEGRAM_STREAM_UPDATE_INTERVAL_SEC",
    "reactions": "TELEGRAM_REACTIONS_ENABLED",
    "weather_city": "DEFAULT_WEATHER_CITY",
    "language": "_TRANSLATOR_LANGUAGE",  # особый: через translator-профиль
    "autodel": "_AUTODEL_DEFAULT",  # особый: глобальный default autodel
}

# (алиас → (config_key_или_метка, описание))
_SET_FRIENDLY: dict[str, tuple[str, str]] = {
    "stream_interval": ("TELEGRAM_STREAM_UPDATE_INTERVAL_SEC", "Интервал стриминга (сек)"),
    "reactions": ("TELEGRAM_REACTIONS_ENABLED", "Реакции 👀✅❌ (on/off)"),
    "weather_city": ("DEFAULT_WEATHER_CITY", "Город погоды по умолчанию"),
    "autodel": ("_AUTODEL_DEFAULT", "Автоудаление ответов (сек, 0=выкл)"),
    "language": ("_TRANSLATOR_LANGUAGE", "Языковая пара переводчика"),
}


def _get_set_value(bot: "KraabUserbot", alias: str) -> str:
    """Возвращает текущее значение управляемой настройки по алиасу."""
    # _AUTODEL_STATE_KEY живёт в command_handlers; получаем через lazy proxy
    _autodel_key: str = getattr(_ch, "_AUTODEL_STATE_KEY", "autodel_settings")
    if alias == "autodel":
        state: dict = getattr(bot, "_runtime_state", {}) or {}
        settings: dict = state.get(_autodel_key, {})
        val = settings.get("_default", 0)
        return str(val) if val else "0 (выключен)"
    if alias == "language":
        fn = getattr(bot, "get_translator_runtime_profile", None)
        profile: dict = fn() if callable(fn) else {}
        return str(profile.get("language_pair", "es-ru"))
    config_key = _SET_ALIASES.get(alias, alias.upper())
    return str(getattr(config, config_key, "—"))


def _render_all_settings(bot: "KraabUserbot") -> str:
    """Формирует текст со всеми управляемыми настройками."""
    lines = ["⚙️ **Управляемые настройки** (`!set`):", ""]
    for alias, (_, description) in _SET_FRIENDLY.items():
        value = _get_set_value(bot, alias)
        lines.append(f"- `{alias}` = `{value}` — {description}")
    lines.append("")
    lines.append("Использование:")
    lines.append("`!set` — показать всё")
    lines.append("`!set <key>` — показать одну настройку")
    lines.append("`!set <key> <value>` — установить")
    lines.append("")
    lines.append(
        "Также поддерживаются RAW ключи config: `!set TELEGRAM_STREAM_UPDATE_INTERVAL_SEC 3`"
    )
    return "\n".join(lines)


async def handle_set(bot: "KraabUserbot", message: Message) -> None:
    """
    Управление настройками Краба из Telegram.

    !set                    — показать все управляемые настройки
    !set <key>              — показать значение одной настройки
    !set <key> <value>      — установить значение

    Поддерживаемые алиасы:
      stream_interval  → TELEGRAM_STREAM_UPDATE_INTERVAL_SEC
      reactions        → TELEGRAM_REACTIONS_ENABLED (on/off)
      weather_city     → DEFAULT_WEATHER_CITY
      autodel          → глобальный default autodel (сек, 0=выкл)
      language         → языковая пара переводчика (es-ru, en-ru, ...)

    Также принимаются прямые CONFIG-ключи (upper-case).
    """
    # lazy lookup — позволяет тестам патчить _AUTODEL_STATE_KEY через _ch namespace
    _autodel_key: str = getattr(_ch, "_AUTODEL_STATE_KEY", "autodel_settings")

    raw_args = bot._get_command_args(message).strip() if hasattr(bot, "_get_command_args") else ""
    if not raw_args:
        await message.reply(_render_all_settings(bot))
        return

    parts = raw_args.split(maxsplit=1)
    alias_or_key = parts[0].lower()
    has_value = len(parts) > 1
    value_str = parts[1] if has_value else ""

    # Режим 2: показать одну настройку
    if not has_value:
        if alias_or_key in _SET_FRIENDLY:
            val = _get_set_value(bot, alias_or_key)
            desc = _SET_FRIENDLY[alias_or_key][1]
            await message.reply(f"⚙️ `{alias_or_key}` = `{val}` — {desc}")
        else:
            config_key = alias_or_key.upper()
            if hasattr(config, config_key):
                val = str(getattr(config, config_key))
                await message.reply(f"⚙️ `{config_key}` = `{val}`")
            else:
                raise UserInputError(
                    user_message=f"❓ Настройка `{alias_or_key}` не найдена.\n`!set` — список всех настроек."
                )
        return

    # Режим 3: установить значение
    if alias_or_key == "autodel":
        try:
            delay = float(value_str)
        except ValueError:
            raise UserInputError(
                user_message="❌ `autodel` принимает число секунд (0 = выключить)."
            )
        if not hasattr(bot, "_runtime_state") or bot._runtime_state is None:
            bot._runtime_state = {}
        autodel_settings: dict = bot._runtime_state.setdefault(_autodel_key, {})
        if delay <= 0:
            autodel_settings.pop("_default", None)
            await message.reply("✅ Автоудаление по умолчанию **выключено**.")
        else:
            autodel_settings["_default"] = delay
            await message.reply(f"✅ Автоудаление по умолчанию: **{delay:.0f} сек**.")
        return

    if alias_or_key == "language":
        if not hasattr(bot, "update_translator_runtime_profile"):
            raise UserInputError(user_message="❌ Translator не инициализирован.")
        value_norm = value_str.strip().lower()
        try:
            from ...core.translator_runtime_profile import ALLOWED_LANGUAGE_PAIRS

            if value_norm != "auto-detect" and value_norm not in ALLOWED_LANGUAGE_PAIRS:
                pairs = ", ".join(sorted(ALLOWED_LANGUAGE_PAIRS))
                raise UserInputError(
                    user_message=(
                        f"❌ Неизвестная языковая пара `{value_norm}`.\n"
                        f"Доступны: `{pairs}`, `auto-detect`."
                    )
                )
            profile = bot.update_translator_runtime_profile(language_pair=value_norm, persist=True)
            new_pair = profile.get("language_pair", value_norm)
            await message.reply(f"✅ Языковая пара переводчика: `{new_pair}`")
        except UserInputError:
            raise
        except Exception as exc:  # noqa: BLE001
            await message.reply(f"❌ Ошибка установки языковой пары: {str(exc)[:120]}")
        return

    # Стандартный путь: алиас → CONFIG-ключ или прямой ключ
    config_key = _SET_ALIASES.get(alias_or_key, alias_or_key.upper())
    if config_key.startswith("_"):
        # Спец-ключ без config-атрибута — не должен сюда попасть (autodel/language выше)
        raise UserInputError(
            user_message=f"❓ Настройка `{alias_or_key}` не найдена.\n`!set` — список всех настроек."
        )

    if config.update_setting(config_key, value_str):
        extra = ""
        if config_key == "SCHEDULER_ENABLED" and hasattr(bot, "_sync_scheduler_runtime"):
            try:
                bot._sync_scheduler_runtime()
                sched_state = "ON" if bool(getattr(config, "SCHEDULER_ENABLED", False)) else "OFF"
                extra = f"\n⏰ Scheduler runtime: `{sched_state}`"
            except Exception as exc:  # noqa: BLE001
                extra = f"\n⚠️ Scheduler sync warning: `{str(exc)[:120]}`"
        display_name = alias_or_key if alias_or_key in _SET_ALIASES else config_key
        await message.reply(f"✅ `{display_name}` обновлено!{extra}")
    else:
        await message.reply("❌ Ошибка обновления.")


##############################################################################
# !acl — управление runtime ACL userbot
##############################################################################


async def handle_acl(bot: "KraabUserbot", message: Message) -> None:
    """
    Управление runtime ACL userbot.

    Доступно только owner-контуру.
    """
    access_profile = bot._get_access_profile(message.from_user)
    if access_profile.level != AccessLevel.OWNER:
        raise UserInputError(
            user_message=(
                "🔒 Управление ACL доступно только владельцу.\n"
                "Можно попросить владельца выдать full или partial доступ."
            )
        )

    raw_args = bot._get_command_args(message).strip()
    parts = raw_args.split()
    action = str(parts[0] or "status").strip().lower() if parts else "status"
    state = load_acl_runtime_state()

    def _render_state() -> str:
        full_items = state.get(AccessLevel.FULL.value, [])
        partial_items = state.get(AccessLevel.PARTIAL.value, [])
        owner_items = state.get(AccessLevel.OWNER.value, [])
        return (
            "🛂 **Runtime ACL userbot**\n"
            "-----------------------\n"
            f"- Файл: `{config.USERBOT_ACL_FILE}`\n"
            f"- Владелец (effective): `{get_effective_owner_label()}`\n"
            f"- Fallback owner_username (config): `{config.OWNER_USERNAME}`\n"
            f"- Owner в runtime-файле: `{', '.join(owner_items) if owner_items else '-'}`\n"
            f"- Full: `{', '.join(full_items) if full_items else '-'}`\n"
            f"- Partial: `{', '.join(partial_items) if partial_items else '-'}`\n"
            f"- Partial-команды: `{', '.join(sorted(PARTIAL_ACCESS_COMMANDS))}`\n\n"
            "Команды:\n"
            "- `!acl status`\n"
            "- `!acl grant full @username`\n"
            "- `!acl grant partial @username`\n"
            "- `!acl revoke full @username`\n"
            "- `!acl revoke partial @username`\n"
            "- `!acl list`"
        )

    if action in {"", "status", "list"}:
        await message.reply(_render_state())
        return

    if action not in {"grant", "revoke"}:
        raise UserInputError(
            user_message=(
                "❌ Неизвестное действие ACL.\nИспользуй: `status`, `list`, `grant`, `revoke`."
            )
        )

    if len(parts) < 3:
        raise UserInputError(
            user_message=(
                "❌ Формат ACL-команды:\n"
                "- `!acl grant full @username`\n"
                "- `!acl grant partial 123456789`\n"
                "- `!acl revoke full @username`"
            )
        )

    level = str(parts[1] or "").strip().lower()
    subject = str(parts[2] or "").strip()
    if level not in {AccessLevel.FULL.value, AccessLevel.PARTIAL.value}:
        raise UserInputError(user_message="❌ Можно изменять только уровни `full` и `partial`.")

    result = update_acl_subject(level, subject, add=(action == "grant"))
    state = result["state"]
    verb = "выдан" if action == "grant" else "снят"
    changed_note = "обновлено" if result["changed"] else "без изменений"
    await message.reply(
        "✅ ACL обновлён.\n"
        f"- Уровень: `{level}`\n"
        f"- Subject: `{result['subject']}`\n"
        f"- Результат: `{verb}` / {changed_note}\n"
        f"- Full: `{', '.join(state.get('full', [])) if state.get('full') else '-'}`\n"
        f"- Partial: `{', '.join(state.get('partial', [])) if state.get('partial') else '-'}`"
    )


##############################################################################
# !scope — управление ACL из Telegram
##############################################################################


async def handle_scope(bot: "KraabUserbot", message: Message) -> None:
    """
    Управление ACL правами из Telegram через команду !scope.

    Без аргументов — показывает ACL-уровень отправителя (доступно любому).
    Подкоманды (owner-only): grant, revoke, list.

    Синтаксис:
    - !scope                         — мой текущий уровень доступа
    - !scope grant <user_id> full    — выдать full доступ
    - !scope grant <user_id> partial — выдать partial доступ
    - !scope revoke <user_id>        — отозвать все уровни (full + partial)
    - !scope list                    — список всех ACL-записей
    """
    raw_args = bot._get_command_args(message).strip()
    parts = raw_args.split()
    action = parts[0].strip().lower() if parts else ""

    # Без аргументов — показываем уровень доступа отправителя (доступно всем)
    if not action:
        user = message.from_user
        profile = bot._get_access_profile(user)
        user_id = str(getattr(user, "id", "") or "")
        username = str(getattr(user, "username", "") or "")
        display = f"@{username}" if username else f"id:{user_id}"
        level_emoji = {
            AccessLevel.OWNER: "👑",
            AccessLevel.FULL: "🔓",
            AccessLevel.PARTIAL: "🔑",
            AccessLevel.GUEST: "👤",
        }.get(profile.level, "❓")
        await message.reply(
            f"🛂 **Уровень доступа**: {level_emoji} `{profile.level.value}`\n"
            f"- Пользователь: {display}\n"
            f"- Источник ACL: `{profile.source}`\n\n"
            "Используй `!scope grant <user_id> full|partial` чтобы выдать права.\n"
            "Используй `!scope revoke <user_id>` чтобы отозвать.\n"
            "Используй `!scope list` для просмотра всех ACL-записей."
        )
        return

    # list — только owner
    if action == "list":
        access_profile = bot._get_access_profile(message.from_user)
        if access_profile.level != AccessLevel.OWNER:
            raise UserInputError(user_message="🔒 `!scope list` доступен только владельцу.")
        state = load_acl_runtime_state()
        owner_items = state.get(AccessLevel.OWNER.value, [])
        full_items = state.get(AccessLevel.FULL.value, [])
        partial_items = state.get(AccessLevel.PARTIAL.value, [])
        await message.reply(
            "🛂 **Все ACL-записи userbot**\n"
            "--------------------------\n"
            f"👑 Owner: `{', '.join(owner_items) if owner_items else '-'}`\n"
            f"🔓 Full:  `{', '.join(full_items) if full_items else '-'}`\n"
            f"🔑 Partial: `{', '.join(partial_items) if partial_items else '-'}`\n\n"
            f"Effective owner: `{get_effective_owner_label()}`"
        )
        return

    # grant / revoke — только owner
    access_profile = bot._get_access_profile(message.from_user)
    if access_profile.level != AccessLevel.OWNER:
        raise UserInputError(
            user_message=("🔒 Управление правами `!scope grant/revoke` доступно только владельцу.")
        )

    if action == "grant":
        if len(parts) < 3:
            raise UserInputError(
                user_message=(
                    "❌ Формат: `!scope grant <user_id> full|partial`\n"
                    "Пример: `!scope grant 123456789 full`"
                )
            )
        subject = parts[1].strip()
        level_raw = parts[2].strip().lower()
        if level_raw not in {AccessLevel.FULL.value, AccessLevel.PARTIAL.value}:
            raise UserInputError(user_message="❌ Уровень должен быть `full` или `partial`.")
        result = update_acl_subject(level_raw, subject, add=True)
        state = result["state"]
        changed_note = "обновлено" if result["changed"] else "без изменений"
        await message.reply(
            f"✅ Доступ выдан.\n"
            f"- Subject: `{result['subject']}`\n"
            f"- Уровень: `{level_raw}`\n"
            f"- Статус: {changed_note}\n"
            f"- Full: `{', '.join(state.get('full', [])) if state.get('full') else '-'}`\n"
            f"- Partial: `{', '.join(state.get('partial', [])) if state.get('partial') else '-'}`"
        )
        return

    if action == "revoke":
        if len(parts) < 2:
            raise UserInputError(
                user_message=(
                    "❌ Формат: `!scope revoke <user_id>`\nПример: `!scope revoke 123456789`"
                )
            )
        subject = parts[1].strip()
        removed_from: list[str] = []
        for level_val in (AccessLevel.FULL.value, AccessLevel.PARTIAL.value):
            res = update_acl_subject(level_val, subject, add=False)
            if res["changed"]:
                removed_from.append(level_val)
        if removed_from:
            removed_str = ", ".join(f"`{lvl}`" for lvl in removed_from)
            await message.reply(
                f"✅ Доступ отозван.\n"
                f"- Subject: `{normalize_subject(subject)}`\n"
                f"- Удалён из уровней: {removed_str}"
            )
        else:
            await message.reply(
                f"ℹ️ Subject `{normalize_subject(subject)}` не найден ни в одном уровне ACL.\n"
                "Ничего не изменено."
            )
        return

    raise UserInputError(
        user_message=(
            "❌ Неизвестное действие. Доступные подкоманды:\n"
            "- `!scope` — мой уровень доступа\n"
            "- `!scope grant <user_id> full|partial`\n"
            "- `!scope revoke <user_id>`\n"
            "- `!scope list`"
        )
    )


##############################################################################
# !reasoning — просмотр скрытой reasoning-trace
##############################################################################


async def handle_reasoning(bot: "KraabUserbot", message: Message) -> None:
    """
    Показывает скрытую reasoning-trace отдельно от основного ответа.

    Это owner/debug-команда: мысли не идут в обычный ответ, но владелец может
    посмотреть последний скрытый trace по явному запросу.
    """
    # _split_text_for_telegram живёт в command_handlers (multi-use)
    _split = getattr(_ch, "_split_text_for_telegram", None)

    args = str(bot._get_command_args(message) or "").strip().lower()
    chat_id = str(getattr(getattr(message, "chat", None), "id", "") or "")

    if args in {"clear", "reset"}:
        cleared = bool(bot.clear_hidden_reasoning_trace_snapshot(chat_id))
        await message.reply(
            "🧼 Скрытая reasoning-trace для этого чата очищена."
            if cleared
            else "🧼 Для этого чата пока нечего очищать: reasoning-trace ещё не накоплена."
        )
        return

    trace = bot.get_hidden_reasoning_trace_snapshot(chat_id)
    if not trace:
        await message.reply(
            "🧠 Для этого чата пока нет сохранённой reasoning-trace.\n"
            "Сначала дождись обычного ответа Краба, а потом вызови `!reasoning`."
        )
        return

    lines = [
        "🧠 **Скрытая reasoning-trace**",
        f"- Updated: `{trace.get('updated_at') or '-'}`",
        f"- Transport: `{trace.get('transport_mode') or 'unknown'}`",
        f"- Route: `{trace.get('route_channel') or '-'} / {trace.get('route_model') or '-'}`",
        f"- Query: `{trace.get('query') or '-'}`",
        f"- Preview: `{trace.get('answer_preview') or '-'}`",
    ]
    if not bool(trace.get("available")):
        lines.append(
            "⚠️ Для последнего ответа отдельный reasoning-блок не пришёл: "
            "провайдер вернул только финальный текст или скрытые мысли не доехали до транспорта."
        )
        await message.reply("\n".join(lines))
        return

    body = "\n".join(lines + ["", str(trace.get("reasoning") or "").strip()])
    if _split is not None:
        for chunk in _split(body):
            await message.reply(chunk)
    else:
        await message.reply(body[:3900])


##############################################################################
# !role — смена системного промпта (личности)
##############################################################################


async def handle_role(bot: "KraabUserbot", message: Message) -> None:
    """Смена системного промпта (личности)."""
    args = message.text.split()
    if len(args) < 2 or args[1] == "list":
        await message.reply(f"🎭 **Роли:**\n{list_roles()}")
    else:
        role = args[1] if len(args) == 2 else args[2]
        if role in ROLES:
            bot.current_role = role
            await message.reply(f"🎭 Теперь я: `{role}`")
        else:
            raise UserInputError(user_message="❌ Роль не найдена.")


##############################################################################
# !notify — управление streaming tool notifications
##############################################################################


async def handle_notify(bot: "KraabUserbot", message: Message) -> None:
    """
    Управление streaming tool notifications в Telegram.

    !notify on  — показывать какой инструмент вызывается (🔍 Ищу..., 📸 Скриншот...)
    !notify off — не показывать (чище, меньше сообщений)
    !notify     — текущий статус
    """
    from ...config import config as _cfg

    args = bot._get_command_args(message).strip().lower()
    if args in {"on", "1", "true", "yes"}:
        _cfg.update_setting("TOOL_NARRATION_ENABLED", "1")
        await message.reply(
            "🔔 Tool notifications: **ON**\nБуду показывать какой инструмент вызываю."
        )
    elif args in {"off", "0", "false", "no"}:
        _cfg.update_setting("TOOL_NARRATION_ENABLED", "0")
        await message.reply(
            "🔕 Tool notifications: **OFF**\nИнструменты молча, только финальный ответ."
        )
    else:
        status = "ON ✅" if getattr(_cfg, "TOOL_NARRATION_ENABLED", True) else "OFF 🔕"
        await message.reply(
            f"🔔 Tool notifications: **{status}**\n\n"
            "Команды:\n"
            "`!notify on` — показывать инструменты\n"
            "`!notify off` — скрыть инструменты"
        )


##############################################################################
# !chatban — управление persisted chat ban cache
##############################################################################


def _render_chat_ban_entries(entries: list[dict[str, Any]]) -> str:
    """Форматирует список chat ban cache записей для `!chatban status`."""
    if not entries:
        return (
            "🚫 **Chat ban cache** пуст.\n"
            "Записи создаются автоматически при `USER_BANNED_IN_CHANNEL` / "
            "`ChatWriteForbidden`. Ручное управление: `!chatban clear <chat_id>`."
        )
    lines: list[str] = [f"🚫 **Chat ban cache ({len(entries)}):**"]
    for entry in entries:
        chat_id = entry.get("chat_id", "—")
        code = entry.get("last_error_code") or entry.get("error_code") or "?"
        expires = entry.get("expires_at") or "permanent"
        hits = entry.get("hit_count", 1)
        lines.append(f"- `{chat_id}` · {code} · expires={expires} · hits={hits}")
    lines.append("\nСнять: `!chatban clear <chat_id>`")
    return "\n".join(lines)


async def handle_chatban(bot: "KraabUserbot", message: Message) -> None:
    """
    Управление persisted chat ban cache (B.8).

    Когда Telegram возвращает `USER_BANNED_IN_CHANNEL` / `ChatWriteForbidden`
    на попытку отправить сообщение, Краб помечает чат в cache. Пока запись
    активна, Краб вообще не обрабатывает входящие из этого чата —
    не гоняет LLM и не пытается `send_message`. Это защищает от:
    - бессмысленных Gemini-токенов на ответы которые не дойдут;
    - повторных API-вызовов в забаненный чат, которые Telegram считает
      агрессивным паттерном и за которые продлевает SpamBot limit.

    Subcommands:
    - `!chatban` / `!chatban status` — показать текущие записи;
    - `!chatban clear <chat_id>` — убрать конкретную запись вручную
      (если уверен что ban снят).
    """
    args = str(message.text or "").split()
    sub = args[1].strip().lower() if len(args) >= 2 else "status"

    if sub in {"", "status", "show", "list"}:
        entries = chat_ban_cache.list_entries()
        await message.reply(_render_chat_ban_entries(entries))
        return

    if sub == "clear":
        if len(args) < 3 or not args[2].strip():
            raise UserInputError(user_message="❌ Укажи chat_id: `!chatban clear <chat_id>`")
        target = args[2].strip()
        removed = chat_ban_cache.clear(target)
        if removed:
            await message.reply(
                f"✅ Убрал `{target}` из chat ban cache. "
                f"Краб снова будет обрабатывать сообщения оттуда."
            )
        else:
            await message.reply(f"ℹ️ `{target}` не был в chat ban cache (уже снят или не помечен).")
        return

    raise UserInputError(
        user_message="❌ Неизвестная подкоманда chatban. Используй `!chatban status` или `!chatban clear <chat_id>`."
    )


##############################################################################
# !cap — управление Policy Matrix (capability overrides)
##############################################################################


async def handle_cap(bot: "KraabUserbot", message: Message) -> None:
    """Управление Policy Matrix — горячий toggle capabilities.

    Использование:
      !cap                     — список текущих оверрайдов + все валидные capability
      !cap <capability> on     — включить capability для всех ролей
      !cap <capability> off    — выключить capability для всех ролей
      !cap reset               — сбросить все оверрайды (вернуть дефолты)
    """
    del bot
    from ...core.capability_registry import (
        _VALID_CAPABILITIES,  # type: ignore[attr-defined]
        clear_capability_overrides,
        get_capability_overrides,
        set_capability_override,
    )

    raw_parts = str(message.text or "").split()
    sub = raw_parts[1].lower().strip() if len(raw_parts) > 1 else ""

    if not sub or sub == "list":
        overrides = get_capability_overrides()
        valid = sorted(_VALID_CAPABILITIES)
        lines = ["🎛 **Policy Matrix — capability overrides**"]
        if overrides:
            lines.append("")
            lines.append("**Активные оверрайды:**")
            for cap, val in sorted(overrides.items()):
                icon = "✅" if val else "🚫"
                lines.append(f"  {icon} `{cap}`")
        else:
            lines.append("_(оверрайды не заданы — используются дефолты ролей)_")
        lines.append("")
        lines.append("**Все capabilities:**")
        lines.append("`" + "`, `".join(valid) + "`")
        lines.append("")
        lines.append("📝 `!cap <name> on/off` · `!cap reset`")
        await message.reply("\n".join(lines))
        return

    if sub == "reset":
        clear_capability_overrides()
        await message.reply("♻️ Все оверрайды сброшены — используются дефолты ролей.")
        return

    # !cap <capability> on/off
    if len(raw_parts) < 3:
        raise UserInputError(user_message="🎛 `!cap <capability> on|off`")
    cap_name = sub
    action = raw_parts[2].lower().strip()
    if action not in ("on", "off"):
        raise UserInputError(user_message="🎛 Значение должно быть `on` или `off`")

    result = set_capability_override(cap_name, action == "on")
    if "error" in result:
        valid = sorted(_VALID_CAPABILITIES)
        await message.reply(
            f"❌ `{cap_name}` — неизвестная capability.\nДоступные: `{'`, `'.join(valid)}`"
        )
        return

    icon = "✅" if action == "on" else "🚫"
    await message.reply(f"{icon} `{cap_name}` → **{action.upper()}** (все роли)")


##############################################################################
# !silence — режим тишины
##############################################################################


async def handle_silence(bot: "KraabUserbot", message: Message) -> None:
    """!тишина — управление режимом тишины.

    Синтаксис:
      !тишина               — toggle текущего чата (30 мин)
      !тишина 15            — mute текущего чата на 15 минут
      !тишина стоп          — снять mute текущего чата
      !тишина глобально     — глобальный mute (60 мин)
      !тишина глобально 30  — глобальный mute на 30 мин
      !тишина статус        — показать все активные mutes
      !тишина расписание 23:00-08:00 — ночной режим по расписанию
      !тишина расписание статус      — статус расписания
      !тишина расписание выкл        — отключить расписание
    """
    from ...core.silence_mode import silence_manager
    from ...core.silence_schedule import silence_schedule_manager

    chat_id = str(message.chat.id)
    raw = (message.text or "").strip()
    parts = raw.split(maxsplit=1)
    args = parts[1].strip().lower() if len(parts) > 1 else ""

    # Расписание ночного режима
    if args.startswith("расписание"):
        sched_arg = args[len("расписание") :].strip()
        if not sched_arg or sched_arg == "статус":
            await message.reply(silence_schedule_manager.format_status())
            return
        if sched_arg in ("выкл", "off", "стоп"):
            silence_schedule_manager.disable_schedule()
            await message.reply("🌙 Расписание тишины **отключено**.")
            return
        if "-" in sched_arg:
            time_parts = sched_arg.split("-", 1)
            if len(time_parts) == 2:
                start_s, end_s = time_parts[0].strip(), time_parts[1].strip()
                try:
                    silence_schedule_manager.set_schedule(start_s, end_s)
                    active_marker = (
                        " (сейчас активно ✅)"
                        if silence_schedule_manager.is_schedule_active()
                        else ""
                    )
                    await message.reply(
                        f"🌙 Расписание тишины установлено: **{start_s}–{end_s}**{active_marker}\n"
                        f"Краб будет молчать в эти часы.\n"
                        f"`!тишина расписание выкл` — отключить"
                    )
                    return
                except ValueError as exc:
                    await message.reply(f"❌ {exc}")
                    return
        await message.reply("❌ Неверный формат. Пример: `!тишина расписание 23:00-08:00`")
        return

    if args in ("статус", "status"):
        await message.reply(silence_manager.format_status())
        return

    if args in ("on", "off"):
        if args == "off":
            was_chat = silence_manager.unmute_chat(chat_id)
            was_global = silence_manager.unmute_global()
            if was_chat or was_global:
                await message.reply("🔊 Тишина снята.")
            else:
                await message.reply("ℹ️ Тишина не была активна.")
            return
        minutes = int(getattr(config, "SILENCE_DEFAULT_MINUTES", 30))
        silence_manager.mute_chat(chat_id, minutes)
        await message.reply(f"🤫 Тишина в этом чате на **{minutes}** мин.")
        return

    if args.startswith("стоп"):
        was_chat = silence_manager.unmute_chat(chat_id)
        was_global = silence_manager.unmute_global()
        if was_chat or was_global:
            await message.reply("🔊 Тишина снята.")
        else:
            await message.reply("ℹ️ Тишина не была активна.")
        return

    if args.startswith("глобально"):
        rest = args.replace("глобально", "").strip()
        minutes = (
            int(rest) if rest.isdigit() else int(getattr(config, "SILENCE_DEFAULT_MINUTES", 60))
        )
        silence_manager.mute_global(minutes)
        await message.reply(f"🤫 Глобальная тишина на **{minutes}** мин.")
        return

    # Per-chat: toggle или с указанием минут
    if silence_manager.is_chat_muted(chat_id):
        silence_manager.unmute_chat(chat_id)
        await message.reply("🔊 Тишина в этом чате снята.")
        return

    minutes = int(args) if args.isdigit() else int(getattr(config, "SILENCE_DEFAULT_MINUTES", 30))
    silence_manager.mute_chat(chat_id, minutes)
    await message.reply(f"🤫 Тишина в этом чате на **{minutes}** мин.\n`!тишина стоп` чтобы снять.")


##############################################################################
# !archive / !unarchive — архивация чатов
##############################################################################


async def handle_archive(bot: "KraabUserbot", message: Message) -> None:
    """
    Архивация и разархивация чатов. Owner-only.

    Форматы:
      !archive          — архивировать текущий чат
      !unarchive        — разархивировать текущий чат
      !archive list     — показать список архивированных чатов (до 20)
      !archive stats    — статистика archive.db (размер, кол-во, чанки)
      !archive growth   — рост archive.db за период
    """
    access_profile = bot._get_access_profile(message.from_user)
    if access_profile.level != AccessLevel.OWNER:
        raise UserInputError(user_message="🔒 `!archive` доступен только владельцу.")

    args = bot._get_command_args(message).strip().lower()

    if args == "list":
        try:
            archived = []
            async for dialog in bot.client.get_dialogs(folder_id=1):
                chat = dialog.chat
                title = (
                    getattr(chat, "title", None)
                    or getattr(chat, "first_name", None)
                    or str(chat.id)
                )
                archived.append(f"• `{chat.id}` — {title}")
                if len(archived) >= 20:
                    break
        except Exception as exc:
            reply = f"❌ Не удалось получить архив: `{exc}`"
        else:
            if archived:
                lines = ["📦 **Архивированные чаты** (до 20):"] + archived
                reply = "\n".join(lines)
            else:
                reply = "📦 Архив пуст."

    elif args == "stats":
        import sqlite3

        from src.core.archive_growth_monitor import ARCHIVE_DB

        if not ARCHIVE_DB.exists():
            reply = "📊 archive.db не найден — Memory Layer не инициализирован."
        else:
            size_mb = ARCHIVE_DB.stat().st_size / 1024 / 1024
            try:
                conn = sqlite3.connect(f"file:{ARCHIVE_DB}?mode=ro", uri=True)
                msg_count = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
                try:
                    chunk_count = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
                except sqlite3.OperationalError:
                    chunk_count = 0
                try:
                    last_row = conn.execute("SELECT MAX(date) FROM messages").fetchone()[0]
                    last_write = last_row if last_row else "—"
                except sqlite3.OperationalError:
                    last_write = "—"
                conn.close()
                reply = (
                    f"📊 **Archive.db stats**\n"
                    f"• Размер: `{size_mb:.2f} MB`\n"
                    f"• Сообщений: `{msg_count:,}`\n"
                    f"• Чанков: `{chunk_count:,}`\n"
                    f"• Последнее сообщение: `{last_write}`"
                )
            except Exception as exc:
                reply = f"❌ Ошибка чтения archive.db: `{exc}`"

    elif args == "growth":
        from src.core.archive_growth_monitor import growth_summary, take_snapshot

        snap = take_snapshot()
        summary = growth_summary()
        if snap is None:
            reply = "📈 archive.db не найден — данные недоступны."
        elif isinstance(summary.get("summary"), str):
            reply = (
                f"📈 **Archive.db growth**\n"
                f"• Снапшотов: `{summary['snapshots']}`\n"
                f"• Текущий размер: `{snap.size_mb:.2f} MB`\n"
                f"• Сообщений сейчас: `{snap.message_count:,}`\n"
                f"• {summary['summary']}"
            )
        else:
            reply = (
                f"📈 **Archive.db growth** (за {summary['days_tracked']:.1f} дн.)\n"
                f"• Снапшотов: `{summary['snapshots']}`\n"
                f"• Размер: `{summary['first_size_mb']:.2f}` → `{summary['latest_size_mb']:.2f} MB`\n"
                f"• Рост: `+{summary['growth_mb_per_day']:.2f} MB/день`\n"
                f"• Сообщений: `{summary['latest_messages']:,}` "
                f"(+{summary['growth_messages_per_day']:.0f}/день)"
            )

    else:
        chat_id = message.chat.id
        try:
            await bot.client.archive_chats(chat_id)
            reply = "📦 Чат добавлен в архив."
        except Exception as exc:
            reply = f"❌ Не удалось архивировать: `{exc}`"

    if message.from_user and message.from_user.id == bot.me.id:
        await message.edit(reply)
    else:
        await message.reply(reply)


async def handle_unarchive(bot: "KraabUserbot", message: Message) -> None:
    """
    Разархивирует текущий чат. Owner-only.

    Формат:
      !unarchive        — разархивировать текущий чат
    """
    access_profile = bot._get_access_profile(message.from_user)
    if access_profile.level != AccessLevel.OWNER:
        raise UserInputError(user_message="🔒 `!unarchive` доступен только владельцу.")

    chat_id = message.chat.id
    try:
        await bot.client.unarchive_chats(chat_id)
        reply = "📤 Чат извлечён из архива."
    except Exception as exc:
        reply = f"❌ Не удалось разархивировать: `{exc}`"

    if message.from_user and message.from_user.id == bot.me.id:
        await message.edit(reply)
    else:
        await message.reply(reply)
