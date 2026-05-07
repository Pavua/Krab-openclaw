# -*- coding: utf-8 -*-
"""
content_commands — Phase 2 Wave 15 extraction (Session 27).

Медиа- и контент-команды:
  !yt, !img, !ocr, !media, !snippet, !template,
  !top, !fwd, !collect, !grep, !spam, !id, !backup.

Re-exported from command_handlers.py для обратной совместимости.
"""

from __future__ import annotations

import datetime
import json
import pathlib
import re
from typing import TYPE_CHECKING

from pyrogram.types import Message

from ...core.access_control import AccessLevel
from ...core.exceptions import UserInputError
from ...core.logger import get_logger
from ...openclaw_client import openclaw_client as _openclaw_client_default
from ...skills.youtube_metadata import fetch_yt_metadata, format_yt_metadata

if TYPE_CHECKING:
    pass

logger = get_logger(__name__)


def _ch_attr(name: str, default):
    """Lazy proxy к command_handlers.<name> с fallback на default."""
    try:
        from .. import command_handlers as _ch
    except Exception:  # noqa: BLE001
        return default
    return getattr(_ch, name, default)


# Алиас openclaw_client — тесты патчат command_handlers.openclaw_client
openclaw_client = _openclaw_client_default


def _get_openclaw_client():
    return _ch_attr("openclaw_client", _openclaw_client_default)


def _split_text_for_telegram(text: str, limit: int = 4000) -> list[str]:
    """Разбивает длинный текст на части не более limit символов."""
    if len(text) <= limit:
        return [text]
    parts: list[str] = []
    while text:
        parts.append(text[:limit])
        text = text[limit:]
    return parts


# ---------------------------------------------------------------------------
# !yt — информация о YouTube видео
# ---------------------------------------------------------------------------

_YT_URL_RE = re.compile(
    r"https?://(?:www\.)?(?:youtube\.com/watch\?[^\s]*v=[\w-]+|youtu\.be/[\w-]+|youtube\.com/shorts/[\w-]+)"
)

_YT_PROMPT_TEMPLATE = (
    "Найди информацию об этом YouTube видео: {url}. "
    "Покажи: название, автор, длительность, дата, описание (кратко)."
)

# Prompt когда oEmbed уже дал базовые данные — просим LLM обогатить
_YT_PROMPT_WITH_META = (
    "YouTube видео: {url}\n"
    "Базовые данные из oEmbed:\n{meta_block}\n\n"
    "Используй web_search или fetch, чтобы дополнить: длительность, дата публикации, "
    "краткое описание содержимого. Если данных нет — покажи то что уже известно."
)

# Prompt для cloud LLM без yt-dlp, акцент на web_fetch/search а не subprocess
_YT_PROMPT_CLOUD = (
    "Получи информацию о YouTube видео {url} через web_search или http_fetch. "
    "НЕ запускай yt-dlp или youtube-dl. "
    "Верни: название, автор/канал, длительность, дата публикации, краткое описание."
)


def _extract_yt_url(text: str) -> str | None:
    """Извлекает первый YouTube URL из текста. Возвращает None если не найдено."""
    m = _YT_URL_RE.search(text or "")
    return m.group(0) if m else None


async def handle_yt(bot: "object", message: Message) -> None:
    """
    !yt <URL>       — информация о YouTube видео через AI + web_search.
    !yt (в reply)   — извлекает URL из цитируемого сообщения.

    Сессия изолирована: yt_{chat_id}.

    Session 40: extended URL extraction — теперь смотрит:
    1. arg после команды
    2. text/caption inline-сообщения (для случая `!yt` в caption forwarded preview)
    3. web_page.url inline-сообщения (Pyrofork сохраняет YouTube URL в Message.web_page
       когда юзер пишет ссылку текстом → Telegram создаёт WEB_PAGE_PREVIEW)
    4. text/caption reply-сообщения
    5. web_page.url reply-сообщения (тот же кейс через reply)
    """

    def _yt_url_from_message(msg: "Message | None") -> str | None:
        """Извлечь YouTube URL из text/caption/web_page любого Message.

        Defensive: используем getattr — некоторые тестовые SimpleNamespace
        и stub-объекты могут не иметь полного Message API.
        """
        if msg is None:
            return None
        for attr in ("text", "caption"):
            source = getattr(msg, attr, None)
            if source:
                found = _extract_yt_url(source)
                if found:
                    return found
        # Pyrofork: forwarded или inline preview хранит URL в .web_page
        web_page = getattr(msg, "web_page", None)
        if web_page is not None:
            for attr in ("url", "display_url"):
                wp_url = getattr(web_page, attr, None)
                if wp_url:
                    found = _extract_yt_url(str(wp_url))
                    if found:
                        return found
        return None

    args = bot._get_command_args(message).strip()

    url: str | None = _extract_yt_url(args)
    if url is None:
        # 1) inline message itself (caption + web_page)
        url = _yt_url_from_message(message)
    if url is None:
        # 2) reply target (text/caption + web_page)
        url = _yt_url_from_message(message.reply_to_message)

    if url is None:
        raise UserInputError(
            user_message=(
                "🎬 Использование:\n"
                "`!yt <YouTube URL>` — информация о видео\n"
                "или ответь командой `!yt` на сообщение с YouTube ссылкой\n\n"
                "ℹ️ Forwarded video в Telegram **не содержит** оригинальный YouTube URL — "
                "это ограничение протокола. Скопируй ссылку и отправь как текст: `!yt <URL>`."
            )
        )

    session_id = f"yt_{message.chat.id}"
    msg = await message.reply(f"🎬 Ищу информацию о видео: `{url}`...")

    # --- Резервный путь: oEmbed (не требует subprocess/DNS sandbox) ---
    oembed_prefix = ""
    try:
        meta = await fetch_yt_metadata(url)
        if meta:
            oembed_prefix = format_yt_metadata(meta)
    except Exception as exc:  # noqa: BLE001
        logger.debug("yt_oembed_skipped", reason=str(exc))

    # Если oEmbed дал достаточно данных — отображаем сразу и просим LLM обогатить
    if oembed_prefix:
        prompt = _YT_PROMPT_WITH_META.format(url=url, meta_block=oembed_prefix)
    else:
        # Иначе — cloud LLM с явным запретом yt-dlp subprocess
        prompt = _YT_PROMPT_CLOUD.format(url=url)

    try:
        oc = _get_openclaw_client()
        chunks: list[str] = []
        async for chunk in oc.send_message_stream(
            message=prompt,
            chat_id=session_id,
            disable_tools=False,
        ):
            chunks.append(str(chunk))

        result = "".join(chunks).strip()

        if not result and oembed_prefix:
            # LLM не добавил ничего, но oEmbed сработал — покажем oEmbed
            result = oembed_prefix

        if not result:
            await msg.edit("❌ AI вернул пустой ответ.")
            return

        parts = _split_text_for_telegram(result)
        await msg.edit(parts[0])
        for part in parts[1:]:
            await message.reply(part)

    except Exception as exc:  # noqa: BLE001
        # Если LLM тоже упал, но oEmbed сработал — покажем хотя бы базовые данные
        if oembed_prefix:
            logger.warning("handle_yt_llm_failed_fallback_to_oembed", error=str(exc))
            parts = _split_text_for_telegram(
                oembed_prefix + f"\n\n_(AI обогащение недоступно: {exc})_"
            )
            await msg.edit(parts[0])
            return
        logger.error("handle_yt_error", error=str(exc), error_type=type(exc).__name__)
        await msg.edit(f"❌ Ошибка: {exc}")


# ---------------------------------------------------------------------------
# !img — анализ фото через AI vision
# ---------------------------------------------------------------------------


async def handle_img(bot: "object", message: Message) -> None:
    """
    !img                — reply на фото → краткое описание
    !img <вопрос>       — reply на фото → ответ на вопрос о фото

    Сессия изолирована: img_{chat_id}. force_cloud=True.
    """
    import base64
    import io

    question = bot._get_command_args(message).strip()

    replied = message.reply_to_message
    if replied is None:
        raise UserInputError(
            user_message=(
                "🖼 **!img** — описание фото через AI vision\n\n"
                "Ответь на сообщение с фото:\n"
                "`!img` — описание\n"
                "`!img <вопрос>` — ответ на вопрос о фото"
            )
        )

    has_photo = bool(replied.photo)
    has_doc_image = bool(
        replied.document
        and replied.document.mime_type
        and replied.document.mime_type.startswith("image/")
    )

    if not has_photo and not has_doc_image:
        raise UserInputError(
            user_message=(
                "🖼 Это сообщение не содержит фото. Ответь командой на сообщение с фотографией."
            )
        )

    status_msg = await message.reply("🔍 Анализирую фото...")

    try:
        img_bytes_io = io.BytesIO()
        await replied.download(in_memory=img_bytes_io)
        img_bytes_io.seek(0)
        img_bytes = img_bytes_io.read()

        if not img_bytes:
            await status_msg.edit("❌ Не удалось скачать фото.")
            return

        img_b64 = base64.b64encode(img_bytes).decode("ascii")

        if question:
            prompt = question
        else:
            prompt = (
                "Опиши это фото подробно. "
                "Что на нём изображено? Текст, объекты, люди, место — всё что видишь."
            )

        session_id = f"img_{message.chat.id}"
        oc = _get_openclaw_client()

        chunks: list[str] = []
        async for chunk in oc.send_message_stream(
            message=prompt,
            chat_id=session_id,
            images=[img_b64],
            force_cloud=True,
            disable_tools=True,
        ):
            chunks.append(str(chunk))

        result = "".join(chunks).strip()

        if not result:
            await status_msg.edit("❌ AI не смог проанализировать фото.")
            return

        parts = _split_text_for_telegram(result)
        await status_msg.edit(parts[0])
        for part in parts[1:]:
            await message.reply(part)

    except UserInputError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error("handle_img_error", error=str(exc), error_type=type(exc).__name__)
        await status_msg.edit(f"❌ Ошибка анализа фото: {exc}")


# ---------------------------------------------------------------------------
# !ocr — извлечение текста из изображения через AI vision
# ---------------------------------------------------------------------------


async def handle_ocr(bot: "object", message: Message) -> None:
    """
    !ocr                — reply на фото → дословный текст с изображения
    !ocr <подсказка>    — reply на фото → OCR с доп. контекстом

    Сессия изолирована: ocr_{chat_id}. force_cloud=True.
    """
    import base64
    import io

    hint = bot._get_command_args(message).strip()

    replied = message.reply_to_message
    if replied is None:
        raise UserInputError(
            user_message=(
                "📄 **!ocr** — извлечение текста из изображения\n\n"
                "Ответь командой на сообщение с фото:\n"
                "`!ocr` — извлечь весь текст\n"
                "`!ocr <подсказка>` — OCR с дополнительным контекстом"
            )
        )

    has_photo = bool(replied.photo)
    has_doc_image = bool(
        replied.document
        and replied.document.mime_type
        and replied.document.mime_type.startswith("image/")
    )

    if not has_photo and not has_doc_image:
        raise UserInputError(
            user_message=(
                "📄 Это сообщение не содержит фото. Ответь командой на сообщение с изображением."
            )
        )

    status_msg = await message.reply("🔍 Извлекаю текст...")

    try:
        img_bytes_io = io.BytesIO()
        await replied.download(in_memory=img_bytes_io)
        img_bytes_io.seek(0)
        img_bytes = img_bytes_io.read()

        if not img_bytes:
            await status_msg.edit("❌ Не удалось скачать изображение.")
            return

        img_b64 = base64.b64encode(img_bytes).decode("ascii")

        if hint:
            prompt = (
                f"Извлеки весь текст с этого изображения дословно. "
                f"Дополнительный контекст: {hint}. "
                f"Верни только сам текст без пояснений."
            )
        else:
            prompt = (
                "Извлеки весь текст с этого изображения дословно. "
                "Сохрани оригинальное форматирование (абзацы, списки, таблицы). "
                "Верни только текст без пояснений и комментариев."
            )

        session_id = f"ocr_{message.chat.id}"
        oc = _get_openclaw_client()

        chunks: list[str] = []
        async for chunk in oc.send_message_stream(
            message=prompt,
            chat_id=session_id,
            images=[img_b64],
            force_cloud=True,
            disable_tools=True,
        ):
            chunks.append(str(chunk))

        result = "".join(chunks).strip()

        if not result:
            await status_msg.edit("❌ Текст на изображении не найден.")
            return

        parts = _split_text_for_telegram(result)
        await status_msg.edit(f"📄 **OCR:**\n{parts[0]}")
        for part in parts[1:]:
            await message.reply(part)

    except UserInputError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error("handle_ocr_error", error=str(exc), error_type=type(exc).__name__)
        await status_msg.edit(f"❌ Ошибка OCR: {exc}")


# ---------------------------------------------------------------------------
# !media — скачивание медиафайлов
# ---------------------------------------------------------------------------


async def handle_media(bot: "object", message: Message) -> None:
    """
    Скачивает медиафайлы из Telegram.

    Использование (в reply на фото/видео/документ):
      !media           — скачать и переслать как файл (документ)
      !media save      — скачать в ~/Downloads/krab_media/
      !media info      — показать метаданные (размер, тип, разрешение)
    """
    import mimetypes
    import tempfile

    args = bot._get_command_args(message).strip().lower()
    subcommand = args.split()[0] if args else ""

    replied = message.reply_to_message
    if replied is None:
        raise UserInputError(
            user_message=(
                "📥 **!media** — скачивание медиафайлов\n\n"
                "Ответь на сообщение с медиа:\n"
                "`!media` — скачать и переслать как файл\n"
                "`!media save` — сохранить в ~/Downloads/krab_media/\n"
                "`!media info` — метаданные файла"
            )
        )

    media_type = None
    file_name = None
    file_size = None
    mime_type = None
    width = height = duration = None

    if replied.photo:
        media_type = "photo"
        mime_type = "image/jpeg"
        width = replied.photo.width
        height = replied.photo.height
        file_size = replied.photo.file_size
        file_name = f"photo_{replied.photo.file_unique_id}.jpg"

    elif replied.video:
        media_type = "video"
        mime_type = replied.video.mime_type or "video/mp4"
        width = replied.video.width
        height = replied.video.height
        duration = replied.video.duration
        file_size = replied.video.file_size
        ext = mimetypes.guess_extension(mime_type) or ".mp4"
        file_name = replied.video.file_name or f"video_{replied.video.file_unique_id}{ext}"

    elif replied.document:
        media_type = "document"
        mime_type = replied.document.mime_type or "application/octet-stream"
        file_size = replied.document.file_size
        file_name = replied.document.file_name or f"doc_{replied.document.file_unique_id}"

    elif replied.audio:
        media_type = "audio"
        mime_type = replied.audio.mime_type or "audio/mpeg"
        duration = replied.audio.duration
        file_size = replied.audio.file_size
        ext = mimetypes.guess_extension(mime_type) or ".mp3"
        file_name = replied.audio.file_name or f"audio_{replied.audio.file_unique_id}{ext}"

    elif replied.voice:
        media_type = "voice"
        mime_type = replied.voice.mime_type or "audio/ogg"
        duration = replied.voice.duration
        file_size = replied.voice.file_size
        ext = mimetypes.guess_extension(mime_type) or ".ogg"
        file_name = f"voice_{replied.voice.file_unique_id}{ext}"

    elif replied.sticker:
        media_type = "sticker"
        mime_type = replied.sticker.mime_type or "image/webp"
        width = replied.sticker.width
        height = replied.sticker.height
        file_size = replied.sticker.file_size
        ext = ".tgs" if getattr(replied.sticker, "is_animated", False) else ".webp"
        file_name = f"sticker_{replied.sticker.file_unique_id}{ext}"

    else:
        raise UserInputError(
            user_message=(
                "📥 Это сообщение не содержит медиафайл.\n"
                "Ответь командой на фото, видео, документ, аудио, голосовое или стикер."
            )
        )

    if subcommand == "info":
        lines = [f"📋 **Метаданные медиафайла** (`{media_type}`)"]
        lines.append(f"• Имя: `{file_name}`")
        if mime_type:
            lines.append(f"• MIME: `{mime_type}`")
        if file_size:
            size_kb = file_size / 1024
            if size_kb >= 1024:
                lines.append(f"• Размер: `{size_kb / 1024:.1f} МБ`")
            else:
                lines.append(f"• Размер: `{size_kb:.1f} КБ`")
        if width and height:
            lines.append(f"• Разрешение: `{width}×{height}`")
        if duration is not None:
            lines.append(f"• Длительность: `{duration} сек`")
        await message.reply("\n".join(lines))
        return

    if subcommand == "save":
        save_dir = pathlib.Path.home() / "Downloads" / "krab_media"
        save_dir.mkdir(parents=True, exist_ok=True)
        save_path = save_dir / (file_name or "media_file")

        status_msg = await message.reply(f"⬇️ Сохраняю `{file_name}`...")
        try:
            await replied.download(file_name=str(save_path))
            size_str = ""
            if save_path.exists():
                sz = save_path.stat().st_size / 1024
                size_str = f" ({sz / 1024:.1f} МБ)" if sz >= 1024 else f" ({sz:.1f} КБ)"
            await status_msg.edit(f"✅ Сохранено: `{save_path}`{size_str}")
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "handle_media_save_error",
                file_name=file_name,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            await status_msg.edit(f"❌ Ошибка сохранения: {exc}")
        return

    # По умолчанию: скачать и переслать как документ
    status_msg = await message.reply(f"⬇️ Скачиваю `{file_name}`...")
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = pathlib.Path(tmpdir) / (file_name or "media_file")
            await replied.download(file_name=str(tmp_path))

            if not tmp_path.exists() or tmp_path.stat().st_size == 0:
                await status_msg.edit("❌ Не удалось скачать файл (пустой или недоступен).")
                return

            sz = tmp_path.stat().st_size / 1024
            size_str = f"{sz / 1024:.1f} МБ" if sz >= 1024 else f"{sz:.1f} КБ"
            caption = f"📥 `{file_name}` · {size_str}"

            await bot.client.send_document(
                message.chat.id,
                str(tmp_path),
                caption=caption,
                reply_to_message_id=message.id,
            )
            try:
                await status_msg.delete()
            except Exception:  # noqa: BLE001
                pass

    except Exception as exc:  # noqa: BLE001
        logger.error(
            "handle_media_error",
            file_name=file_name,
            error=str(exc),
            error_type=type(exc).__name__,
        )
        try:
            await status_msg.edit(f"❌ Ошибка скачивания: {exc}")
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# !snippet — хранилище кодовых сниппетов
# ---------------------------------------------------------------------------

_SNIPPETS_FILE = pathlib.Path.home() / ".openclaw" / "krab_runtime_state" / "code_snippets.json"


def _load_snippets() -> dict[str, dict]:
    """Загружает словарь {name: {code, created_at}} из JSON-файла."""
    try:
        if _SNIPPETS_FILE.exists():
            return json.loads(_SNIPPETS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        logger.warning("snippet_load_failed")
    return {}


def _save_snippets(data: dict[str, dict]) -> None:
    """Сохраняет сниппеты в JSON-файл."""
    _SNIPPETS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _SNIPPETS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


async def handle_snippet(bot: "object", message: Message) -> None:
    """
    !snippet save <name> <code>  — сохранить сниппет (код после имени)
    !snippet save <name>          — в reply на сообщение → сохраняет текст reply
    !snippet <name>               — показать сниппет в code block
    !snippet list                 — список всех сниппетов
    !snippet del <name>           — удалить сниппет
    !snippet search <query>       — поиск по содержимому
    """
    import datetime as _dt  # noqa: PLC0415

    raw_args = bot._get_command_args(message).strip()
    parts = raw_args.split(None, 1)

    if not parts or parts[0].lower() == "list":
        snippets = _load_snippets()
        if not snippets:
            await message.reply(
                "📭 Нет сохранённых сниппетов.\n"
                "Используй `!snippet save <name> <code>` или ответь на сообщение с `!snippet save <name>`"
            )
            return
        lines = [f"• `{name}`" for name in sorted(snippets)]
        await message.reply("📋 **Сниппеты:**\n" + "\n".join(lines))
        return

    subcommand = parts[0].lower()

    if subcommand == "save":
        rest = parts[1].strip() if len(parts) > 1 else ""
        name_and_code = rest.split(None, 1)
        if not name_and_code:
            raise UserInputError(
                user_message="❌ Укажи имя: `!snippet save <name> <code>` или ответь на сообщение"
            )
        name = name_and_code[0].strip().lower()
        if not name:
            raise UserInputError(user_message="❌ Имя сниппета не может быть пустым.")

        if len(name_and_code) > 1 and name_and_code[1].strip():
            code = name_and_code[1].strip()
        else:
            replied = message.reply_to_message
            if replied is None or not (replied.text or replied.caption):
                raise UserInputError(
                    user_message=(
                        "❌ Укажи код после имени: `!snippet save <name> <code>`\n"
                        "Или ответь на сообщение с кодом командой `!snippet save <name>`"
                    )
                )
            code = (replied.text or replied.caption or "").strip()

        snippets = _load_snippets()
        snippets[name] = {
            "code": code,
            "created_at": _dt.datetime.now(tz=_dt.timezone.utc).isoformat(),
        }
        _save_snippets(snippets)
        await message.reply(f"✅ Сниппет `{name}` сохранён ({len(code)} символов).")
        return

    if subcommand == "del":
        if len(parts) < 2 or not parts[1].strip():
            raise UserInputError(user_message="❌ Укажи имя: `!snippet del <name>`")
        name = parts[1].strip().lower()
        snippets = _load_snippets()
        if name not in snippets:
            raise UserInputError(user_message=f"❌ Сниппет `{name}` не найден.")
        del snippets[name]
        _save_snippets(snippets)
        await message.reply(f"🗑 Сниппет `{name}` удалён.")
        return

    if subcommand == "search":
        if len(parts) < 2 or not parts[1].strip():
            raise UserInputError(user_message="❌ Укажи запрос: `!snippet search <query>`")
        query = parts[1].strip().lower()
        snippets = _load_snippets()
        matches = [
            name
            for name, data in snippets.items()
            if query in name or query in data.get("code", "").lower()
        ]
        if not matches:
            await message.reply(f"🔍 Ничего не найдено по запросу `{query}`.")
            return
        lines = [f"• `{name}`" for name in sorted(matches)]
        await message.reply(f"🔍 Найдено ({len(matches)}):\n" + "\n".join(lines))
        return

    name = parts[0].lower()
    snippets = _load_snippets()
    if name not in snippets:
        raise UserInputError(user_message=f"❌ Сниппет `{name}` не найден. Список: `!snippet list`")
    code = snippets[name].get("code", "")
    created = snippets[name].get("created_at", "")
    header = f"📄 **{name}**" + (f" _(сохранён {created[:10]})_" if created else "")
    await message.reply(f"{header}\n```\n{code}\n```")


# ---------------------------------------------------------------------------
# !template — шаблоны сообщений с подстановкой переменных
# ---------------------------------------------------------------------------

_TEMPLATES_FILE = (
    pathlib.Path.home() / ".openclaw" / "krab_runtime_state" / "message_templates.json"
)


def _load_templates() -> dict[str, str]:
    """Загружает шаблоны из JSON. Формат: {name: text}."""
    try:
        if _TEMPLATES_FILE.exists():
            return json.loads(_TEMPLATES_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        logger.warning("template_load_failed")
    return {}


def _save_templates(data: dict[str, str]) -> None:
    """Сохраняет шаблоны в JSON-файл."""
    _TEMPLATES_FILE.parent.mkdir(parents=True, exist_ok=True)
    _TEMPLATES_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _apply_template_vars(text: str, positional_args: list[str]) -> str:
    """Подставляет позиционные переменные {var1}, {var2}, ... в порядке появления."""
    placeholders = list(dict.fromkeys(re.findall(r"\{(\w+)\}", text)))
    if not placeholders:
        return text
    result = text
    for idx, ph in enumerate(placeholders):
        if idx < len(positional_args):
            result = result.replace(f"{{{ph}}}", positional_args[idx])
    return result


async def handle_template(bot: "object", message: Message) -> None:
    """
    !template save <name> <text>  — сохранить шаблон
    !template list                — список всех шаблонов
    !template del <name>          — удалить шаблон
    !template <name>              — отправить шаблон (без переменных)
    !template <name> val1 val2 …  — отправить с подстановкой переменных
    """
    raw_args = bot._get_command_args(message).strip()
    parts = raw_args.split(None, 1)

    if not parts or parts[0].lower() == "list":
        templates = _load_templates()
        if not templates:
            await message.reply(
                "📭 Нет сохранённых шаблонов.\n"
                "Используй `!template save <name> <text>` чтобы создать шаблон."
            )
            return
        lines = []
        for name, text in sorted(templates.items()):
            preview = text[:60].replace("\n", " ")
            if len(text) > 60:
                preview += "…"
            lines.append(f"• `{name}` — {preview}")
        await message.reply("📋 **Шаблоны:**\n" + "\n".join(lines))
        return

    subcommand = parts[0].lower()

    if subcommand == "save":
        rest = parts[1].strip() if len(parts) > 1 else ""
        name_and_text = rest.split(None, 1)
        if not name_and_text:
            raise UserInputError(
                user_message="❌ Укажи имя и текст: `!template save <name> <text>`"
            )
        name = name_and_text[0].strip().lower()
        if not name:
            raise UserInputError(user_message="❌ Имя шаблона не может быть пустым.")
        if len(name_and_text) < 2 or not name_and_text[1].strip():
            raise UserInputError(
                user_message=(
                    "❌ Укажи текст шаблона: `!template save <name> <text>`\n"
                    "Переменные задаются как `{var1}`, `{var2}` и т.д."
                )
            )
        text = name_and_text[1].strip()
        templates = _load_templates()
        templates[name] = text
        _save_templates(templates)
        vars_found = list(dict.fromkeys(re.findall(r"\{(\w+)\}", text)))
        var_hint = (
            f" Переменные: {', '.join(f'`{{{v}}}`' for v in vars_found)}" if vars_found else ""
        )
        await message.reply(f"✅ Шаблон `{name}` сохранён.{var_hint}")
        return

    if subcommand == "del":
        if len(parts) < 2 or not parts[1].strip():
            raise UserInputError(user_message="❌ Укажи имя: `!template del <name>`")
        name = parts[1].strip().lower()
        templates = _load_templates()
        if name not in templates:
            raise UserInputError(user_message=f"❌ Шаблон `{name}` не найден.")
        del templates[name]
        _save_templates(templates)
        await message.reply(f"🗑 Шаблон `{name}` удалён.")
        return

    name = subcommand
    templates = _load_templates()
    if name not in templates:
        raise UserInputError(user_message=f"❌ Шаблон `{name}` не найден. Список: `!template list`")
    template_text = templates[name]
    positional_args: list[str] = parts[1].split() if len(parts) > 1 else []
    result_text = _apply_template_vars(template_text, positional_args)
    await message.reply(result_text)


# ---------------------------------------------------------------------------
# !top — лидерборд активности чата
# ---------------------------------------------------------------------------


def _plural_messages(n: int) -> str:
    """Возвращает правильную форму слова 'сообщение' для числа n."""
    if 11 <= n % 100 <= 19:
        return "сообщений"
    rem = n % 10
    if rem == 1:
        return "сообщение"
    if 2 <= rem <= 4:
        return "сообщения"
    return "сообщений"


async def handle_top(bot: "object", message: Message) -> None:
    """
    Лидерборд активности чата на основе истории сообщений.

    Варианты:
      !top [N]     — топ N самых активных за последние 24 часа (default N=10)
      !top week    — за последние 7 дней
      !top all     — за всё время (последние 1000 сообщений)
    """
    args = bot._get_command_args(message).strip().lower()

    limit = 1000
    top_n = 10
    period_label = "24ч"

    now = datetime.datetime.now(datetime.timezone.utc)
    cutoff: datetime.datetime | None = now - datetime.timedelta(hours=24)

    if args == "week":
        cutoff = now - datetime.timedelta(days=7)
        period_label = "неделя"
    elif args == "all":
        cutoff = None
        period_label = "всё время"
    elif args:
        try:
            top_n = max(1, min(int(args), 50))
        except ValueError:
            raise UserInputError(
                user_message=(
                    "❌ Неверный аргумент.\n"
                    "Использование:\n"
                    "`!top [N]` — топ за 24ч (N до 50)\n"
                    "`!top week` — за неделю\n"
                    "`!top all` — за всё время"
                )
            )

    status_msg = await message.reply(f"⏳ Считаю активность за {period_label}...")

    chat_id = message.chat.id
    counts: dict[int, tuple[str, int]] = {}

    try:
        async for msg in bot.client.get_chat_history(chat_id, limit=limit):
            if cutoff is not None:
                msg_date = msg.date
                if msg_date is not None:
                    if msg_date.tzinfo is None:
                        msg_date = msg_date.replace(tzinfo=datetime.timezone.utc)
                    if msg_date < cutoff:
                        break

            user = msg.from_user
            if user is None:
                continue

            uid = user.id
            if uid not in counts:
                if user.username:
                    display = f"@{user.username}"
                elif user.first_name or user.last_name:
                    parts_name = filter(None, [user.first_name, user.last_name])
                    display = " ".join(parts_name)
                else:
                    display = f"user_{uid}"
                counts[uid] = (display, 0)

            display_name, cnt = counts[uid]
            counts[uid] = (display_name, cnt + 1)

    except Exception as exc:  # noqa: BLE001
        logger.warning("handle_top_error", error=str(exc), error_type=type(exc).__name__)
        await status_msg.edit(f"❌ Не удалось получить историю чата: {exc}")
        return

    if not counts:
        await status_msg.edit(f"📭 Нет сообщений за {period_label}.")
        return

    ranking = sorted(counts.values(), key=lambda x: x[1], reverse=True)[:top_n]

    medals = ["🥇", "🥈", "🥉"]
    lines = [f"🏆 **Топ чата ({period_label})**", "─────────────"]
    for i, (name, cnt) in enumerate(ranking, start=1):
        prefix = medals[i - 1] if i <= 3 else f"{i}."
        word = _plural_messages(cnt)
        lines.append(f"{prefix} {name} — {cnt} {word}")

    text = "\n".join(lines)
    await status_msg.edit(text)


# ---------------------------------------------------------------------------
# !fwd — пересылка сообщений без метки «Forwarded»
# ---------------------------------------------------------------------------


async def handle_fwd(bot: "object", message: Message) -> None:
    """
    Пересылка сообщений без метки «Forwarded» (copy_message).

    Синтаксис:
      !fwd <chat_id>          — в ответ на сообщение: скопировать его в chat_id
      !fwd <chat_id> last N   — скопировать последние N сообщений из текущего чата

    Owner-only.
    """
    access_profile = bot._get_access_profile(message.from_user)
    if access_profile.level != AccessLevel.OWNER:
        raise UserInputError(user_message="🔒 `!fwd` доступен только владельцу.")

    args = bot._get_command_args(message).strip()
    if not args:
        raise UserInputError(
            user_message=(
                "📤 **Форвард без метки**\n\n"
                "`!fwd <chat_id>` — скопировать сообщение (в ответ)\n"
                "`!fwd <chat_id> last N` — скопировать последние N сообщений"
            )
        )

    parts = args.split()
    try:
        to_chat_id = int(parts[0])
    except ValueError:
        raise UserInputError(user_message=f"❌ Неверный chat_id: `{parts[0]}`")

    from_chat_id = message.chat.id

    if len(parts) >= 3 and parts[1].lower() == "last":
        try:
            n = int(parts[2])
        except ValueError:
            raise UserInputError(user_message=f"❌ N должно быть числом, получено: `{parts[2]}`")
        if n < 1 or n > 200:
            raise UserInputError(user_message="❌ N должно быть от 1 до 200.")

        try:
            msgs = []
            async for msg in bot.client.get_chat_history(from_chat_id, limit=n):
                msgs.append(msg)
            msgs.reverse()
            copied = 0
            for msg in msgs:
                try:
                    await bot.client.copy_message(to_chat_id, from_chat_id, msg.id)
                    copied += 1
                except Exception:  # noqa: BLE001
                    pass
            reply = f"📤 Скопировано {copied}/{len(msgs)} сообщений → `{to_chat_id}`"
        except Exception as exc:  # noqa: BLE001
            reply = f"❌ Ошибка при копировании: `{exc}`"

    else:
        target = message.reply_to_message
        if target is None:
            raise UserInputError(
                user_message=(
                    "📤 Ответь на сообщение, которое хочешь переслать, "
                    "или используй `!fwd <chat_id> last N`."
                )
            )
        try:
            await bot.client.copy_message(to_chat_id, from_chat_id, target.id)
            reply = f"📤 Сообщение скопировано → `{to_chat_id}`"
        except Exception as exc:  # noqa: BLE001
            reply = f"❌ Не удалось скопировать: `{exc}`"

    if message.from_user and message.from_user.id == bot.me.id:
        await message.edit(reply)
    else:
        await message.reply(reply)


# ---------------------------------------------------------------------------
# !collect — сбор сообщений из чата
# ---------------------------------------------------------------------------


async def handle_collect(bot: "object", message: Message) -> None:
    """
    Собирает последние N сообщений из указанного чата.

    Синтаксис:
      !collect <chat_id> <N>

    Owner-only.
    """
    access_profile = bot._get_access_profile(message.from_user)
    if access_profile.level != AccessLevel.OWNER:
        raise UserInputError(user_message="🔒 `!collect` доступен только владельцу.")

    args = bot._get_command_args(message).strip()
    parts = args.split()
    if len(parts) < 2:
        raise UserInputError(
            user_message=(
                "📥 **Collect — просмотр истории чата**\n\n"
                "`!collect <chat_id> <N>` — вывести последние N сообщений из чата"
            )
        )

    try:
        src_chat_id = int(parts[0])
    except ValueError:
        raise UserInputError(user_message=f"❌ Неверный chat_id: `{parts[0]}`")

    try:
        n = int(parts[1])
    except ValueError:
        raise UserInputError(user_message=f"❌ N должно быть числом, получено: `{parts[1]}`")

    if n < 1 or n > 100:
        raise UserInputError(user_message="❌ N должно быть от 1 до 100.")

    to_chat_id = message.chat.id

    try:
        msgs = []
        async for msg in bot.client.get_chat_history(src_chat_id, limit=n):
            msgs.append(msg)
        msgs.reverse()

        if not msgs:
            reply = f"📭 Чат `{src_chat_id}` пуст или недоступен."
            if message.from_user and message.from_user.id == bot.me.id:
                await message.edit(reply)
            else:
                await message.reply(reply)
            return

        header = f"📥 **Collect** из `{src_chat_id}` — последние {len(msgs)} сообщений:"
        if message.from_user and message.from_user.id == bot.me.id:
            await message.edit(header)
        else:
            await message.reply(header)

        copied = 0
        for msg in msgs:
            try:
                await bot.client.copy_message(to_chat_id, src_chat_id, msg.id)
                copied += 1
            except Exception:  # noqa: BLE001
                pass

        if copied < len(msgs):
            await message.reply(
                f"⚠️ Скопировано {copied}/{len(msgs)} (часть сообщений недоступна для копирования)"
            )

    except Exception as exc:  # noqa: BLE001
        reply = f"❌ Ошибка при сборе: `{exc}`"
        if message.from_user and message.from_user.id == bot.me.id:
            await message.edit(reply)
        else:
            await message.reply(reply)


# ---------------------------------------------------------------------------
# !grep — поиск по истории чата
# ---------------------------------------------------------------------------


async def handle_grep(bot: "object", message: Message) -> None:
    """
    !grep <query> [@chat] [N] — поиск по истории чата.

    Форматы:
      !grep биткоин              — ищет в текущем чате (последние 200 сообщений)
      !grep биткоин 500          — ищет в последних 500 сообщениях
      !grep биткоин @durov 100   — ищет в чате @durov (последние 100 сообщений)
      !grep /pattern/            — regex-поиск (case-insensitive)
    """
    import re as _re  # noqa: PLC0415

    raw = bot._get_command_args(message)
    if not raw:
        raise UserInputError(
            user_message=(
                "🔍 Использование:\n"
                "`!grep <запрос> [@чат] [N]`\n\n"
                "Примеры:\n"
                "`!grep биткоин` — ищет в этом чате (200 последних сообщений)\n"
                "`!grep биткоин 500` — ищет в 500 последних сообщениях\n"
                "`!grep биткоин @durov 100` — ищет в другом чате\n"
                "`!grep /паттерн/` — regex-поиск"
            )
        )

    parts = raw.split()
    query_parts: list[str] = []
    target_chat: int | str = message.chat.id
    limit: int = 200

    i = 0
    while i < len(parts):
        part = parts[i]
        if part.startswith("@") and len(part) > 1:
            target_chat = part
        elif part.isdigit():
            limit = min(int(part), 2000)
        else:
            query_parts.append(part)
        i += 1

    query_str = " ".join(query_parts).strip()
    if not query_str:
        raise UserInputError(user_message="🔍 Укажи поисковый запрос после `!grep`")

    use_regex = False
    pattern: _re.Pattern | None = None

    if query_str.startswith("/") and query_str.endswith("/") and len(query_str) > 2:
        regex_src = query_str[1:-1]
        try:
            pattern = _re.compile(regex_src, _re.IGNORECASE)
            use_regex = True
            display_query = f"/{regex_src}/"
        except _re.error as exc:
            raise UserInputError(user_message=f"❌ Невалидный regex: `{exc}`") from exc
    else:
        display_query = query_str

    status_msg = await message.reply(
        f"🔍 Ищу `{display_query}` в последних **{limit}** сообщениях..."
    )

    matches: list[str] = []
    scanned = 0

    try:
        async for msg in bot.client.get_chat_history(target_chat, limit=limit):
            scanned += 1
            text = msg.text or msg.caption or ""
            if not text:
                continue

            if use_regex and pattern is not None:
                found = bool(pattern.search(text))
            else:
                found = query_str.lower() in text.lower()

            if not found:
                continue

            dt = msg.date
            time_str = dt.strftime("%d.%m %H:%M") if dt else "??:??"

            sender = ""
            if msg.from_user:
                sender = (
                    f"@{msg.from_user.username}"
                    if msg.from_user.username
                    else msg.from_user.first_name or "Unknown"
                )
            elif msg.sender_chat:
                sender = msg.sender_chat.title or "Channel"

            preview = text.replace("\n", " ")
            if len(preview) > 200:
                if use_regex and pattern is not None:
                    m = pattern.search(preview)
                    if m:
                        start = max(0, m.start() - 60)
                        end = min(len(preview), m.end() + 60)
                        prefix = "..." if start > 0 else ""
                        suffix = "..." if end < len(preview) else ""
                        preview = prefix + preview[start:end] + suffix
                    else:
                        preview = preview[:200] + "..."
                else:
                    idx = preview.lower().find(query_str.lower())
                    if idx >= 0:
                        start = max(0, idx - 60)
                        end = min(len(preview), idx + len(query_str) + 60)
                        prefix = "..." if start > 0 else ""
                        suffix = "..." if end < len(preview) else ""
                        preview = prefix + preview[start:end] + suffix
                    else:
                        preview = preview[:200] + "..."

            matches.append(f"[{time_str}] {sender}: {preview}")

            if len(matches) >= 20:
                break

    except Exception as exc:  # noqa: BLE001
        logger.warning("handle_grep_error", error=str(exc), error_type=type(exc).__name__)
        await status_msg.edit(f"❌ Ошибка при поиске: {exc}")
        return

    if not matches:
        await status_msg.edit(
            f"🔍 Ничего не найдено для `{display_query}` в последних {scanned} сообщениях."
        )
        return

    header = f"🔍 Найдено **{len(matches)}** совпадений для `{display_query}`"
    if len(matches) >= 20:
        header += " (показаны первые 20)"
    header += ":\n\n"

    lines = [f"{i + 1}. {m}" for i, m in enumerate(matches)]
    body = "\n".join(lines)

    full = header + body
    if len(full) > 4000:
        full = full[:3950] + "\n...(обрезано)"

    await status_msg.edit(full)


# ---------------------------------------------------------------------------
# !spam — управление антиспам фильтром
# ---------------------------------------------------------------------------


async def handle_spam(bot: "object", message: Message) -> None:
    """
    Управление антиспам фильтром в группе.

    Subcommands:
      !spam on            — включить в текущем чате
      !spam off           — выключить в текущем чате
      !spam status        — показать настройки
      !spam action ban    — банить нарушителей
      !spam action mute   — ограничивать (restrict) нарушителей
      !spam action delete — только удалять сообщения (default)

    Owner-only.
    """
    from ...core.spam_guard import (  # noqa: PLC0415
        VALID_ACTIONS,
        get_status,
        set_action,
        set_enabled,
    )

    access_profile = bot._get_access_profile(message.from_user)
    if access_profile.level != AccessLevel.OWNER:
        raise UserInputError(user_message="🔒 `!spam` доступен только владельцу.")

    chat_id = message.chat.id
    args = (message.text or "").split()
    sub = args[1].strip().lower() if len(args) >= 2 else "status"

    if sub == "on":
        set_enabled(chat_id, True)
        status = get_status(chat_id)
        await message.reply(
            f"✅ Антиспам **включён** в чате `{chat_id}`.\n"
            f"Действие при детекте: `{status['action']}`"
        )
        return

    if sub == "off":
        set_enabled(chat_id, False)
        await message.reply(f"🔕 Антиспам **выключен** в чате `{chat_id}`.")
        return

    if sub in {"status", "show", ""}:
        status = get_status(chat_id)
        state_icon = "✅" if status["enabled"] else "❌"
        await message.reply(
            f"🛡 **Антиспам** — `{chat_id}`\n\n"
            f"Статус: {state_icon} {'включён' if status['enabled'] else 'выключен'}\n"
            f"Действие: `{status['action']}`\n\n"
            f"Детект срабатывает при:\n"
            f"• flood: >5 сообщений за 10 сек\n"
            f"• >3 ссылок в одном сообщении\n"
            f"• пересланное сообщение со ссылками"
        )
        return

    if sub == "action":
        action = args[2].strip().lower() if len(args) >= 3 else ""
        if action not in VALID_ACTIONS:
            raise UserInputError(
                user_message=(
                    f"❌ Неизвестное действие: `{action}`.\nДоступны: `ban`, `mute`, `delete`"
                )
            )
        set_action(chat_id, action)
        await message.reply(f"⚙️ Действие при спаме установлено: `{action}`")
        return

    raise UserInputError(
        user_message=(
            "🛡 **!spam — антиспам фильтр**\n\n"
            "`!spam on` — включить\n"
            "`!spam off` — выключить\n"
            "`!spam status` — текущие настройки\n"
            "`!spam action ban|mute|delete` — действие при детекте"
        )
    )


# ---------------------------------------------------------------------------
# !id — показать ID текущего чата, себя, сообщения
# ---------------------------------------------------------------------------


async def handle_id(bot: "object", message: Message) -> None:
    """Показать ID текущего чата, своего аккаунта и (если reply) сообщения и автора.

    Синтаксис:
      !id         — chat_id + свой user_id
      !id в reply — chat_id + свой user_id + message_id + user_id автора
    """
    chat_id = message.chat.id
    me = await bot.client.get_me()
    my_user_id = me.id

    lines: list[str] = [
        "🆔 IDs",
        f"Chat: `{chat_id}`",
        f"User: `{my_user_id}`",
    ]

    reply = message.reply_to_message
    if reply is not None:
        lines.append(f"Message: `{reply.id}`")
        reply_from = reply.from_user
        if reply_from is not None:
            lines.append(f"Author: `{reply_from.id}`")

    await message.reply("\n".join(lines))


# ---------------------------------------------------------------------------
# !backup — экспорт всех persistent данных Краба в ZIP
# ---------------------------------------------------------------------------

_BACKUP_FILES = [
    "bookmarks.json",
    "chat_monitors.json",
    "command_aliases.json",
    "saved_stickers.json",
    "personal_todos.json",
    "code_snippets.json",
    "message_templates.json",
    "saved_quotes.json",
    "welcome_messages.json",
    "silence_schedule.json",
    "spam_filter_config.json",
    "swarm_memory.json",
    "swarm_channels.json",
]


async def handle_backup(bot: "object", message: Message) -> None:
    """
    Экспортирует все persistent данные Краба в ZIP-архив и отправляет в чат.

    !backup        — создать и отправить архив
    !backup list   — показать список файлов, которые войдут в архив
    """
    import tempfile
    import zipfile as _zipfile

    args = bot._get_command_args(message).strip().lower()

    runtime_dir = pathlib.Path.home() / ".openclaw" / "krab_runtime_state"

    if args == "list":
        lines = ["📋 **Файлы в резервной копии:**\n"]
        found_count = 0
        missing_count = 0
        for fname in _BACKUP_FILES:
            fpath = runtime_dir / fname
            if fpath.exists():
                size_kb = fpath.stat().st_size / 1024
                lines.append(f"✅ `{fname}` ({size_kb:.1f} KB)")
                found_count += 1
            else:
                lines.append(f"⬜ `{fname}` _(отсутствует)_")
                missing_count += 1
        lines.append(f"\n**Итого:** {found_count} файлов найдено, {missing_count} отсутствуют.")
        await message.reply("\n".join(lines))
        return

    status_msg = await message.reply("⏳ Создаю резервную копию данных Краба…")

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            import datetime as _dt  # noqa: PLC0415

            timestamp = _dt.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            archive_name = f"krab_backup_{timestamp}.zip"
            archive_path = pathlib.Path(tmpdir) / archive_name

            included: list[str] = []
            skipped: list[str] = []

            with _zipfile.ZipFile(archive_path, "w", compression=_zipfile.ZIP_DEFLATED) as zf:
                for fname in _BACKUP_FILES:
                    fpath = runtime_dir / fname
                    if fpath.exists():
                        zf.write(fpath, arcname=fname)
                        included.append(fname)
                    else:
                        skipped.append(fname)

            if not included:
                await status_msg.edit(
                    "⚠️ Нет данных для резервной копии — ни один файл не найден.\n"
                    "Используй `!backup list` для проверки."
                )
                return

            archive_size_kb = archive_path.stat().st_size / 1024

            caption_lines = [
                f"💾 **Krab Backup** `{timestamp}`",
                f"Файлов: {len(included)} | Размер: {archive_size_kb:.1f} KB",
            ]
            if skipped:
                caption_lines.append(f"Пропущено (нет): {', '.join(skipped)}")

            await bot.client.send_document(
                chat_id=message.chat.id,
                document=str(archive_path),
                caption="\n".join(caption_lines),
                reply_to_message_id=message.id,
            )
            await status_msg.delete()

    except Exception as exc:  # noqa: BLE001
        logger.error("handle_backup_error", error=str(exc), error_type=type(exc).__name__)
        await status_msg.edit(f"❌ Ошибка создания резервной копии: {str(exc)[:300]}")
