# -*- coding: utf-8 -*-
"""
admin_commands - Phase 2 Wave 11 extraction (Session 27).

Административные команды и их private helpers:
  !config, !set, !acl, !scope, !reasoning, !role, !notify,
  !chatban, !block, !unblock, !blocklist, !cap, !silence (!тишина),
  !costs, !models, !budget, !digest, !archive, !unarchive,
  !trust, !proactivity, !setpanelauth.

Re-exported from command_handlers.py для обратной совместимости (тесты,
external imports `from src.handlers.command_handlers import handle_config`).
"""

from __future__ import annotations

import os
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
from ...core.command_blocklist import command_blocklist
from ...core.cost_analytics import cost_analytics
from ...core.exceptions import UserInputError
from ...core.logger import get_logger
from ...core.telegram_buttons import build_costs_detail_buttons
from ...core.weekly_digest import weekly_digest
from ...employee_templates import ROLES, list_roles

if TYPE_CHECKING:
    from ...userbot_bridge import KraabUserbot

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# !reasoning использует _split_text_for_telegram из command_handlers
# (остаётся там как multi-use). Lazy import при вызове.
# ---------------------------------------------------------------------------


# ─────────────────────────────────────────────────────────────────────────────
# !config — просмотр и редактирование технических настроек
# ─────────────────────────────────────────────────────────────────────────────

# Группы ключей для !config
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


# ─────────────────────────────────────────────────────────────────────────────
# !set — управление настройками по алиасам
# ─────────────────────────────────────────────────────────────────────────────

# Алиасы !set: короткое имя → реальный ключ config (или специальный режим)
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
    # Ленивый импорт ключа _AUTODEL_STATE_KEY из scheduler_commands
    from ..commands.scheduler_commands import _AUTODEL_STATE_KEY  # noqa: PLC0415

    if alias == "autodel":
        state: dict = getattr(bot, "_runtime_state", {}) or {}
        settings: dict = state.get(_AUTODEL_STATE_KEY, {})
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
    from ..commands.scheduler_commands import _AUTODEL_STATE_KEY  # noqa: PLC0415

    raw_args = bot._get_command_args(message).strip() if hasattr(bot, "_get_command_args") else ""
    if not raw_args:
        # Режим 1: показать все настройки
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
        autodel_settings: dict = bot._runtime_state.setdefault(_AUTODEL_STATE_KEY, {})
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
            from ...core.translator_runtime_profile import ALLOWED_LANGUAGE_PAIRS  # noqa: PLC0415

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
        # Спец-ключ без config-атрибута — не должен сюда попасть (autodel/language уже выше)
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


# ─────────────────────────────────────────────────────────────────────────────
# !acl — управление runtime ACL
# ─────────────────────────────────────────────────────────────────────────────


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


# ─────────────────────────────────────────────────────────────────────────────
# !scope — управление ACL правами
# ─────────────────────────────────────────────────────────────────────────────


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
        # !scope grant <user_id> full|partial
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
        # !scope revoke <user_id> — удаляет из ВСЕХ уровней (full + partial)
        if len(parts) < 2:
            raise UserInputError(
                user_message=(
                    "❌ Формат: `!scope revoke <user_id>`\nПример: `!scope revoke 123456789`"
                )
            )
        subject = parts[1].strip()
        # Удаляем из full и partial (owner нельзя отозвать через !scope)
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


# ─────────────────────────────────────────────────────────────────────────────
# !reasoning — скрытая reasoning-trace
# ─────────────────────────────────────────────────────────────────────────────


async def handle_reasoning(bot: "KraabUserbot", message: Message) -> None:
    """
    Показывает скрытую reasoning-trace отдельно от основного ответа.

    Это owner/debug-команда: мысли не идут в обычный ответ, но владелец может
    посмотреть последний скрытый trace по явному запросу.
    """
    # Ленивый импорт чтобы не тянуть весь command_handlers при старте
    from .. import command_handlers as _ch  # noqa: PLC0415

    _split = _ch._split_text_for_telegram

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
    for chunk in _split(body):
        await message.reply(chunk)


# ─────────────────────────────────────────────────────────────────────────────
# !role — смена системного промпта
# ─────────────────────────────────────────────────────────────────────────────


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


# ─────────────────────────────────────────────────────────────────────────────
# !notify — управление tool notifications
# ─────────────────────────────────────────────────────────────────────────────


async def handle_notify(bot: "KraabUserbot", message: Message) -> None:
    """
    Управление streaming tool notifications в Telegram.

    !notify on  — показывать какой инструмент вызывается (🔍 Ищу..., 📸 Скриншот...)
    !notify off — не показывать (чище, меньше сообщений)
    !notify     — текущий статус
    """
    from ...config import config as _cfg  # noqa: PLC0415

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


# ─────────────────────────────────────────────────────────────────────────────
# !chatban — управление chat ban cache
# ─────────────────────────────────────────────────────────────────────────────


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


# ─────────────────────────────────────────────────────────────────────────────
# !block / !unblock / !blocklist — per-chat command blocklist
# ─────────────────────────────────────────────────────────────────────────────


async def handle_cmdblock(bot: "KraabUserbot", message: Message) -> None:
    """!block <cmd> — заблокировать команду в текущем чате (owner-only).

    Краб будет молча игнорировать команду в этом чате (silent skip, без ошибки).
    Полезно когда в чате есть другой бот с тем же триггером.
    """
    access_profile = bot._get_access_profile(message.from_user)
    if access_profile.level != AccessLevel.OWNER:
        raise UserInputError(user_message="❌ Только owner.")

    args = str(message.text or "").split()
    if len(args) < 2 or not args[1].strip():
        raise UserInputError(user_message="❌ Укажи команду: `!block <cmd>` (без префикса)")

    cmd = args[1].strip()
    chat_id = message.chat.id
    added = command_blocklist.add_block(chat_id, cmd)
    if added:
        await message.reply(
            f"✅ Команда `!{cmd}` заблокирована в этом чате. "
            f"Краб будет молча её игнорировать.\n"
            f"`!blocklist` — посмотреть все блоки."
        )
    else:
        await message.reply(f"ℹ️ `!{cmd}` уже была в blocklist для этого чата.")


async def handle_cmdunblock(bot: "KraabUserbot", message: Message) -> None:
    """!unblock <cmd> — убрать команду из blocklist текущего чата (owner-only)."""
    access_profile = bot._get_access_profile(message.from_user)
    if access_profile.level != AccessLevel.OWNER:
        raise UserInputError(user_message="❌ Только owner.")

    args = str(message.text or "").split()
    if len(args) < 2 or not args[1].strip():
        raise UserInputError(user_message="❌ Укажи команду: `!unblock <cmd>`")

    cmd = args[1].strip()
    chat_id = message.chat.id
    removed = command_blocklist.remove_block(chat_id, cmd)
    if removed:
        await message.reply(f"✅ `!{cmd}` убрана из blocklist. Краб снова будет обрабатывать её.")
    else:
        await message.reply(f"ℹ️ `!{cmd}` не была в blocklist для этого чата.")


async def handle_blocklist(bot: "KraabUserbot", message: Message) -> None:
    """!blocklist [<chat_id>] — показать per-chat command blocklist (owner-only).

    Без аргументов — все блоки текущего чата.
    С аргументом `all` — все чаты.
    """
    access_profile = bot._get_access_profile(message.from_user)
    if access_profile.level != AccessLevel.OWNER:
        raise UserInputError(user_message="❌ Только owner.")

    args = str(message.text or "").split()
    show_all = len(args) >= 2 and args[1].strip().lower() == "all"

    if show_all:
        all_blocks = command_blocklist.list_blocks()
        if not all_blocks:
            await message.reply("ℹ️ Command blocklist пуст.")
            return
        lines = ["**Command Blocklist (все чаты):**"]
        for key, cmds in all_blocks.items():
            label = "global (*)" if key == "*" else f"chat `{key}`"
            lines.append(f"  {label}: `{'`, `'.join(cmds)}`")
        await message.reply("\n".join(lines))
    else:
        chat_id = message.chat.id
        blocks = command_blocklist.list_blocks(chat_id)
        global_blocks = command_blocklist.list_blocks("*")
        if not blocks and not global_blocks:
            await message.reply(f"ℹ️ Blocklist для чата `{chat_id}` пуст.\nДобавить: `!block <cmd>`")
            return
        lines = [f"**Command Blocklist** (чат `{chat_id}`):"]
        if global_blocks:
            lines.append(f"  global (*): `{'`, `'.join(global_blocks)}`")
        if blocks:
            lines.append(f"  этот чат: `{'`, `'.join(blocks)}`")
        lines.append("\nУбрать: `!unblock <cmd>`")
        await message.reply("\n".join(lines))


# ─────────────────────────────────────────────────────────────────────────────
# !cap — управление Policy Matrix
# ─────────────────────────────────────────────────────────────────────────────


async def handle_cap(bot: "KraabUserbot", message: Message) -> None:
    """Управление Policy Matrix — горячий toggle capabilities.

    Использование:
      !cap                     — список текущих оверрайдов + все валидные capability
      !cap <capability> on     — включить capability для всех ролей
      !cap <capability> off    — выключить capability для всех ролей
      !cap reset               — сбросить все оверрайды (вернуть дефолты)
    """
    del bot
    from ...core.capability_registry import (  # noqa: PLC0415
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


# ─────────────────────────────────────────────────────────────────────────────
# !тишина (!silence) — управление режимом тишины
# ─────────────────────────────────────────────────────────────────────────────


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
    from ...core.silence_mode import silence_manager  # noqa: PLC0415
    from ...core.silence_schedule import silence_schedule_manager  # noqa: PLC0415

    chat_id = str(message.chat.id)
    raw = (message.text or "").strip()
    # Убираем префикс команды
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
        # Ожидаем формат HH:MM-HH:MM
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


# ─────────────────────────────────────────────────────────────────────────────
# !costs — cost report
# ─────────────────────────────────────────────────────────────────────────────


def _costs_filter_calls(calls: list, *, days: int | None = None) -> list:
    """Вернуть вызовы за последние `days` дней (None = все)."""
    if days is None:
        return calls
    import time as _time  # noqa: PLC0415

    cutoff = _time.time() - days * 86400
    return [r for r in calls if r.timestamp >= cutoff]


def _costs_aggregate(calls: list) -> dict:
    """Агрегировать список CallRecord в сводку."""
    from collections import defaultdict as _dd  # noqa: PLC0415

    by_model: dict = _dd(lambda: {"cost_usd": 0.0, "calls": 0, "tokens": 0})
    by_provider: dict = _dd(lambda: {"cost_usd": 0.0, "calls": 0})
    total_cost = 0.0
    total_tokens = 0
    for r in calls:
        total_cost += r.cost_usd
        total_tokens += r.input_tokens + r.output_tokens
        by_model[r.model_id]["cost_usd"] += r.cost_usd
        by_model[r.model_id]["calls"] += 1
        by_model[r.model_id]["tokens"] += r.input_tokens + r.output_tokens
        # Провайдер — часть до «/»
        provider = r.model_id.split("/")[0] if "/" in r.model_id else r.model_id
        by_provider[provider]["cost_usd"] += r.cost_usd
        by_provider[provider]["calls"] += 1
    return {
        "total_cost": total_cost,
        "total_tokens": total_tokens,
        "calls_count": len(calls),
        "by_model": dict(by_model),
        "by_provider": dict(by_provider),
    }


def _costs_ascii_trend(calls: list, days: int = 30) -> str:
    """Построить ASCII-график расходов за последние `days` дней."""
    import datetime  # noqa: PLC0415

    now = datetime.date.today()
    # Сгруппировать вызовы по дате
    daily: dict[datetime.date, float] = {}
    for d in range(days):
        day = now - datetime.timedelta(days=days - 1 - d)
        daily[day] = 0.0
    for r in calls:
        day = datetime.date.fromtimestamp(r.timestamp)
        if day in daily:
            daily[day] += r.cost_usd

    values = [daily[d] for d in sorted(daily)]
    max_v = max(values) if values else 0.0
    total = sum(values)
    avg = total / len(values) if values else 0.0

    bars = " ▁▂▃▄▅▆▇█"

    def _bar(v: float) -> str:
        if max_v == 0:
            return " "
        idx = round((v / max_v) * (len(bars) - 1))
        return bars[idx]

    bar_line = "".join(_bar(v) for v in values)

    lines = [
        f"📈 **Тренд за {days} дней ($):**",
        f"`{bar_line}`",
        f"↑ {days}d ago {'·' * max(0, len(values) - 10)} ↑ today",
        f"avg=${avg:.2f}/d · total=${total:.4f}",
    ]
    return "\n".join(lines)


async def _handle_costs_today(bot: "KraabUserbot", message: Message) -> None:
    """!costs today — расходы за сегодня."""
    calls = _costs_filter_calls(getattr(cost_analytics, "_calls", []), days=1)
    agg = _costs_aggregate(calls)
    lines = [
        "💰 **Costs: сегодня**",
        "─────────────────",
        f"Вызовов: {agg['calls_count']} | Токенов: {agg['total_tokens']}",
        f"Стоимость: ${agg['total_cost']:.4f}",
    ]
    if agg["by_model"]:
        lines.append("")
        lines.append("**По моделям:**")
        for mid, data in sorted(agg["by_model"].items(), key=lambda x: -x[1]["cost_usd"]):
            lines.append(f"• {mid}: ${data['cost_usd']:.4f} ({data['calls']} calls)")
    await message.reply("\n".join(lines))


async def _handle_costs_week(bot: "KraabUserbot", message: Message) -> None:
    """!costs week — расходы за 7 дней."""
    calls = _costs_filter_calls(getattr(cost_analytics, "_calls", []), days=7)
    agg = _costs_aggregate(calls)
    lines = [
        "💰 **Costs: 7 дней**",
        "─────────────────",
        f"Вызовов: {agg['calls_count']} | Токенов: {agg['total_tokens']}",
        f"Стоимость: ${agg['total_cost']:.4f}",
        f"Средняя в день: ${agg['total_cost'] / 7:.4f}",
    ]
    if agg["by_model"]:
        lines.append("")
        lines.append("**По моделям:**")
        for mid, data in sorted(agg["by_model"].items(), key=lambda x: -x[1]["cost_usd"]):
            lines.append(f"• {mid}: ${data['cost_usd']:.4f} ({data['calls']} calls)")
    await message.reply("\n".join(lines))


async def _handle_costs_breakdown(bot: "KraabUserbot", message: Message) -> None:
    """!costs breakdown — разбивка по провайдерам."""
    calls = getattr(cost_analytics, "_calls", [])
    agg = _costs_aggregate(calls)
    lines = [
        "💰 **Costs: по провайдерам**",
        "─────────────────",
        f"Всего: ${agg['total_cost']:.4f} | Вызовов: {agg['calls_count']}",
        "",
        "**По провайдерам:**",
    ]
    total = agg["total_cost"] or 1.0  # защита от деления на 0
    for provider, data in sorted(agg["by_provider"].items(), key=lambda x: -x[1]["cost_usd"]):
        pct = round(data["cost_usd"] / total * 100, 1)
        lines.append(f"• {provider}: ${data['cost_usd']:.4f} ({pct}%, {data['calls']} calls)")
    if agg["by_model"]:
        lines.append("")
        lines.append("**По моделям:**")
        for mid, data in sorted(agg["by_model"].items(), key=lambda x: -x[1]["cost_usd"])[:8]:
            lines.append(f"• {mid}: ${data['cost_usd']:.4f} ({data['calls']} calls)")
    await message.reply("\n".join(lines))


async def _handle_costs_budget(bot: "KraabUserbot", message: Message) -> None:
    """!costs budget — бюджет vs фактические расходы."""
    budget = cost_analytics.get_monthly_budget_usd()
    cost_month = cost_analytics.get_monthly_cost_usd()
    cost_session = cost_analytics.get_cost_so_far_usd()
    forecast = cost_analytics.monthly_calls_forecast()
    lines = [
        "💰 **Costs: бюджет**",
        "─────────────────",
        f"Сессия: ${cost_session:.4f}",
        f"Месяц: ${cost_month:.4f}",
    ]
    if budget > 0:
        pct = min(100, round(cost_month / budget * 100, 1))
        remaining = max(0.0, budget - cost_month)
        bar_filled = round(pct / 10)
        bar = "█" * bar_filled + "░" * (10 - bar_filled)
        lines += [
            f"Бюджет: ${budget:.2f}",
            f"[{bar}] {pct}%",
            f"Остаток: ${remaining:.4f}",
        ]
        if pct >= 90:
            lines.append("⚠️ Бюджет почти исчерпан!")
        elif pct >= 75:
            lines.append("⚡ Более 75% бюджета израсходовано.")
    else:
        lines.append("Бюджет: не задан (`!budget 10.00` чтобы задать)")
    if forecast is not None:
        lines.append(f"Прогноз вызовов (конец месяца): ~{int(forecast)}")
    await message.reply("\n".join(lines))


async def _handle_costs_trend(bot: "KraabUserbot", message: Message) -> None:
    """!costs trend — ASCII-тренд за 30 дней."""
    calls = getattr(cost_analytics, "_calls", [])
    trend_text = _costs_ascii_trend(calls, days=30)
    await message.reply(trend_text)


async def handle_costs(bot: "KraabUserbot", message: Message) -> None:
    """!costs [today|week|breakdown|budget|trend] — cost report (owner-only)."""
    # Проверка: только владелец
    access_profile = bot._get_access_profile(message.from_user)
    if access_profile.level != AccessLevel.OWNER:
        raise UserInputError(user_message="🔒 Команда доступна только владельцу.")

    args = (bot._get_command_args(message) or "").strip().lower()
    sub = args.split()[0] if args else ""

    if sub == "today":
        return await _handle_costs_today(bot, message)
    if sub == "week":
        return await _handle_costs_week(bot, message)
    if sub == "breakdown":
        return await _handle_costs_breakdown(bot, message)
    if sub == "budget":
        return await _handle_costs_budget(bot, message)
    if sub == "trend":
        return await _handle_costs_trend(bot, message)

    # Default — текущий month summary
    report = cost_analytics.build_usage_report_dict()

    cost_session = report.get("cost_session_usd", 0.0)
    budget = report.get("monthly_budget_usd") or 0.0
    cost_month = report.get("cost_month_usd", 0.0)
    total_calls = len(getattr(cost_analytics, "_calls", []))
    total_tokens = report.get("total_tokens", 0)
    total_fallbacks = report.get("total_fallbacks", 0)
    total_tool_calls = report.get("total_tool_calls", 0)
    by_model: dict = report.get("by_model", {})
    by_channel: dict = report.get("by_channel", {})

    # Процент бюджета
    if budget > 0:
        pct = min(100, round(cost_month / budget * 100, 1))
        budget_line = f"Бюджет: ${budget:.2f} ({pct}% использовано)"
    else:
        budget_line = "Бюджет: не задан"

    lines = [
        "💰 **Cost Report**",
        "─────────────────",
        f"Потрачено: ${cost_session:.4f}",
        budget_line,
        f"Вызовов: {total_calls} | Токенов: {total_tokens}",
        f"Fallbacks: {total_fallbacks} | Tool calls: {total_tool_calls}",
    ]

    if by_model:
        lines.append("")
        lines.append("**По моделям:**")
        for mid, data in sorted(by_model.items(), key=lambda x: -x[1].get("cost_usd", 0)):
            lines.append(f"• {mid}: ${data.get('cost_usd', 0):.4f} ({data.get('calls', 0)} calls)")

    if by_channel:
        lines.append("")
        lines.append("**По каналам:**")
        ch_parts = [f"{ch}: {cnt}" for ch, cnt in sorted(by_channel.items())]
        lines.append("• " + " | ".join(ch_parts))

    await message.reply("\n".join(lines), reply_markup=build_costs_detail_buttons())


# ─────────────────────────────────────────────────────────────────────────────
# !models — распределение вызовов по тирам
# ─────────────────────────────────────────────────────────────────────────────


async def handle_models(bot: "KraabUserbot", message: Message) -> None:
    """!models — распределение вызовов по тирам моделей (owner-only)."""
    access_profile = bot._get_access_profile(message.from_user)
    if access_profile.level != AccessLevel.OWNER:
        raise UserInputError(user_message="🔒 Команда доступна только владельцу.")

    from ...core.model_tier_tracker import (  # noqa: PLC0415
        format_tier_summary_text,
        get_tier_summary,
    )

    args = (bot._get_command_args(message) or "").strip().lower()
    # Поддерживаем !models 48 (часов) и !models week
    hours = 24.0
    if args == "week":
        hours = 168.0
    elif args == "month":
        hours = 720.0
    else:
        try:
            hours = float(args)
        except ValueError:
            pass

    calls = getattr(cost_analytics, "_calls", [])
    summary = get_tier_summary(calls, since_hours=hours)
    period_label = {24.0: "24ч", 168.0: "неделя", 720.0: "месяц"}.get(hours, f"{hours:.0f}ч")
    text = format_tier_summary_text(summary).replace("за 24ч", f"за {period_label}")
    await message.reply(text)


# ─────────────────────────────────────────────────────────────────────────────
# !budget — управление месячным бюджетом
# ─────────────────────────────────────────────────────────────────────────────


async def handle_budget(bot: "KraabUserbot", message: Message) -> None:
    """!budget [сумма] — показать или установить месячный бюджет (owner-only)."""
    # Проверка: только владелец
    access_profile = bot._get_access_profile(message.from_user)
    if access_profile.level != AccessLevel.OWNER:
        raise UserInputError(user_message="🔒 Команда доступна только владельцу.")

    raw_args = bot._get_command_args(message).strip()

    if not raw_args:
        # Показать текущий бюджет
        current = cost_analytics.get_monthly_budget_usd()
        cost_month = cost_analytics.get_monthly_cost_usd()
        if current > 0:
            pct = min(100, round(cost_month / current * 100, 1))
            remaining = max(0.0, current - cost_month)
            await message.reply(
                f"💳 **Месячный бюджет:** ${current:.2f}\n"
                f"Потрачено: ${cost_month:.4f} ({pct}%)\n"
                f"Осталось: ${remaining:.4f}"
            )
        else:
            await message.reply(
                f"💳 **Месячный бюджет:** не задан\n"
                f"Потрачено за месяц: ${cost_month:.4f}\n"
                f"Чтобы задать: `!budget 10.00`"
            )
        return

    # Установить новый бюджет
    try:
        new_budget = float(raw_args.replace(",", "."))
    except ValueError:
        raise UserInputError(
            user_message=f"❌ Некорректное значение: `{raw_args}`. Укажи число, например `!budget 15.00`."
        )

    if new_budget < 0:
        raise UserInputError(user_message="❌ Бюджет не может быть отрицательным.")

    cost_analytics._monthly_budget_usd = new_budget

    if new_budget == 0:
        await message.reply("✅ Месячный бюджет сброшен (без ограничений).")
    else:
        await message.reply(f"✅ Месячный бюджет установлен: **${new_budget:.2f}**")


# ─────────────────────────────────────────────────────────────────────────────
# !digest — немедленный daily + weekly digest
# ─────────────────────────────────────────────────────────────────────────────


async def handle_digest(bot: "KraabUserbot", message: Message) -> None:
    """
    !digest — немедленно сгенерировать и отправить daily + weekly digest (owner-only).

    Отправляет:
    1. Nightly Summary (daily) — данные за сегодня.
    2. Weekly Digest — данные за 7 дней (swarm/cost/inbox сводка).
    """
    # Проверка: только владелец
    access_profile = bot._get_access_profile(message.from_user)
    if access_profile.level != AccessLevel.OWNER:
        raise UserInputError(user_message="🔒 Команда доступна только владельцу.")

    await message.reply("⏳ Генерирую digest...")

    # --- Nightly (daily) summary ---
    try:
        from ...core.nightly_summary import generate_summary  # noqa: PLC0415

        daily_text = await generate_summary()
        await message.reply(daily_text, parse_mode="markdown")
    except Exception as exc:  # noqa: BLE001
        logger.warning("handle_digest_nightly_failed", error=str(exc))
        await message.reply(f"⚠️ Daily summary не удался: {exc}")

    # --- Weekly digest ---
    try:
        result = await weekly_digest.generate_digest()
    except Exception as exc:  # noqa: BLE001
        logger.warning("handle_digest_weekly_failed", error=str(exc))
        await message.reply(f"❌ Weekly digest не удался: {exc}")
        return

    if not result.get("ok"):
        err = result.get("error", "неизвестная ошибка")
        await message.reply(f"❌ Weekly digest не удался: {err}")
        return

    rounds = result.get("total_rounds", 0)
    cost = result.get("cost_week_usd", 0.0)
    attention = result.get("attention_count", 0)

    if not weekly_digest._telegram_callback:
        await message.reply(
            f"✅ **Weekly Digest**\n"
            f"Swarm rounds (7д): {rounds}\n"
            f"Cost (7д): ${cost:.4f}\n"
            f"Attention items: {attention}"
        )
    else:
        await message.reply(
            f"✅ Weekly digest отправлен.\n"
            f"Rounds: {rounds} | Cost 7д: ${cost:.4f} | Attention: {attention}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# !archive / !unarchive — архивация чатов
# ─────────────────────────────────────────────────────────────────────────────


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
        # Получаем список архивированных диалогов
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
        # Статистика archive.db: размер, кол-во сообщений, чанков, последняя запись
        import sqlite3  # noqa: PLC0415

        from src.core.archive_growth_monitor import ARCHIVE_DB  # noqa: PLC0415

        if not ARCHIVE_DB.exists():
            reply = "📊 archive.db не найден — Memory Layer не инициализирован."
        else:
            size_mb = ARCHIVE_DB.stat().st_size / 1024 / 1024
            try:
                conn = sqlite3.connect(f"file:{ARCHIVE_DB}?mode=ro", uri=True)
                msg_count = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
                # chunks могут отсутствовать если индексация не запускалась
                try:
                    chunk_count = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
                except sqlite3.OperationalError:
                    chunk_count = 0
                # последняя запись по полю date
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
        # Динамика роста archive.db из истории снапшотов
        from src.core.archive_growth_monitor import growth_summary, take_snapshot  # noqa: PLC0415

        snap = take_snapshot()
        summary = growth_summary()
        if snap is None:
            reply = "📈 archive.db не найден — данные недоступны."
        elif isinstance(summary.get("summary"), str):
            # Недостаточно данных
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
        # Архивируем текущий чат
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


# ─────────────────────────────────────────────────────────────────────────────
# !trust — управление trusted guests allowlist
# ─────────────────────────────────────────────────────────────────────────────

_TRUST_HELP = """\
🔐 **!trust** — управление allowlist доверенных гостей (W10.1 bypass)

Команды (owner-only):
  `!trust add @username [user_id]` — добавить в текущем чате
  `!trust remove @username`        — удалить из текущего чата
  `!trust list`                    — список для текущего чата
  `!trust list all`                — список всех чатов

По умолчанию: `@dodik_ggt` разрешена в YMB FAMILY FOREVER и How2AI.
Trusted guest получает LLM-ответ даже без @mention Краба в группе.
"""


async def handle_trust(bot: "KraabUserbot", message: Message) -> None:
    """
    !trust add|remove|list — управление trusted_guests allowlist.

    Owner-only. Позволяет legit friends (Дашка @dodik_ggt и др.) получать
    LLM-ответы в группах, минуя W10.1 guest XOR gate.
    """
    from ...core.trusted_guests import trusted_guests  # noqa: PLC0415

    access_profile = bot._get_access_profile(message.from_user)
    if access_profile.level != AccessLevel.OWNER:
        await message.reply("🔒 `!trust` доступен только владельцу.")
        return

    raw = str(message.text or "").strip()
    parts = raw.split(maxsplit=3)
    # parts[0] = "!trust", parts[1] = subcommand, parts[2] = @username, parts[3] = user_id
    sub = parts[1].strip().lower() if len(parts) > 1 else ""

    if not sub or sub == "help":
        await message.reply(_TRUST_HELP)
        return

    chat_id = message.chat.id

    # !trust list [all]
    if sub == "list":
        scope = parts[2].strip().lower() if len(parts) > 2 else ""
        if scope == "all":
            all_data = trusted_guests.all_chats()
            if not all_data:
                await message.reply("📋 Trusted guests: пусто.")
                return
            lines = ["📋 **Trusted guests (все чаты):**", ""]
            for cid, entry in all_data.items():
                uids = entry.get("user_ids", [])
                unames = entry.get("usernames", [])
                lines.append(f"**Chat** `{cid}`:")
                for uid in uids:
                    lines.append(f"  • user_id={uid}")
                for uname in unames:
                    lines.append(f"  • {uname}")
                lines.append("")
            await message.reply("\n".join(lines).rstrip())
            return

        entries = trusted_guests.list_trusted(chat_id)
        if not entries:
            await message.reply(f"📋 Trusted guests в `{chat_id}`: пусто.")
            return
        lines = [f"📋 **Trusted guests в чате `{chat_id}`:**", ""]
        for e in entries:
            uid = e.get("user_id")
            uname = e.get("username") or "—"
            uid_str = f"user_id={uid}" if uid else "user_id=?"
            lines.append(f"  • {uname} ({uid_str})")
        await message.reply("\n".join(lines))
        return

    # !trust add @username [user_id]
    if sub == "add":
        username_arg = parts[2].strip() if len(parts) > 2 else ""
        if not username_arg:
            await message.reply("❌ Формат: `!trust add @username [user_id]`")
            return
        user_id_arg = 0
        if len(parts) > 3:
            try:
                user_id_arg = int(parts[3].strip())
            except ValueError:
                await message.reply("❌ user_id должен быть числом.")
                return
        norm_uname = username_arg.lstrip("@").strip()
        trusted_guests.add_trusted(chat_id, user_id_arg, f"@{norm_uname}")
        uid_info = f", user_id={user_id_arg}" if user_id_arg else ""
        await message.reply(
            f"✅ `@{norm_uname}`{uid_info} добавлен в trusted guests чата `{chat_id}`.\n"
            f"Теперь получает LLM-ответы без @mention Краба."
        )
        return

    # !trust remove @username
    if sub == "remove":
        username_arg = parts[2].strip() if len(parts) > 2 else ""
        if not username_arg:
            await message.reply("❌ Формат: `!trust remove @username`")
            return
        norm_uname = username_arg.lstrip("@").strip()
        trusted_guests.remove_trusted(chat_id, 0, f"@{norm_uname}")
        await message.reply(f"🗑️ `@{norm_uname}` удалён из trusted guests чата `{chat_id}`.")
        return


# ─────────────────────────────────────────────────────────────────────────────
# !proactivity — управление уровнем проактивности
# ─────────────────────────────────────────────────────────────────────────────


async def handle_proactivity(bot: "KraabUserbot", message: Message) -> None:
    """!proactivity — управление уровнем проактивности Краба.

    Синтаксис:
      !proactivity                   — показать текущий уровень и настройки
      !proactivity <level>           — переключить уровень
      !proactivity help              — справка по уровням

    Уровни:
      silent    (0) — только explicit @mention; reactions off
      reactive  (1) — mention + reply-to-krab; contextual reactions
      attentive (2) — DEFAULT: implicit triggers threshold 0.7, normal autonomy
      engaged   (3) — threshold 0.5, chatty autonomy, follow-up 5 мин
      proactive (4) — threshold 0.3, unsolicited thoughts
    """
    from ...core.proactivity import (  # noqa: PLC0415
        ProactivityLevel,
        allows_unsolicited,
        get_autonomy_mode,
        get_level,
        get_reactions_mode,
        get_trigger_threshold,
        set_level,
    )

    raw = (message.text or "").strip()
    parts = raw.split(maxsplit=1)
    arg = parts[1].strip().lower() if len(parts) > 1 else ""

    # Без аргументов — показать статус
    if not arg or arg == "status" or arg == "статус":
        lv = get_level()
        lines = [
            f"⚡ **Proactivity Level**: `{lv.value}` ({lv.name.lower()})",
            f"🤖 Autonomy mode: `{get_autonomy_mode()}`",
            f"📊 Trigger threshold: `{get_trigger_threshold()}`",
            f"💬 Reactions mode: `{get_reactions_mode()}`",
            f"💡 Unsolicited thoughts: `{'on' if allows_unsolicited() else 'off'}`",
            "",
            "Уровни: `silent` `reactive` `attentive` `engaged` `proactive`",
        ]
        await message.reply("\n".join(lines))
        return

    # Справка
    if arg in ("help", "помощь", "?"):
        help_text = (
            "**!proactivity — уровни активности Краба:**\n\n"
            "`silent` (0) — только explicit @mention; reactions off\n"
            "`reactive` (1) — mention + reply-to-krab; contextual reactions\n"
            "`attentive` (2) — **DEFAULT**: implicit 0.7, normal autonomy\n"
            "`engaged` (3) — implicit 0.5, chatty autonomy, follow-up 5 мин\n"
            "`proactive` (4) — implicit 0.3, unsolicited thoughts\n\n"
            "Пример: `!proactivity engaged`"
        )
        await message.reply(help_text)
        return

    # Переключение уровня
    valid = {lv.name.lower() for lv in ProactivityLevel} | {
        str(lv.value) for lv in ProactivityLevel
    }
    if arg not in valid:
        await message.reply(
            f"❌ Неизвестный уровень `{arg}`.\n"
            "Доступные: `silent`, `reactive`, `attentive`, `engaged`, `proactive` (или 0–4)"
        )
        return

    set_level(arg)
    lv = get_level()
    await message.reply(
        f"✅ Proactivity переключён: **{lv.name.lower()}** ({lv.value})\n"
        f"Autonomy: `{get_autonomy_mode()}` | Threshold: `{get_trigger_threshold()}`"
    )


# ─────────────────────────────────────────────────────────────────────────────
# !setpanelauth — bcrypt-пароль для Krab Panel
# ─────────────────────────────────────────────────────────────────────────────


async def handle_setpanelauth(bot: "KraabUserbot", message: Message) -> None:
    """Установить bcrypt-пароль для Krab Panel (owner-only).

    !setpanelauth <user> <pass>  — сгенерировать хэш и вывести env-переменные
    !setpanelauth status         — текущий статус KRAB_PANEL_AUTH
    !setpanelauth off            — показать команду для отключения

    Применение: добавить в .env и перезапустить Краба.
    """
    from ...core.access_control import is_owner  # noqa: PLC0415

    if not is_owner(message):
        return

    args = bot._get_command_args(message).strip()

    if args == "status":
        auth_enabled = os.getenv("KRAB_PANEL_AUTH", "") == "1"
        username = os.getenv("KRAB_PANEL_USERNAME", "krab")
        has_hash = bool(os.getenv("KRAB_PANEL_PASSWORD_HASH", ""))
        status = "включён" if auth_enabled else "выключен"
        hash_status = "задан" if has_hash else "НЕ задан"
        await message.reply(
            f"**Panel bcrypt auth:** {status}\nUsername: `{username}`\nPassword hash: {hash_status}"
        )
        return

    if args == "off":
        await message.reply(
            "Чтобы выключить auth — удалите из `.env`:\n"
            "```\nKRAB_PANEL_AUTH=1\n"
            "KRAB_PANEL_USERNAME=...\n"
            "KRAB_PANEL_PASSWORD_HASH=...\n```\n"
            "Затем перезапустите Краба."
        )
        return

    parts = args.split(None, 1)
    if len(parts) < 2:
        await message.reply(
            "Использование: `!setpanelauth <username> <password>`\n"
            "Пример: `!setpanelauth pablito myS3cr3t`"
        )
        return

    username, password = parts[0], parts[1]

    try:
        import bcrypt  # noqa: PLC0415

        hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12)).decode()
    except ImportError:
        await message.reply("bcrypt не установлен в окружении Краба")
        return

    env_block = (
        f"KRAB_PANEL_AUTH=1\nKRAB_PANEL_USERNAME={username}\nKRAB_PANEL_PASSWORD_HASH={hashed}"
    )
    await message.reply(
        f"Добавьте в `.env` и перезапустите Краба:\n\n```\n{env_block}\n```\n\n"
        "После перезапуска панель потребует Basic Auth с bcrypt-проверкой.\n"
        "_Сообщение с паролем будет автоматически удалено..._"
    )
    # Удалить исходное сообщение с паролем из истории чата
    try:
        await message.delete()
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

__all__ = [
    # config
    "_CONFIG_GROUPS",
    "_CONFIG_KEY_DESC",
    "_render_config_value",
    "_render_config_all",
    "handle_config",
    # set
    "_SET_ALIASES",
    "_SET_FRIENDLY",
    "_get_set_value",
    "_render_all_settings",
    "handle_set",
    # acl / scope
    "handle_acl",
    "handle_scope",
    # reasoning / role
    "handle_reasoning",
    "handle_role",
    # notify
    "handle_notify",
    # chatban / blocklist
    "_render_chat_ban_entries",
    "handle_chatban",
    "handle_cmdblock",
    "handle_cmdunblock",
    "handle_blocklist",
    # cap / silence
    "handle_cap",
    "handle_silence",
    # costs
    "_costs_filter_calls",
    "_costs_aggregate",
    "_costs_ascii_trend",
    "_handle_costs_today",
    "_handle_costs_week",
    "_handle_costs_breakdown",
    "_handle_costs_budget",
    "_handle_costs_trend",
    "handle_costs",
    # models / budget / digest
    "handle_models",
    "handle_budget",
    "handle_digest",
    # archive
    "handle_archive",
    "handle_unarchive",
    # trust / proactivity / setpanelauth
    "_TRUST_HELP",
    "handle_trust",
    "handle_proactivity",
    "handle_setpanelauth",
]
