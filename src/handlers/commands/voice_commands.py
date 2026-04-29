# -*- coding: utf-8 -*-
"""
voice_commands - Phase 2 Wave 4 extraction (Session 27).

Voice control commands for userbot:
  !voice (on/off/toggle/speed/voice/delivery/block/unblock/blocked/reset),
  !tts (macOS say + ffmpeg -> OGG/Opus voice message),
  audio_message handler (incoming voice -> transcription via perceptor).

Includes helper ``_render_voice_profile`` and module-level state
(``_TTS_VOICES``, ``_TTS_LANG_ALIASES``).
Re-exported from command_handlers.py for backwards compatibility.

See ``docs/CODE_SPLITS_PLAN.md`` Phase 2 - domain extractions.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from typing import TYPE_CHECKING, Any

from pyrogram.types import Message

from ...core.exceptions import UserInputError
from ...core.logger import get_logger
from ...core.subprocess_env import clean_subprocess_env

if TYPE_CHECKING:
    from ...userbot_bridge import KraabUserbot

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Helpers: voice profile rendering
# ---------------------------------------------------------------------------


def _render_voice_profile(profile: dict[str, Any]) -> str:
    """Format runtime voice-profile for Telegram-reply."""
    enabled = bool(profile.get("enabled"))
    delivery = str(profile.get("delivery") or "text+voice")
    speed = float(profile.get("speed") or 1.5)
    voice_name = str(profile.get("voice") or "ru-RU-DmitryNeural")
    input_ready = bool(profile.get("input_transcription_ready"))
    live_foundation = bool(profile.get("live_voice_foundation"))
    blocked_chats = profile.get("blocked_chats") or []
    if blocked_chats:
        blocked_preview = ", ".join(f"`{cid}`" for cid in list(blocked_chats)[:5])
        if len(blocked_chats) > 5:
            blocked_preview += f" (+{len(blocked_chats) - 5})"
    else:
        blocked_preview = "—"
    return (
        "\U0001f399️ **Voice runtime**\n"
        f"- Озвучка ответов: `{'ВКЛ' if enabled else 'ВЫКЛ'}`\n"
        f"- Режим доставки: `{delivery}`\n"
        f"- Скорость: `{speed:.2f}x`\n"
        f"- Голос: `{voice_name}`\n"
        f"- Входящие voice/STT: `{'READY' if input_ready else 'DOWN'}`\n"
        f"- Live voice foundation: `{'READY' if live_foundation else 'DEGRADED'}`\n"
        f"- Blocked chats: {blocked_preview}\n\n"
        "Команды:\n"
        "`!voice on|off|toggle`\n"
        "`!voice speed <0.75..2.5>`\n"
        "`!voice voice <edge-tts-id>`\n"
        "`!voice delivery <text+voice|voice-only>`\n"
        "`!voice block <chat_id>` / `!voice unblock <chat_id>` / `!voice blocked`\n"
        "`!voice reset`"
    )


# ---------------------------------------------------------------------------
# !voice - manage runtime voice profile
# ---------------------------------------------------------------------------


async def handle_voice(bot: "KraabUserbot", message: Message) -> None:
    """
    Управление runtime voice-профилем userbot.

    Раньше это был только toggle-флаг. Теперь команда управляет целым профилем:
    enabled/speed/voice/delivery, чтобы owner мог нормально настраивать голосовой
    контур без ручной правки `.env`.
    """
    args = str(message.text or "").split()
    if len(args) == 1 or str(args[1] or "").strip().lower() in {"status", "show"}:
        await message.reply(_render_voice_profile(bot.get_voice_runtime_profile()))
        return

    sub = str(args[1] or "").strip().lower()
    if sub in {"toggle", "on", "off"}:
        if sub == "toggle":
            profile = bot.update_voice_runtime_profile(
                enabled=not bool(bot.get_voice_runtime_profile().get("enabled")),
                persist=True,
            )
        else:
            profile = bot.update_voice_runtime_profile(enabled=(sub == "on"), persist=True)
        await message.reply(_render_voice_profile(profile))
        return

    if sub == "speed":
        if len(args) < 3:
            raise UserInputError(user_message="❌ Укажи скорость: `!voice speed 1.25`")
        try:
            profile = bot.update_voice_runtime_profile(speed=float(args[2]), persist=True)
        except ValueError as exc:
            raise UserInputError(
                user_message="❌ Скорость должна быть числом, например `1.25`."
            ) from exc
        await message.reply(_render_voice_profile(profile))
        return

    if sub == "voice":
        if len(args) < 3:
            raise UserInputError(
                user_message="❌ Укажи voice-id, например `!voice voice ru-RU-SvetlanaNeural`."
            )
        profile = bot.update_voice_runtime_profile(voice=args[2], persist=True)
        await message.reply(_render_voice_profile(profile))
        return

    if sub == "delivery":
        if len(args) < 3:
            raise UserInputError(
                user_message="❌ Укажи режим: `!voice delivery text+voice` или `!voice delivery voice-only`."
            )
        delivery = str(args[2] or "").strip().lower()
        if delivery not in {"text+voice", "voice-only"}:
            raise UserInputError(
                user_message="❌ Поддерживаются только `text+voice` и `voice-only`."
            )
        profile = bot.update_voice_runtime_profile(delivery=delivery, persist=True)
        await message.reply(_render_voice_profile(profile))
        return

    if sub == "reset":
        profile = bot.update_voice_runtime_profile(
            enabled=False,
            speed=1.5,
            voice="ru-RU-DmitryNeural",
            delivery="text+voice",
            persist=True,
        )
        await message.reply(_render_voice_profile(profile))
        return

    if sub == "blocked":
        blocked = bot.get_voice_blocked_chats()
        if not blocked:
            await message.reply(
                "\U0001f399️ **Voice blocklist** пуст.\n"
                "Добавить чат: `!voice block <chat_id>` (например, `!voice block -1001587432709`)."
            )
            return
        lines = "\n".join(f"- `{cid}`" for cid in blocked)
        await message.reply(
            f"\U0001f399️ **Voice blocklist ({len(blocked)}):**\n{lines}\n\n"
            "Убрать: `!voice unblock <chat_id>`"
        )
        return

    if sub in {"block", "unblock"}:
        # Two variants:
        #   1) `!voice block <chat_id>` - explicit id;
        #   2) `!voice block` without args -> use current chat id.
        target_chat_id: str
        if len(args) >= 3 and str(args[2] or "").strip():
            target_chat_id = str(args[2]).strip()
        else:
            chat_ref = getattr(message, "chat", None)
            inferred = getattr(chat_ref, "id", None) if chat_ref else None
            if inferred is None:
                raise UserInputError(
                    user_message=(
                        f"❌ Укажи chat_id: `!voice {sub} <chat_id>`\n"
                        "(например, `-1001587432709` для супергруппы)"
                    )
                )
            target_chat_id = str(inferred)

        try:
            if sub == "block":
                bot.add_voice_blocked_chat(target_chat_id, persist=True)
                prefix = "✅ Добавил в voice blocklist"
            else:
                bot.remove_voice_blocked_chat(target_chat_id, persist=True)
                prefix = "✅ Убрал из voice blocklist"
        except ValueError as exc:
            raise UserInputError(user_message=f"❌ {exc}") from exc

        profile = bot.get_voice_runtime_profile()
        await message.reply(f"{prefix}: `{target_chat_id}`\n\n{_render_voice_profile(profile)}")
        return

    raise UserInputError(user_message="❌ Неизвестная подкоманда voice. Используй `!voice status`.")


# ---------------------------------------------------------------------------
# audio_message - incoming voice/audio handler
# ---------------------------------------------------------------------------


async def handle_audio_message(bot: "KraabUserbot", message: Message) -> None:
    """
    Обработка входящих голосовых/аудио сообщений Telegram.

    Скачивает аудио, транскрибирует через perceptor, обрабатывает как текстовый запрос.
    """
    from ...modules.perceptor import perceptor

    try:
        audio_bytes = await message.download(in_memory=True)
        if audio_bytes is None:
            await message.reply("❌ Не удалось скачать аудио.")
            return

        transcript = await perceptor.transcribe_audio(bytes(audio_bytes))
        if not transcript:
            await message.reply("❌ Не удалось распознать речь.")
            return

        await message.reply(f"_{transcript}_")

        # Process transcript as regular text query via bot
        fake_text = transcript
        response = await bot.process_text_query(fake_text, message)
        if response:
            await message.reply(response)

    except Exception as exc:
        logger.error("handle_audio_message_error", error=str(exc))
        await message.reply(f"❌ Ошибка обработки аудио: {str(exc)[:200]}")


# ---------------------------------------------------------------------------
# !tts - text to voice message (macOS say)
# ---------------------------------------------------------------------------

# Supported languages and macOS say voices
_TTS_VOICES: dict[str, str] = {
    "ru": "Milena",
    "en": "Samantha",
    "es": "Monica",
}

# Language code aliases
_TTS_LANG_ALIASES: dict[str, str] = {
    "ru": "ru",
    "en": "en",
    "es": "es",
    "russian": "ru",
    "english": "en",
    "spanish": "es",
}


async def handle_tts(bot: "KraabUserbot", message: Message) -> None:
    """
    !tts [lang] <текст> — голосовое сообщение через macOS say + ffmpeg.

    Варианты вызова:
      !tts Привет, мир          — русский (Milena, по умолчанию)
      !tts en Hello world       — английский (Samantha)
      !tts es Hola mundo        — испанский (Monica)
      !tts (reply на сообщение) — озвучивает текст ответного сообщения

    Pipeline: say -v <Voice> -o speech.aiff <text> -> ffmpeg -> OGG/Opus -> send_voice
    """
    raw = bot._get_command_args(message).strip()

    # Detect language and text
    lang = "ru"
    text = raw

    if raw:
        parts = raw.split(None, 1)
        first = parts[0].lower()
        if first in _TTS_LANG_ALIASES:
            lang = _TTS_LANG_ALIASES[first]
            text = parts[1].strip() if len(parts) > 1 else ""

    # If no text - check reply
    if not text:
        replied = getattr(message, "reply_to_message", None)
        if replied is not None:
            replied_text = getattr(replied, "text", None) or getattr(replied, "caption", None) or ""
            text = replied_text.strip()

    if not text:
        raise UserInputError(
            user_message=(
                "\U0001f399️ **TTS — текст в голос (macOS say)**\n\n"
                "Использование:\n"
                "`!tts <текст>` — русский (Milena)\n"
                "`!tts en <текст>` — английский (Samantha)\n"
                "`!tts es <текст>` — испанский (Monica)\n"
                "`!tts` в ответ на сообщение — озвучить его\n\n"
                "_Поддерживаемые языки: ru, en, es_"
            )
        )

    voice_name = _TTS_VOICES.get(lang, _TTS_VOICES["ru"])

    # Temp files: say -> AIFF, ffmpeg -> OGG/Opus
    with tempfile.TemporaryDirectory(prefix="krab_tts_") as tmpdir:
        aiff_path = os.path.join(tmpdir, "speech.aiff")
        ogg_path = os.path.join(tmpdir, "speech.ogg")

        try:
            # Step 1: macOS say -> AIFF
            say_proc = await asyncio.create_subprocess_exec(
                "/usr/bin/say",
                "-v",
                voice_name,
                "-o",
                aiff_path,
                text,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                env=clean_subprocess_env(),
            )
            await say_proc.wait()
            if say_proc.returncode != 0:
                logger.error("tts_say_failed", voice=voice_name, returncode=say_proc.returncode)
                raise UserInputError(
                    user_message=f"❌ macOS say завершился с ошибкой (код {say_proc.returncode})."
                )

            # Step 2: AIFF -> OGG/Opus (Telegram voice message)
            ffmpeg_proc = await asyncio.create_subprocess_exec(
                "ffmpeg",
                "-y",
                "-i",
                aiff_path,
                "-c:a",
                "libopus",
                "-b:a",
                "32k",
                "-vbr",
                "on",
                "-compression_level",
                "10",
                ogg_path,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                env=clean_subprocess_env(),
            )
            await ffmpeg_proc.wait()

            if not os.path.exists(ogg_path) or os.path.getsize(ogg_path) == 0:
                logger.error("tts_ffmpeg_failed", aiff=aiff_path, ogg=ogg_path)
                raise UserInputError(user_message="❌ ffmpeg не смог конвертировать аудио.")

            # Step 3: send voice message
            logger.info(
                "tts_sending_voice",
                lang=lang,
                voice=voice_name,
                text_len=len(text),
                ogg_size=os.path.getsize(ogg_path),
            )
            await bot.client.send_voice(message.chat.id, ogg_path)

        except UserInputError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.error("tts_error", error=str(exc), error_type=type(exc).__name__)
            raise UserInputError(user_message=f"❌ TTS ошибка: {str(exc)[:200]}") from exc
