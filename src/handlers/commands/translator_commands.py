# -*- coding: utf-8 -*-
"""
translator_commands - Phase 2 Wave 9 extraction (Session 27).

Команды переводчика (product-level runtime profile + быстрый перевод текста):
  !translator (status/help/on/off/lang/auto/mode/strategy/ordinary/internet/
               subtitles/timeline/summary/diagnostics/phrase/test/history/
               session/reset),
  !translate [lang] <текст>,
  !translate auto.

Re-exported from command_handlers.py для обратной совместимости (тесты,
external imports `from src.handlers.command_handlers import handle_translator`).

См. ``docs/CODE_SPLITS_PLAN.md`` Phase 2 - domain extractions.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pyrogram.types import Message

from ...core.exceptions import UserInputError
from ...core.logger import get_logger
from ...core.translator_runtime_profile import (
    ALLOWED_LANGUAGE_PAIRS,
    ALLOWED_TRANSLATION_MODES,
    ALLOWED_VOICE_STRATEGIES,
    default_translator_runtime_profile,
)

if TYPE_CHECKING:
    from ...userbot_bridge import KraabUserbot

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Render helpers
# ---------------------------------------------------------------------------


def _render_translator_profile(profile: dict[str, Any]) -> str:
    """Форматирует product-level translator runtime profile для Telegram-ответа."""
    language_pair = str(profile.get("language_pair") or "es-ru")
    mode = str(profile.get("translation_mode") or "bilingual")
    strategy = str(profile.get("voice_strategy") or "voice-first")
    target = str(profile.get("target_device") or "iphone_companion")
    quick_phrases = profile.get("quick_phrases") or []
    quick_phrase_count = int(profile.get("quick_phrase_count") or len(quick_phrases) or 0)
    voice_foundation = bool(profile.get("voice_foundation_ready"))
    voice_runtime = bool(profile.get("voice_runtime_enabled"))
    ordinary_calls = "ВКЛ" if bool(profile.get("ordinary_calls_enabled")) else "ВЫКЛ"
    internet_calls = "ВКЛ" if bool(profile.get("internet_calls_enabled")) else "ВЫКЛ"
    subtitles = "ВКЛ" if bool(profile.get("subtitles_enabled")) else "ВЫКЛ"
    timeline = "ВКЛ" if bool(profile.get("timeline_enabled")) else "ВЫКЛ"
    summary = "ВКЛ" if bool(profile.get("summary_enabled")) else "ВЫКЛ"
    diagnostics = "ВКЛ" if bool(profile.get("diagnostics_enabled")) else "ВЫКЛ"
    preview = ", ".join(f"`{item}`" for item in list(quick_phrases)[:3]) or "—"
    return (
        "🗣️ **Translator runtime**\n"
        f"- Языковая пара: `{language_pair}`\n"
        f"- Mode: `{mode}`\n"
        f"- Voice strategy: `{strategy}`\n"
        f"- Target device: `{target}`\n"
        f"- Ordinary calls: `{ordinary_calls}`\n"
        f"- Internet calls: `{internet_calls}`\n"
        f"- Subtitles / Timeline / Summary / Diagnostics: `{subtitles}` / `{timeline}` / `{summary}` / `{diagnostics}`\n"
        f"- Quick phrases: `{quick_phrase_count}`\n"
        f"- Voice foundation: `{'READY' if voice_foundation else 'DEGRADED'}`\n"
        f"- Voice runtime replies: `{'ВКЛ' if voice_runtime else 'ВЫКЛ'}`\n"
        f"- Preview: {preview}\n\n"
        "Команды:\n"
        "`!translator status`\n"
        "`!translator lang <es-ru|es-en|en-ru|auto-detect>`\n"
        "`!translator mode <bilingual|auto_to_ru|auto_to_en>`\n"
        "`!translator strategy <voice-first|subtitles-first>`\n"
        "`!translator ordinary <on|off>` / `!translator internet <on|off>`\n"
        "`!translator subtitles|timeline|summary|diagnostics <on|off>`\n"
        "`!translator phrase add <текст>` / `!translator phrase remove <номер>`\n"
        "`!translator reset`"
    )


def _render_translator_session_state(state: dict[str, Any]) -> str:
    """Форматирует translator session state для Telegram-ответа."""
    status = str(state.get("session_status") or "idle")
    muted = bool(state.get("translation_muted"))
    session_id = str(state.get("session_id") or "—")
    label = str(state.get("active_session_label") or "—")
    pair = str(state.get("language_pair") or state.get("last_language_pair") or "—")
    original = str(state.get("last_translated_original") or "—")
    translation = str(state.get("last_translated_translation") or "—")
    last_event = str(state.get("last_event") or "session_idle")
    updated_at = str(state.get("updated_at") or "—")
    timeline_summary = (
        state.get("timeline_summary") if isinstance(state.get("timeline_summary"), dict) else {}
    )
    preview = (
        state.get("timeline_preview") if isinstance(state.get("timeline_preview"), list) else []
    )
    preview_lines: list[str] = []
    for item in preview[:3]:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "session_updated")
        ts = str(item.get("ts") or "—")
        item_translation = str(item.get("translation") or item.get("original") or "").strip()
        suffix = f" · {item_translation}" if item_translation else ""
        preview_lines.append(f"- `{ts}` `{kind}`{suffix}")
    preview_block = "\n".join(preview_lines) if preview_lines else "- `timeline пока пуст`"
    return (
        "🎧 **Translator session**\n"
        f"- Состояние: `{status}`\n"
        f"- Translation muted: `{'YES' if muted else 'NO'}`\n"
        f"- Session id: `{session_id}`\n"
        f"- Session label: `{label}`\n"
        f"- Language pair: `{pair}`\n"
        f"- Last event: `{last_event}`\n"
        f"- Updated: `{updated_at}`\n"
        f"- Timeline events: `{timeline_summary.get('total', state.get('timeline_event_count', 0))}`\n"
        f"- Line / control: `{timeline_summary.get('line_events', 0)}` / `{timeline_summary.get('control_events', 0)}`\n"
        f"- Last original: `{original}`\n"
        f"- Last translation: `{translation}`\n"
        "Recent timeline:\n"
        f"{preview_block}\n\n"
        "Команды:\n"
        "`!translator session status`\n"
        "`!translator session start [label]`\n"
        "`!translator session pause`\n"
        "`!translator session resume`\n"
        "`!translator session stop`\n"
        "`!translator session mute` / `!translator session unmute`\n"
        "`!translator session replay <original> | <translation>`\n"
        "`!translator session clear`"
    )


def _parse_toggle_arg(raw: Any, *, field_name: str) -> bool:
    """Нормализует `on/off` аргумент для командных флагов translator.

    Дублирует общий ``_shared._parse_toggle_arg`` для совместимости с тестами,
    которые импортируют ``_parse_toggle_arg`` из ``command_handlers`` (см.
    ``test_command_handlers_unit.py``).
    """
    value = str(raw or "").strip().lower()
    if value == "on":
        return True
    if value == "off":
        return False
    raise UserInputError(user_message=f"❌ Для `{field_name}` поддерживаются только `on` и `off`.")


# ---------------------------------------------------------------------------
# !translate language aliases
# ---------------------------------------------------------------------------

# Поддерживаемые языки для быстрого указания через аргумент
_TRANSLATE_LANG_ALIASES: dict[str, str] = {
    "ru": "ru",
    "en": "en",
    "es": "es",
    "fr": "fr",
    "de": "de",
    "it": "it",
    "pt": "pt",
    "uk": "uk",
    "рус": "ru",
    "рu": "ru",
    "eng": "en",
    "spa": "es",
}


# ---------------------------------------------------------------------------
# !translator
# ---------------------------------------------------------------------------


async def handle_translator(bot: "KraabUserbot", message: Message) -> None:
    """
    Управление product-level translator runtime profile.

    Это не live call session-control. Команда управляет persisted owner-профилем,
    который потом читают owner UI, handoff и будущий iPhone companion flow.
    """
    # Парсим аргументы через message.command (Pyrogram заполняет его независимо от
    # prefix: "!translator status" и "Краб translator status" дают одинаковый
    # command=["translator","status"]). Fallback на split по text для тестов/совместимости.
    if getattr(message, "command", None):
        # message.command = ["translator", "sub", "arg2", "arg3"]
        # args[0]=command, args[1]=sub, args[2]=arg2, args[3]=arg3 — совместимо с дальнейшим кодом
        args = list(message.command)[:4]
        while len(args) < 4:
            args.append("")
    else:
        # fallback для тестов / нестандартных ситуаций
        raw_text = str(message.text or "").split(maxsplit=3)
        # Определяем смещение: если первое слово это command (начинается с !), offset=0 иначе 1
        if len(raw_text) >= 2 and not raw_text[0].startswith(("!", "/", ".")):
            # multi-word prefix: args[0]=prefix, args[1]=cmd, args[2]=sub, args[3]=arg2
            args = ["_cmd"] + raw_text[1:]
        else:
            args = raw_text
        while len(args) < 4:
            args.append("")
    sub = str(args[1] or "").strip().lower()
    if not sub or sub == "show":
        await message.reply(_render_translator_profile(bot.get_translator_runtime_profile()))
        return

    if sub == "status":
        # !translator status — показать и profile, и session state
        profile_text = _render_translator_profile(bot.get_translator_runtime_profile())
        session_text = _render_translator_session_state(bot.get_translator_session_state())
        await message.reply(f"{profile_text}\n\n{session_text}")
        return

    if sub in {"help", "?", "commands"}:
        await message.reply(
            "🔄 **Translator Commands:**\n"
            "`!translator` — текущий profile\n"
            "`!translator lang [pair]` — показать/сменить языковую пару\n"
            "`!translator auto` — переключить на auto-detect\n"
            "`!translator test <text>` — быстрый перевод текста\n"
            "`!translator history` — статистика переводов\n"
            "`!translator session start|stop|pause|resume|status` — управление сессией\n"
            "`!translator mode|strategy|phrase` — настройки профиля"
        )
        return

    if sub == "on":
        # !translator on → !translator session start (per-chat, opt-in)
        profile = bot.get_translator_runtime_profile()
        label = str(args[2] or "").strip() or None
        current_state = bot.get_translator_session_state()
        # Добавляем текущий чат в active_chats — translator строго per-chat opt-in
        current_chat_id = str(message.chat.id)
        active_chats = list(current_state.get("active_chats") or [])
        if current_chat_id not in active_chats:
            active_chats.append(current_chat_id)
        state = bot.update_translator_session_state(
            session_status="active",
            active_chats=active_chats,
            active_session_label=label,
            last_language_pair=profile.get("language_pair"),
            last_event="session_started",
            persist=True,
        )
        await message.reply(_render_translator_session_state(state))
        return

    if sub == "off":
        # !translator off → !translator session stop
        state = bot.update_translator_session_state(session_status="stopped", persist=True)
        await message.reply(_render_translator_session_state(state))
        return

    if sub == "auto":
        # !translator auto — shortcut для !translator lang auto-detect
        profile = bot.update_translator_runtime_profile(language_pair="auto-detect", persist=True)
        await message.reply(
            "🌐 Translator → **auto-detect** mode.\nЯзык будет определяться автоматически."
        )
        return

    if sub in {"lang", "language"}:
        if not str(args[2] or "").strip():
            # Без аргументов — показать текущую пару
            profile = bot.get_translator_runtime_profile()
            current = profile.get("language_pair", "es-ru")
            await message.reply(
                f"🌐 Текущая пара: **{current}**\n\n"
                f"Доступные: `es-ru`, `es-en`, `en-ru`, `auto-detect`\n"
                f"Сменить: `!translator lang auto-detect`"
            )
            return
        value = str(args[2] or "").strip().lower()
        if value not in ALLOWED_LANGUAGE_PAIRS:
            raise UserInputError(
                user_message="❌ Поддерживаются только `es-ru`, `es-en`, `en-ru`, `auto-detect`."
            )
        profile = bot.update_translator_runtime_profile(language_pair=value, persist=True)
        await message.reply(_render_translator_profile(profile))
        return

    if sub == "mode":
        if len(args) < 3:
            raise UserInputError(user_message="❌ Укажи mode: `!translator mode bilingual`.")
        value = str(args[2] or "").strip().lower()
        if value not in ALLOWED_TRANSLATION_MODES:
            raise UserInputError(
                user_message="❌ Поддерживаются только `bilingual`, `auto_to_ru`, `auto_to_en`."
            )
        profile = bot.update_translator_runtime_profile(translation_mode=value, persist=True)
        await message.reply(_render_translator_profile(profile))
        return

    if sub in {"strategy", "voice_strategy"}:
        if len(args) < 3:
            raise UserInputError(
                user_message="❌ Укажи strategy: `!translator strategy voice-first`."
            )
        value = str(args[2] or "").strip().lower()
        if value not in ALLOWED_VOICE_STRATEGIES:
            raise UserInputError(
                user_message="❌ Поддерживаются только `voice-first` и `subtitles-first`."
            )
        profile = bot.update_translator_runtime_profile(voice_strategy=value, persist=True)
        await message.reply(_render_translator_profile(profile))
        return

    toggle_fields = {
        "ordinary": "ordinary_calls_enabled",
        "internet": "internet_calls_enabled",
        "subtitles": "subtitles_enabled",
        "timeline": "timeline_enabled",
        "summary": "summary_enabled",
        "diagnostics": "diagnostics_enabled",
    }
    if sub in toggle_fields:
        if len(args) < 3:
            raise UserInputError(
                user_message=f"❌ Укажи состояние: `!translator {sub} on` или `!translator {sub} off`."
            )
        enabled = _parse_toggle_arg(args[2], field_name=f"!translator {sub}")
        profile = bot.update_translator_runtime_profile(
            **{toggle_fields[sub]: enabled},
            persist=True,
        )
        await message.reply(_render_translator_profile(profile))
        return

    if sub == "phrase":
        action = str(args[2] or "").strip().lower() if len(args) >= 3 else ""
        current = bot.get_translator_runtime_profile()
        quick_phrases = list(current.get("quick_phrases") or [])

        if action == "add":
            if len(args) < 4 or not str(args[3] or "").strip():
                raise UserInputError(
                    user_message="❌ Укажи фразу: `!translator phrase add Повтори медленнее`."
                )
            quick_phrases.append(str(args[3]).strip())
            profile = bot.update_translator_runtime_profile(
                quick_phrases=quick_phrases,
                persist=True,
            )
            await message.reply(_render_translator_profile(profile))
            return

        if action == "remove":
            if len(args) < 4:
                raise UserInputError(
                    user_message="❌ Укажи номер фразы: `!translator phrase remove 2`."
                )
            try:
                index = int(str(args[3]).strip()) - 1
            except ValueError as exc:
                raise UserInputError(
                    user_message="❌ Номер фразы должен быть целым числом."
                ) from exc
            if index < 0 or index >= len(quick_phrases):
                raise UserInputError(
                    user_message=f"❌ Нет фразы с номером `{index + 1}`. Сейчас их: `{len(quick_phrases)}`."
                )
            quick_phrases.pop(index)
            profile = bot.update_translator_runtime_profile(
                quick_phrases=quick_phrases,
                persist=True,
            )
            await message.reply(_render_translator_profile(profile))
            return

        raise UserInputError(
            user_message="❌ Используй `!translator phrase add <текст>` или `!translator phrase remove <номер>`."
        )

    if sub == "history":
        # !translator history [N] — последние N переводов из session state (default 5)
        # Парсим необязательный аргумент N
        raw_text_parts = str(message.text or "").split(None, 2)
        raw_n = raw_text_parts[2].strip() if len(raw_text_parts) > 2 else ""
        try:
            n_show = max(1, min(20, int(raw_n))) if raw_n.isdigit() else 5
        except (ValueError, TypeError):
            n_show = 5

        state = bot.get_translator_session_state()
        history: list[dict] = list(state.get("history") or [])
        recent = history[-n_show:] if history else []

        if not recent:
            await message.reply("📋 История переводов пуста.")
            return

        lines = ["📋 **Последние переводы:**", ""]
        for idx, entry in enumerate(reversed(recent), start=1):
            src = entry.get("src_lang", "?")
            tgt = entry.get("tgt_lang", "?")
            latency_s = entry.get("latency_ms", 0) / 1000
            orig = entry.get("original", "")[:120]
            trans = entry.get("translation", "")[:120]
            lines.append(f"{idx}. [{src}→{tgt}] {latency_s:.1f}s")
            lines.append(f'   "{orig}" → "{trans}"')
            lines.append("")
        await message.reply("\n".join(lines).rstrip())
        return

    if sub == "test":
        # !translator test <text> — быстрый inline перевод для тестирования
        # args split maxsplit=3 обрезает текст → берём всё после "!translator test "
        raw_text = str(message.text or "")
        test_idx = raw_text.lower().find("test")
        test_text = raw_text[test_idx + 4 :].strip() if test_idx >= 0 else ""
        if not test_text:
            raise UserInputError(user_message="❌ Формат: `!translator test Buenos días amigo`")
        try:
            from ...core.language_detect import detect_language, resolve_translation_pair
            from ...core.translator_engine import translate_text
            from ...openclaw_client import openclaw_client as _oc

            detected = detect_language(test_text)
            if not detected:
                await message.reply("❌ Не удалось определить язык.")
                return
            profile = bot.get_translator_runtime_profile()
            src, tgt = resolve_translation_pair(detected, profile.get("language_pair", "es-ru"))
            if src == tgt:
                await message.reply(f"ℹ️ Язык совпадает ({src}), перевод не нужен.")
                return
            result = await translate_text(test_text, src, tgt, openclaw_client=_oc)
            await message.reply(
                f"🔄 {src}→{tgt} ({result.latency_ms}ms)\n"
                f"**{result.original}**\n"
                f"_{result.translated}_"
            )
        except Exception as exc:
            await message.reply(f"❌ Ошибка: {str(exc)[:200]}")
        return

    if sub == "session":
        action = str(args[2] or "").strip().lower() if len(args) >= 3 else "status"
        if action in {"status", "show"}:
            await message.reply(
                _render_translator_session_state(bot.get_translator_session_state())
            )
            return
        if action == "start":
            label = str(args[3] or "").strip() if len(args) >= 4 else ""
            profile = bot.get_translator_runtime_profile()
            # Per-chat: добавляем chat_id в active_chats если не global
            current_chat_id = str(message.chat.id)
            current_state = bot.get_translator_session_state()
            active_chats = list(current_state.get("active_chats") or [])
            if current_chat_id not in active_chats:
                active_chats.append(current_chat_id)
            state = bot.update_translator_session_state(
                session_status="active",
                translation_muted=False,
                active_session_label=label,
                active_chats=active_chats,
                last_language_pair=profile.get("language_pair"),
                last_event="session_started",
                persist=True,
            )
            await message.reply(_render_translator_session_state(state))
            return
        if action == "pause":
            state = bot.update_translator_session_state(
                session_status="paused",
                last_event="session_paused",
                persist=True,
            )
            await message.reply(_render_translator_session_state(state))
            return
        if action == "resume":
            state = bot.update_translator_session_state(
                session_status="active",
                last_event="session_resumed",
                persist=True,
            )
            await message.reply(_render_translator_session_state(state))
            return
        if action == "stop":
            # Per-chat: убираем этот чат из active_chats
            current_chat_id = str(message.chat.id)
            current_state = bot.get_translator_session_state()
            active_chats = [
                c for c in (current_state.get("active_chats") or []) if c != current_chat_id
            ]
            # Если active_chats пуст — полный stop; иначе только deactivate этот чат
            if not active_chats:
                state = bot.update_translator_session_state(
                    session_status="idle",
                    translation_muted=False,
                    active_session_label="",
                    active_chats=[],
                    last_event="session_stopped",
                    persist=True,
                )
            else:
                state = bot.update_translator_session_state(
                    active_chats=active_chats,
                    last_event=f"chat_removed:{current_chat_id}",
                    persist=True,
                )
            await message.reply(_render_translator_session_state(state))
            return
        if action == "clear":
            state = bot.update_translator_session_state(
                clear_timeline=True,
                persist=True,
            )
            await message.reply(_render_translator_session_state(state))
            return
        if action in {"mute", "unmute"}:
            state = bot.update_translator_session_state(
                translation_muted=(action == "mute"),
                last_event="translation_muted" if action == "mute" else "translation_unmuted",
                persist=True,
            )
            await message.reply(_render_translator_session_state(state))
            return
        if action == "replay":
            raw = str(args[3] or "").strip() if len(args) >= 4 else ""
            if "|" not in raw:
                raise UserInputError(
                    user_message="❌ Используй `!translator session replay original | translation`."
                )
            original, translation = [part.strip() for part in raw.split("|", 1)]
            if not original or not translation:
                raise UserInputError(user_message="❌ Для replay нужны и original, и translation.")
            profile = bot.get_translator_runtime_profile()
            state = bot.update_translator_session_state(
                last_translated_original=original,
                last_translated_translation=translation,
                last_language_pair=profile.get("language_pair"),
                last_event="line_replayed",
                persist=True,
            )
            await message.reply(_render_translator_session_state(state))
            return
        raise UserInputError(
            user_message="❌ Используй `!translator session status|start|pause|resume|stop|mute|unmute|replay|clear`."
        )

    if sub == "reset":
        profile = bot.update_translator_runtime_profile(
            **dict(default_translator_runtime_profile),
            persist=True,
        )
        await message.reply(_render_translator_profile(profile))
        return

    raise UserInputError(
        user_message="❌ Неизвестная подкоманда translator. Используй `!translator status`."
    )


# ---------------------------------------------------------------------------
# !translate / !translate auto
# ---------------------------------------------------------------------------


async def handle_translate(bot: "KraabUserbot", message: Message) -> None:
    """
    !translate [lang] <текст> — быстрый перевод без voice.

    Формы вызова:
      !translate <текст>         — автоопределение направления (ru→en, en→ru, es→ru)
                                   или по language_pair профиля если задана
      !translate en <текст>      — перевод на указанный язык
      !translate (reply)         — перевод текста ответного сообщения
      !translate auto            — включить/выключить автоперевод входящих в чате
    """
    from ...core.translator_engine import translate_text
    from ...openclaw_client import openclaw_client as _oc

    # --- Парсинг аргументов ---
    raw_text = str(message.text or "").strip()
    # Убираем команду: !translate или Краб translate
    parts = raw_text.split(maxsplit=1)
    after_cmd = parts[1].strip() if len(parts) > 1 else ""

    # Специальный subcommand: !translate auto → toggle автоперевода
    if after_cmd.lower() in ("auto", "авто"):
        await handle_translate_auto(bot, message)
        return

    # Определяем target lang и текст для перевода
    tgt_lang_override: str | None = None
    text_to_translate: str = ""

    if after_cmd:
        # Проверяем первое слово — может быть указание языка
        first_word, _, rest = after_cmd.partition(" ")
        if first_word.lower() in _TRANSLATE_LANG_ALIASES:
            tgt_lang_override = _TRANSLATE_LANG_ALIASES[first_word.lower()]
            text_to_translate = rest.strip()
        else:
            text_to_translate = after_cmd.strip()

    # Если текст не указан — берём из reply
    if not text_to_translate:
        reply_msg = getattr(message, "reply_to_message", None)
        if reply_msg is not None:
            text_to_translate = str(getattr(reply_msg, "text", None) or "").strip()
        if not text_to_translate:
            raise UserInputError(
                user_message=(
                    "❌ Укажи текст для перевода:\n"
                    "`!translate <текст>`\n"
                    "`!translate en <текст>`\n"
                    "`!translate auto` — автоперевод входящих в чате\n"
                    "Или ответь командой на сообщение."
                )
            )

    # --- Определяем языковую пару ---
    profile = bot.get_translator_runtime_profile()
    pair = profile.get("language_pair", "")

    if tgt_lang_override:
        # Пользователь явно указал целевой язык — автоопределяем source через language_detect
        try:
            from ...core.language_detect import detect_language

            detected = detect_language(text_to_translate)
            src_lang = detected if detected and detected != tgt_lang_override else "auto"
            if src_lang == "auto" or not src_lang:
                # Берём src из профиля если авто не сработал
                src_lang = pair.split("-")[0] if "-" in pair else "auto"
        except Exception:
            src_lang = pair.split("-")[0] if "-" in pair else "auto"
        tgt_lang = tgt_lang_override
        if src_lang == tgt_lang:
            # Если совпадает — переводим на другой (из профиля tgt)
            tgt_lang = pair.split("-")[1] if len(pair.split("-")) >= 2 else "ru"
    elif pair and pair not in ("", "auto-detect"):
        # Профиль задан — используем пару из профиля; language_detect определяет src
        try:
            from ...core.language_detect import detect_language, resolve_translation_pair

            detected = detect_language(text_to_translate)
            if detected:
                src_lang, tgt_lang = resolve_translation_pair(detected, pair)
            else:
                src_lang, tgt_lang = (pair.split("-") + ["ru"])[:2]
        except Exception:
            src_lang, tgt_lang = (pair.split("-") + ["ru"])[:2]
    else:
        # Нет профиля или auto-detect — автоопределяем направление по языку текста
        # Правила: ru→en, en→ru, es→ru, остальное→ru
        try:
            from ...core.language_detect import auto_detect_direction, detect_language

            detected = detect_language(text_to_translate)
            if detected:
                src_lang, tgt_lang = auto_detect_direction(detected)
            else:
                # Детекция не удалась — fallback: переводим на русский
                src_lang, tgt_lang = "auto", "ru"
        except Exception:
            src_lang, tgt_lang = "auto", "ru"

    # Если src == tgt — принудительно меняем tgt на ru или на en
    if src_lang == tgt_lang:
        tgt_lang = "en" if src_lang == "ru" else "ru"

    # --- Перевод ---
    try:
        result = await translate_text(
            text_to_translate,
            src_lang,
            tgt_lang,
            openclaw_client=_oc,
            chat_id="translate_cmd",
        )
    except Exception as exc:
        logger.exception("handle_translate: ошибка при переводе")
        await message.reply(f"❌ Ошибка перевода: {str(exc)[:200]}")
        return

    if not result.translated:
        await message.reply("❌ Пустой ответ от модели.")
        return

    await message.reply(
        f"🔄 {result.src_lang}→{result.tgt_lang} ({result.latency_ms}ms)\n"
        f"**{result.original}**\n"
        f"_{result.translated}_"
    )


async def handle_translate_auto(bot: "KraabUserbot", message: Message) -> None:
    """
    !translate auto — toggle автоперевода входящих сообщений в текущем чате.

    Когда включён: все входящие текстовые сообщения переводятся автоматически
    (направление определяется через auto_detect_direction: ru→en, en→ru, es→ru).
    Повторный вызов выключает автоперевод.
    """
    chat_id = str(message.chat.id)
    is_enabled = bot.is_auto_translate_enabled(chat_id)

    if is_enabled:
        bot.remove_auto_translate_chat(chat_id)
        await message.reply("🔄 Автоперевод в этом чате выключен.")
        logger.info("auto_translate_disabled", chat_id=chat_id)
    else:
        bot.add_auto_translate_chat(chat_id)
        await message.reply(
            "🔄 Автоперевод входящих включён в этом чате.\n"
            "Направление: ru→en, en→ru, es→ru.\n"
            "Для выключения: `!translate auto`"
        )
        logger.info("auto_translate_enabled", chat_id=chat_id)


__all__ = [
    "_TRANSLATE_LANG_ALIASES",
    "_parse_toggle_arg",
    "_render_translator_profile",
    "_render_translator_session_state",
    "handle_translate",
    "handle_translate_auto",
    "handle_translator",
]
