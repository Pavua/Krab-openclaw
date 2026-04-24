#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Krab Telegram MCP Server

Продакшн-готовый MCP сервер, бриджующий Telegram-аккаунт с LLM-средами
(Claude Desktop, OpenAI Codex, Cursor, Google Antigravity).

Запуск:
  python server.py --transport stdio          # для Claude Desktop / Codex
  python server.py --transport sse --port 8001  # для web-клиентов

Переменные окружения (.env проекта):
  TELEGRAM_API_ID, TELEGRAM_API_HASH, TELEGRAM_SESSION_NAME
  KRAB_EAR_SOCKET_PATH   — путь к IPC-сокету KrabEar (для транскрипции)
  WHISPER_MODEL          — mlx-whisper fallback модель (default: mlx-community/whisper-large-v3-turbo)

Транскрипция (приоритеты):
  1. KrabEar IPC transcribe_paths  — Metal GPU, модель уже в памяти, нет cold start
  2. mlx-whisper напрямую          — fallback если KrabEar не запущен
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

import httpx
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

# ── Bootstrap путей и .env ────────────────────────────────────────────────────

_SERVER_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SERVER_DIR.parents[1]  # .../Краб/

# Добавляем нужные пути в sys.path
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
# _SERVER_DIR нужен для прямого импорта telegram_bridge
# (директория mcp-servers содержит дефис и не может быть Python-пакетом)
if str(_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVER_DIR))

_ENV_PATH = _PROJECT_ROOT / ".env"
if _ENV_PATH.exists():
    load_dotenv(_ENV_PATH, override=False)  # shell env имеет приоритет

# Импортируем TelegramBridge после того, как sys.path настроен
from telegram_bridge import TelegramBridge  # noqa: E402

# ── Константы ─────────────────────────────────────────────────────────────────

_KRAB_WEB_BASE = os.getenv("KRAB_WEB_BASE_URL", "http://127.0.0.1:8080")
_KRAB_LOG_PATH = Path(os.getenv("KRAB_LOG_PATH", str(_PROJECT_ROOT / "openclaw.log")))
_KRAB_EAR_SOCKET = Path(
    os.getenv("KRAB_EAR_SOCKET_PATH", "~/Library/Application Support/KrabEar/krabear.sock")
).expanduser()
_WHISPER_MODEL = os.getenv("WHISPER_MODEL", "mlx-community/whisper-large-v3-turbo")

# ── Singleton бридж ────────────────────────────────────────────────────────────

_bridge = TelegramBridge()

# ── FastMCP lifespan ──────────────────────────────────────────────────────────


@asynccontextmanager
async def _lifespan(server: FastMCP) -> AsyncIterator[None]:
    """Стартует Pyrogram при запуске сервера и корректно останавливает при выходе."""
    await _bridge.start()
    try:
        yield
    finally:
        await _bridge.stop()


# ── CLI args ──────────────────────────────────────────────────────────────────


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Krab Telegram MCP Server",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--transport",
        default="stdio",
        choices=["stdio", "sse"],
        help="Транспортный протокол: stdio (для Claude/Codex) или sse (для web-клиентов)",
    )
    p.add_argument("--port", type=int, default=8001, help="Порт для SSE транспорта (default: 8001)")
    p.add_argument(
        "--host", default="127.0.0.1", help="Хост для SSE транспорта (default: 127.0.0.1)"
    )
    return p.parse_known_args()[0]  # parse_known_args безопасен при вызове MCP-хостом


_args = _parse_args()

# ── FastMCP приложение ─────────────────────────────────────────────────────────

mcp = FastMCP(
    "telegram_mcp",
    instructions=(
        "MCP сервер для работы с Telegram-аккаунтом и мониторинга проекта Краб/OpenClaw. "
        "Умеет читать чаты, отправлять сообщения, транскрибировать голосовые (через KrabEar MLX), "
        "проверять runtime-статус Краба и читать логи."
    ),
    lifespan=_lifespan,
    host=_args.host,
    port=_args.port,
)

# ═════════════════════════════════════════════════════════════════════════════
# Pydantic input models
# ═════════════════════════════════════════════════════════════════════════════


class _GetDialogsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    limit: int = Field(
        default=20,
        ge=1,
        le=200,
        description="Максимальное количество диалогов для возврата (1–200)",
    )


class _GetHistoryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chat_id: str = Field(
        ..., description="ID чата или username (например: -1001234567890 или @channel_name)"
    )
    limit: int = Field(
        default=20, ge=1, le=100, description="Количество последних сообщений (1–100)"
    )


class _SendMessageInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chat_id: str = Field(..., description="ID чата или username получателя")
    text: str = Field(
        ..., min_length=1, max_length=4096, description="Текст сообщения (до 4096 символов)"
    )


class _MediaInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chat_id: str = Field(..., description="ID чата или username")
    message_id: int = Field(..., gt=0, description="ID сообщения с медиафайлом")


class _SearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Поисковый запрос (текст, имя пользователя, ключевое слово)",
    )
    limit: int = Field(
        default=20, ge=1, le=100, description="Максимальное количество результатов (1–100)"
    )


class _EditMessageInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chat_id: str = Field(..., description="ID чата или username")
    message_id: int = Field(..., gt=0, description="ID сообщения для редактирования")
    text: str = Field(..., min_length=1, max_length=4096, description="Новый текст сообщения")


class _SendPhotoInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chat_id: str = Field(..., description="ID чата или username получателя")
    photo_path: str = Field(default="", description="Локальный путь к файлу фото")
    photo_url: str = Field(default="", description="URL фото (если не задан photo_path)")
    caption: str = Field(default="", max_length=1024, description="Подпись к фото (необязательно)")


class _SendReactionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chat_id: str = Field(..., description="ID чата или username")
    message_id: int = Field(..., gt=0, description="ID сообщения для реакции")
    emoji: str = Field(..., min_length=1, description="Эмодзи реакции (например: '👍') или JSON-список")


class _ForwardMessageInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    from_chat_id: str = Field(..., description="ID чата-источника")
    message_id: int = Field(..., gt=0, description="ID пересылаемого сообщения")
    to_chat_id: str = Field(..., description="ID чата-назначения")


class _DeleteMessageInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chat_id: str = Field(..., description="ID чата или username")
    message_id: int = Field(..., gt=0, description="ID сообщения для удаления")


class _PinMessageInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chat_id: str = Field(..., description="ID чата или username")
    message_id: int = Field(..., gt=0, description="ID сообщения для закрепления")
    unpin: bool = Field(default=False, description="True — открепить, False — закрепить (default)")


class _GetMessageInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chat_id: str = Field(..., description="ID чата или username")
    message_id: int = Field(..., gt=0, description="ID сообщения")


class _SendVoiceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chat_id: str = Field(..., description="ID чата или username получателя")
    voice_path: str = Field(..., min_length=1, description="Локальный путь к .ogg файлу")
    duration: int | None = Field(default=None, ge=0, description="Длительность в секундах (необязательно)")


class _TailLogsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    n: int = Field(default=50, ge=1, le=500, description="Количество последних строк лога (1–500)")


class _MemorySearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    q: str = Field(..., max_length=500, description="Поисковый запрос")
    mode: str = Field(
        default="hybrid",
        pattern="^(fts|semantic|hybrid)$",
        description="Режим: fts | semantic | hybrid (default hybrid)",
    )
    limit: int = Field(default=5, ge=1, le=20, description="Количество результатов (1–20)")
    chat_id: str = Field(default="", description="Опциональный chat_id для ограничения поиска")


# ═════════════════════════════════════════════════════════════════════════════
# ── TELEGRAM TOOLS ────────────────────────────────────────────────────────────
# ═════════════════════════════════════════════════════════════════════════════


@mcp.tool(
    name="telegram_get_dialogs",
    annotations={"readOnlyHint": True, "destructiveHint": False},
)
async def telegram_get_dialogs(params: _GetDialogsInput) -> str:
    """Возвращает список последних диалогов Telegram-аккаунта.

    Включает личные чаты, группы и каналы. Полезно для получения chat_id
    перед использованием telegram_get_chat_history или telegram_send_message.

    Args:
        params: Параметры запроса:
            - limit (int): Количество диалогов (1–200, default: 20)

    Returns:
        str: JSON-массив объектов диалогов с полями:
             id, title, type, username, unread_count, top_message
    """
    dialogs = await _bridge.get_dialogs(limit=params.limit)
    return json.dumps(dialogs, ensure_ascii=False, indent=2)


@mcp.tool(
    name="telegram_get_chat_history",
    annotations={"readOnlyHint": True, "destructiveHint": False},
)
async def telegram_get_chat_history(params: _GetHistoryInput) -> str:
    """Получает историю сообщений из указанного чата Telegram.

    Возвращает сообщения в обратном хронологическом порядке (новые первые).
    Поддерживает как числовые chat_id, так и @username.

    Args:
        params: Параметры запроса:
            - chat_id (str): ID чата или @username (например: -1001234567890 или @my_channel)
            - limit (int): Количество сообщений (1–100, default: 20)

    Returns:
        str: JSON-массив объектов сообщений с полями:
             id, chat_id, chat_title, from_user, text, date, has_media, media_type
    """
    # chat_id может быть числом в виде строки
    try:
        cid: int | str = int(params.chat_id)
    except ValueError:
        cid = params.chat_id
    history = await _bridge.get_chat_history(cid, limit=params.limit)
    return json.dumps(history, ensure_ascii=False, indent=2)


@mcp.tool(
    name="telegram_send_message",
    annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False},
)
async def telegram_send_message(params: _SendMessageInput) -> str:
    """Отправляет текстовое сообщение в указанный чат Telegram.

    ВНИМАНИЕ: это действие отправляет реальное сообщение от имени аккаунта.

    Args:
        params: Параметры сообщения:
            - chat_id (str): ID чата или @username получателя
            - text (str): Текст сообщения (до 4096 символов)

    Returns:
        str: JSON-объект с метаданными отправленного сообщения (id, date, chat_id)
    """
    try:
        cid: int | str = int(params.chat_id)
    except ValueError:
        cid = params.chat_id
    result = await _bridge.send_message(cid, params.text)
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool(
    name="telegram_download_media",
    annotations={"readOnlyHint": True, "destructiveHint": False},
)
async def telegram_download_media(params: _MediaInput) -> str:
    """Скачивает медиафайл (фото, документ, видео, аудио) из сообщения Telegram.

    Файл сохраняется во временную директорию /tmp/krab_mcp_media/.
    Возвращает абсолютный путь к файлу для дальнейшей обработки.

    Args:
        params:
            - chat_id (str): ID чата или @username
            - message_id (int): ID сообщения, содержащего медиафайл

    Returns:
        str: JSON-объект {"file_path": "/tmp/krab_mcp_media/...", "message_id": ...}
    """
    try:
        cid: int | str = int(params.chat_id)
    except ValueError:
        cid = params.chat_id
    file_path = await _bridge.download_media(cid, params.message_id)
    return json.dumps(
        {"file_path": file_path, "message_id": params.message_id},
        ensure_ascii=False,
    )


@mcp.tool(
    name="telegram_transcribe_voice",
    annotations={"readOnlyHint": True, "destructiveHint": False},
)
async def telegram_transcribe_voice(params: _MediaInput) -> str:
    """Скачивает голосовое/аудио сообщение из Telegram и транскрибирует его.

    Использует KrabEar IPC (Metal GPU, whisper-large-v3-turbo) если KrabEar запущен.
    Fallback: mlx-whisper напрямую (те же модели, немного медленнее из-за cold start).

    Поддерживает форматы: .ogg, .opus, .mp3, .m4a, .wav, .flac.

    Args:
        params:
            - chat_id (str): ID чата или @username
            - message_id (int): ID голосового/аудио сообщения

    Returns:
        str: JSON-объект {"text": "транскрибированный текст", "source": "krabear|mlx_whisper",
                          "file_path": "/tmp/..."}
    """
    try:
        cid: int | str = int(params.chat_id)
    except ValueError:
        cid = params.chat_id

    file_path = await _bridge.get_voice_file(cid, params.message_id)

    # Приоритет 1: KrabEar IPC (Metal GPU, warm модель)
    if _KRAB_EAR_SOCKET.exists():
        text = await _transcribe_via_krabear(file_path)
        if text is not None:
            return json.dumps(
                {"text": text, "source": "krabear", "file_path": file_path},
                ensure_ascii=False,
            )

    # Fallback: mlx-whisper напрямую
    text = await asyncio.get_event_loop().run_in_executor(None, _transcribe_mlx, file_path)
    return json.dumps(
        {"text": text, "source": "mlx_whisper", "file_path": file_path},
        ensure_ascii=False,
    )


async def _transcribe_via_krabear(audio_path: str) -> str | None:
    """Отправляет файл в KrabEar IPC transcribe_paths и возвращает текст или None."""
    reader: asyncio.StreamReader | None = None
    writer: asyncio.StreamWriter | None = None
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_unix_connection(str(_KRAB_EAR_SOCKET)),
            timeout=5.0,
        )
        request = {
            "id": "mcp_transcribe",
            "method": "transcribe_paths",
            "params": {"paths": [audio_path], "quality_profile": "high"},
        }
        writer.write((json.dumps(request, ensure_ascii=False) + "\n").encode())
        await writer.drain()

        raw = await asyncio.wait_for(reader.readline(), timeout=60.0)
        if not raw:
            return None
        payload = json.loads(raw.decode("utf-8", errors="replace"))
        if not payload.get("ok"):
            return None
        items: list[dict[str, Any]] = payload.get("result", {}).get("items", [])
        return items[0].get("text", "").strip() if items else None
    except Exception:  # noqa: BLE001
        return None
    finally:
        if writer is not None:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:  # noqa: BLE001
                pass


def _transcribe_mlx(audio_path: str) -> str:
    """Синхронная транскрипция через mlx-whisper (запускается в executor)."""
    import mlx_whisper  # type: ignore

    result = mlx_whisper.transcribe(
        audio_path,
        path_or_hf_repo=_WHISPER_MODEL,
    )
    return (result.get("text") or "").strip()


@mcp.tool(
    name="telegram_search",
    annotations={"readOnlyHint": True, "destructiveHint": False},
)
async def telegram_search(params: _SearchInput) -> str:
    """Выполняет глобальный поиск по всем Telegram чатам аккаунта.

    Ищет сообщения, содержащие заданный текст, во всех доступных чатах.
    Полезно для поиска конкретной информации без знания точного чата.

    Args:
        params:
            - query (str): Поисковый запрос (минимум 1 символ)
            - limit (int): Максимальное количество результатов (1–100, default: 20)

    Returns:
        str: JSON-массив найденных сообщений с полями:
             id, chat_id, chat_title, from_user, text, date
    """
    results = await _bridge.search(params.query, limit=params.limit)
    return json.dumps(
        {"query": params.query, "count": len(results), "results": results},
        ensure_ascii=False,
        indent=2,
    )


@mcp.tool(
    name="telegram_edit_message",
    annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True},
)
async def telegram_edit_message(params: _EditMessageInput) -> str:
    """Редактирует ранее отправленное сообщение Telegram.

    Работает только для сообщений, отправленных самим аккаунтом.
    Нельзя редактировать сообщения других пользователей.

    Args:
        params:
            - chat_id (str): ID чата или @username
            - message_id (int): ID сообщения для редактирования
            - text (str): Новый текст сообщения (до 4096 символов)

    Returns:
        str: JSON-объект с обновлёнными метаданными сообщения
    """
    try:
        cid: int | str = int(params.chat_id)
    except ValueError:
        cid = params.chat_id
    result = await _bridge.edit_message(cid, params.message_id, params.text)
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool(
    name="telegram_send_photo",
    annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False},
)
async def telegram_send_photo(params: _SendPhotoInput) -> str:
    """Отправляет фото в Telegram-чат из локального файла или URL.

    Args:
        params:
            - chat_id (str): ID чата или @username
            - photo_path (str): Локальный путь к файлу (приоритет перед photo_url)
            - photo_url (str): URL фото (если photo_path не задан)
            - caption (str): Подпись к фото (до 1024 символов, необязательно)

    Returns:
        str: JSON-объект с метаданными отправленного сообщения
    """
    photo = params.photo_path.strip() or params.photo_url.strip()
    if not photo:
        return json.dumps({"ok": False, "error": "Укажи photo_path или photo_url"}, ensure_ascii=False)
    try:
        cid: int | str = int(params.chat_id)
    except ValueError:
        cid = params.chat_id
    try:
        result = await _bridge.send_photo(cid, photo, caption=params.caption)
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)


@mcp.tool(
    name="telegram_send_reaction",
    annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True},
)
async def telegram_send_reaction(params: _SendReactionInput) -> str:
    """Ставит эмодзи-реакцию на сообщение Telegram.

    Args:
        params:
            - chat_id (str): ID чата или @username
            - message_id (int): ID сообщения
            - emoji (str): Эмодзи (например: '👍', '❤️') или JSON-список эмодзи

    Returns:
        str: JSON-объект {"ok": true, "emoji": [...]}
    """
    try:
        cid: int | str = int(params.chat_id)
    except ValueError:
        cid = params.chat_id
    # Поддержка JSON-списка в строке
    try:
        emoji_val: str | list[str] = json.loads(params.emoji)
    except (json.JSONDecodeError, ValueError):
        emoji_val = params.emoji
    try:
        result = await _bridge.send_reaction(cid, params.message_id, emoji_val)
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)


@mcp.tool(
    name="telegram_forward_message",
    annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False},
)
async def telegram_forward_message(params: _ForwardMessageInput) -> str:
    """Пересылает сообщение из одного чата в другой.

    Args:
        params:
            - from_chat_id (str): ID чата-источника
            - message_id (int): ID сообщения для пересылки
            - to_chat_id (str): ID чата-назначения

    Returns:
        str: JSON-объект с метаданными пересланного сообщения
    """
    try:
        from_cid: int | str = int(params.from_chat_id)
    except ValueError:
        from_cid = params.from_chat_id
    try:
        to_cid: int | str = int(params.to_chat_id)
    except ValueError:
        to_cid = params.to_chat_id
    try:
        result = await _bridge.forward_message(from_cid, params.message_id, to_cid)
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)


@mcp.tool(
    name="telegram_delete_message",
    annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": True},
)
async def telegram_delete_message(params: _DeleteMessageInput) -> str:
    """Удаляет своё сообщение из чата Telegram.

    ВНИМАНИЕ: действие необратимо.

    Args:
        params:
            - chat_id (str): ID чата или @username
            - message_id (int): ID сообщения для удаления

    Returns:
        str: JSON-объект {"ok": true, "deleted": [message_id]}
    """
    try:
        cid: int | str = int(params.chat_id)
    except ValueError:
        cid = params.chat_id
    try:
        result = await _bridge.delete_messages(cid, params.message_id)
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)


@mcp.tool(
    name="telegram_pin_message",
    annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True},
)
async def telegram_pin_message(params: _PinMessageInput) -> str:
    """Закрепляет или открепляет сообщение в чате Telegram.

    Args:
        params:
            - chat_id (str): ID чата или @username
            - message_id (int): ID сообщения
            - unpin (bool): True — открепить, False — закрепить (default: False)

    Returns:
        str: JSON-объект {"ok": true, "action": "pinned"|"unpinned"}
    """
    try:
        cid: int | str = int(params.chat_id)
    except ValueError:
        cid = params.chat_id
    try:
        result = await _bridge.pin_message(cid, params.message_id, unpin=params.unpin)
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)


@mcp.tool(
    name="telegram_get_message",
    annotations={"readOnlyHint": True, "destructiveHint": False},
)
async def telegram_get_message(params: _GetMessageInput) -> str:
    """Получает одно сообщение Telegram по его ID.

    Возвращает полную информацию: текст, медиа, entities, from_user, дату.

    Args:
        params:
            - chat_id (str): ID чата или @username
            - message_id (int): ID сообщения

    Returns:
        str: JSON-объект с полями сообщения (id, text, from_user, date, has_media, entities, ...)
    """
    try:
        cid: int | str = int(params.chat_id)
    except ValueError:
        cid = params.chat_id
    try:
        result = await _bridge.get_message(cid, params.message_id)
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)


@mcp.tool(
    name="telegram_send_voice",
    annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False},
)
async def telegram_send_voice(params: _SendVoiceInput) -> str:
    """Отправляет голосовое сообщение (.ogg) в Telegram-чат.

    Args:
        params:
            - chat_id (str): ID чата или @username получателя
            - voice_path (str): Локальный путь к .ogg файлу
            - duration (int | None): Длительность в секундах (необязательно)

    Returns:
        str: JSON-объект с метаданными отправленного сообщения
    """
    try:
        cid: int | str = int(params.chat_id)
    except ValueError:
        cid = params.chat_id
    try:
        result = await _bridge.send_voice(cid, params.voice_path, duration=params.duration)
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)


# ═════════════════════════════════════════════════════════════════════════════
# ── КРАБ-DEV TOOLS ───────────────────────────────────────────────────────────
# ═════════════════════════════════════════════════════════════════════════════


@mcp.tool(
    name="krab_status",
    annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True},
)
async def krab_status() -> str:
    """Возвращает текущий runtime-статус Краб / OpenClaw.

    Делает GET-запрос к /api/health/lite на панели Краба (:8080).
    Показывает активного провайдера, модель, маршрут и статус всех сервисов.

    Returns:
        str: JSON-объект с полями:
             provider, model, status, last_runtime_route, services, timestamp
             Или {"error": "...", "url": "..."} если Краб не запущен.
    """
    url = f"{_KRAB_WEB_BASE}/api/health/lite"
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(url)
            data = response.json() if response.content else {}
            data["_http_status"] = response.status_code
            return json.dumps(data, ensure_ascii=False, indent=2)
    except httpx.ConnectError:
        return json.dumps(
            {"error": "Краб не запущен или недоступен", "url": url},
            ensure_ascii=False,
        )
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"error": str(exc), "url": url}, ensure_ascii=False)


@mcp.tool(
    name="krab_tail_logs",
    annotations={"readOnlyHint": True, "destructiveHint": False},
)
async def krab_tail_logs(params: _TailLogsInput) -> str:
    """Читает последние строки лога OpenClaw.

    Возвращает хвост основного лога openclaw.log для диагностики проблем
    с провайдерами, fallback-цепочкой и маршрутизацией.

    Args:
        params:
            - n (int): Количество строк (1–500, default: 50)

    Returns:
        str: JSON-объект {"log_path": "...", "lines": [...], "total_returned": N}
    """
    log_path = _KRAB_LOG_PATH
    if not log_path.exists():
        return json.dumps(
            {"error": f"Лог не найден: {log_path}", "log_path": str(log_path)},
            ensure_ascii=False,
        )
    try:
        # Читаем последние N строк эффективно
        lines: list[str] = []
        with open(log_path, "rb") as f:
            # Seek к концу файла и читаем chunk-ами
            f.seek(0, 2)
            end = f.tell()
            buf_size = min(end, params.n * 200)  # ~200 байт на строку в среднем
            f.seek(max(0, end - buf_size))
            content = f.read().decode("utf-8", errors="replace")
            lines = content.splitlines()[-params.n :]
        return json.dumps(
            {
                "log_path": str(log_path),
                "lines": lines,
                "total_returned": len(lines),
            },
            ensure_ascii=False,
            indent=2,
        )
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"error": str(exc), "log_path": str(log_path)}, ensure_ascii=False)


@mcp.tool(
    name="krab_restart_gateway",
    annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True},
)
async def krab_restart_gateway() -> str:
    """Перезапускает OpenClaw Gateway (без перезапуска всего Краба).

    Выполняет `openclaw gateway stop` → пауза 2s → `openclaw gateway start`.
    Нужно после изменений в ~/.openclaw/openclaw.json (настройки thinking, модели).

    ВНИМАНИЕ: во время перезапуска (~5с) запросы к OpenClaw будут недоступны.

    Returns:
        str: JSON-объект {"status": "restarted"|"error", "stop_output": "...", "start_output": "..."}
    """
    try:
        stop_result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: subprocess.run(
                ["openclaw", "gateway", "stop"],
                capture_output=True,
                text=True,
                timeout=15,
            ),
        )
        await asyncio.sleep(2.0)
        start_result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: subprocess.run(
                ["openclaw", "gateway", "start"],
                capture_output=True,
                text=True,
                timeout=15,
            ),
        )
        return json.dumps(
            {
                "status": "restarted",
                "stop_exit_code": stop_result.returncode,
                "stop_output": (stop_result.stdout + stop_result.stderr).strip(),
                "start_exit_code": start_result.returncode,
                "start_output": (start_result.stdout + start_result.stderr).strip(),
            },
            ensure_ascii=False,
            indent=2,
        )
    except FileNotFoundError:
        return json.dumps(
            {
                "status": "error",
                "error": "openclaw CLI не найден. Убедись что OpenClaw установлен и в PATH.",
            },
            ensure_ascii=False,
        )
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False)


# ═════════════════════════════════════════════════════════════════════════════
# krab_run_tests
# ═════════════════════════════════════════════════════════════════════════════


class _RunTestsInput(BaseModel):
    path: str = Field(
        default="tests/unit",
        description=(
            "Путь к тестам относительно корня проекта Краба. "
            "Примеры: 'tests/unit', 'tests/unit/test_access_control.py', "
            "'tests/unit/test_userbot_stream_timeouts.py::test_soft_timeout_fires'. "
            "Только пути внутри директории tests/ — другие отклоняются."
        ),
    )
    extra_args: list[str] = Field(
        default_factory=list,
        description=(
            "Дополнительные аргументы pytest. Например: ['-v'], ['-k', 'test_foo'], "
            "['--tb=short']. Не передавай '--no-header' или '--rootdir' — они выставляются автоматически."
        ),
    )


@mcp.tool(
    name="krab_run_tests",
    annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True},
)
async def krab_run_tests(params: _RunTestsInput) -> str:
    """Запускает тесты проекта Краба через pytest и возвращает результат.

    Инструмент запускает pytest в директории /Users/pablito/Antigravity_AGENTS/Краб/
    используя venv-окружение проекта. Позволяет быстро проверить, не сломала ли
    правка существующие тесты, без выхода из Claude Code.

    Ограничения безопасности:
      - Путь должен начинаться с 'tests/' (защита от выполнения произвольного кода)
      - Hard timeout: 120 секунд
      - Запускается только read-only pytest (без --forked, без --html с записью)

    Args:
        params.path: Путь к тестам (default: "tests/unit"). Может быть директорией
                     или конкретным файлом/тестом (file::test_name).
        params.extra_args: Дополнительные pytest-аргументы (default: []).

    Returns:
        str: JSON {"status": "passed"|"failed"|"error", "exit_code": N,
                   "output": "...", "summary": "последняя строка вывода"}
    """
    project_root = Path("/Users/pablito/Antigravity_AGENTS/Краб")
    # Единый venv (Python 3.13 + pyrofork 2.3.69), синхронизирован с runtime.
    # После унификации venv в batch 7 (commit 00d6a41) старый .venv (Py3.12) удалён.
    python_bin = project_root / "venv" / "bin" / "python"

    # Security: only paths inside tests/
    normalized = params.path.lstrip("/").lstrip("./")
    if not normalized.startswith("tests/") and normalized != "tests":
        return json.dumps(
            {
                "status": "error",
                "error": f"Недопустимый путь '{params.path}'. Только пути внутри tests/ разрешены.",
            },
            ensure_ascii=False,
        )

    # Sanitize extra_args: no shell metacharacters
    safe_extra: list[str] = []
    for arg in params.extra_args:
        if any(c in arg for c in (";", "&", "|", "`", "$", ">")):
            return json.dumps(
                {"status": "error", "error": f"Недопустимый аргумент: '{arg}'"},
                ensure_ascii=False,
            )
        safe_extra.append(arg)

    cmd = [
        str(python_bin),
        "-m",
        "pytest",
        normalized,
        "-q",
        "--tb=short",
        "--no-header",
        *safe_extra,
    ]

    try:
        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120,
                cwd=str(project_root),
            ),
        )
        output = (result.stdout + result.stderr).strip()
        summary = output.splitlines()[-1] if output else ""
        status = "passed" if result.returncode == 0 else "failed"
        return json.dumps(
            {
                "status": status,
                "exit_code": result.returncode,
                "summary": summary,
                "output": output,
            },
            ensure_ascii=False,
            indent=2,
        )
    except subprocess.TimeoutExpired:
        return json.dumps(
            {"status": "error", "error": "Timeout: тесты выполнялись более 120 секунд"},
            ensure_ascii=False,
        )
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False)


# ═════════════════════════════════════════════════════════════════════════════
# ── MEMORY TOOLS ─────────────────────────────────────────────────────────────
# ═════════════════════════════════════════════════════════════════════════════


@mcp.tool(
    name="krab_memory_search",
    annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True},
)
async def krab_memory_search(params: _MemorySearchInput) -> str:
    """Полнотекстовый + semantic hybrid поиск по Krab Memory Layer (archive.db).

    Результаты уже PII-redacted (email/телефоны/карты маскированы в `text_redacted`).
    База: `~/.openclaw/krab_memory/archive.db` (~43k messages / ~9k chunks).

    Используется:
      1. Прямой вызов `HybridRetriever.search()` (in-process, без HTTP).
      2. Fallback на Krab panel `GET /api/memory/search` если модуль недоступен.

    Args:
        params:
            - q (str): поисковый запрос (обязателен)
            - mode (str): "fts" | "semantic" | "hybrid" (default "hybrid")
            - limit (int): 1–20 (default 5)
            - chat_id (str, опц.): ограничение поиска по chat_id

    Returns:
        str: JSON {"ok": bool, "query": str, "mode": str, "count": int,
                   "results": [{"chunk_id", "text", "score", "chat_id", "timestamp"}]}
    """
    q = (params.q or "").strip()
    if not q:
        return json.dumps({"ok": False, "error": "empty_query"}, ensure_ascii=False)

    limit = min(max(int(params.limit), 1), 20)
    mode = params.mode or "hybrid"
    chat_id = params.chat_id.strip() or None

    # Путь 1: прямой вызов HybridRetriever (in-process)
    try:
        from src.core.memory_retrieval import HybridRetriever

        def _run_search():
            retriever = HybridRetriever()
            try:
                return retriever.search(
                    q,
                    chat_id=chat_id,
                    top_k=limit,
                    with_context=0,
                    decay_mode="auto",
                )
            finally:
                retriever.close()

        loop = asyncio.get_event_loop()
        results = await loop.run_in_executor(None, _run_search)

        payload = {
            "ok": True,
            "query": q,
            "mode": mode,
            "count": len(results),
            "results": [
                {
                    "chunk_id": r.message_id,
                    "chat_id": r.chat_id,
                    "text": (r.text_redacted[:500] + "...")
                    if len(r.text_redacted) > 500
                    else r.text_redacted,
                    "score": round(float(r.score), 4),
                    "timestamp": r.timestamp.isoformat() if r.timestamp else None,
                }
                for r in results
            ],
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)
    except ImportError:
        # Fallback на HTTP endpoint панели
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.get(
                    f"{_KRAB_WEB_BASE}/api/memory/search",
                    params={"q": q, "mode": mode, "limit": limit},
                )
                data = r.json() if r.content else {}
                return json.dumps(data, ensure_ascii=False, indent=2)
        except Exception as exc:  # noqa: BLE001
            return json.dumps(
                {"ok": False, "error": f"search_failed: {exc}"},
                ensure_ascii=False,
            )
    except Exception as exc:  # noqa: BLE001
        return json.dumps(
            {"ok": False, "error": f"search_failed: {exc}"},
            ensure_ascii=False,
        )


@mcp.tool(
    name="krab_memory_stats",
    annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True},
)
async def krab_memory_stats() -> str:
    """Статистика Krab Memory Layer: counts по messages/chats/chunks/embedded.

    Читает archive.db в read-only режиме. Если файла нет — возвращает
    `{"archive": {"exists": false}}` без ошибки.

    Returns:
        str: JSON {"archive": {"exists": bool, "messages": int, "chats": int,
                               "chunks": int, "embedded": int, "size_mb": float,
                               "schema_version": int, "path": str}}
    """
    import sqlite3

    db_path = Path("~/.openclaw/krab_memory/archive.db").expanduser()
    archive: dict[str, Any] = {"exists": db_path.exists(), "path": str(db_path)}

    if archive["exists"]:
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            try:
                archive["messages"] = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
                archive["chats"] = conn.execute("SELECT COUNT(*) FROM chats").fetchone()[0]
                archive["chunks"] = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
                archive["size_mb"] = round(db_path.stat().st_size / 1024 / 1024, 2)

                # Schema version из meta
                try:
                    row = conn.execute(
                        "SELECT value FROM meta WHERE key = 'schema_version';"
                    ).fetchone()
                    archive["schema_version"] = int(row[0]) if row else None
                except (sqlite3.OperationalError, ValueError, TypeError):
                    archive["schema_version"] = None

                # Embedded chunks — считаем через vec_chunks_rowids (sqlite-vec индекс).
                # Fallback: legacy-таблица vec_chunks (если мигрировано).
                # Совпадает с логикой collect_memory_stats() из src/core/memory_stats.py.
                try:
                    archive["embedded"] = conn.execute(
                        "SELECT COUNT(*) FROM vec_chunks_rowids"
                    ).fetchone()[0]
                except sqlite3.OperationalError:
                    try:
                        archive["embedded"] = conn.execute(
                            "SELECT COUNT(*) FROM vec_chunks"
                        ).fetchone()[0]
                    except sqlite3.OperationalError:
                        archive["embedded"] = 0
                # Поле encoded_chunks — синоним для совместимости с /api/memory/stats.
                archive["encoded_chunks"] = archive["embedded"]
            finally:
                conn.close()
        except Exception as exc:  # noqa: BLE001
            archive["error"] = str(exc)

    return json.dumps({"archive": archive}, ensure_ascii=False, indent=2, default=str)


# ═════════════════════════════════════════════════════════════════════════════
# ── FILESYSTEM TOOLS (read-only, sandboxed к _PROJECT_ROOT) ──────────────────
# ═════════════════════════════════════════════════════════════════════════════
#
# Security model (sanitize_path):
#   1. Путь резолвится через Path.resolve() — симлинки и «..» разворачиваются.
#   2. Проверяем `is_relative_to(_PROJECT_ROOT)` — запрет escape из sandbox.
#   3. Все fs_* операции READ-ONLY (никаких write/mkdir/rm).
#   4. Жёсткие лимиты: fs_read_file ≤ 500 строк, fs_search max_results ≤ 200.


def _sanitize_path(raw: str) -> Path:
    """Резолвит путь и проверяет что он внутри _PROJECT_ROOT. Иначе ValueError."""
    if not raw or not isinstance(raw, str):
        raise ValueError("empty_path")
    # Разрешаем как абсолютные, так и относительные (от project root).
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = _PROJECT_ROOT / candidate
    resolved = candidate.resolve()
    try:
        resolved.relative_to(_PROJECT_ROOT.resolve())
    except ValueError as exc:
        raise ValueError(f"path_escape: {raw} вне sandbox {_PROJECT_ROOT}") from exc
    return resolved


class _FsReadInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(..., description="Путь к файлу (absolute или относительно корня проекта)")
    start_line: int = Field(default=1, ge=1, description="Стартовая строка (1-indexed)")
    end_line: int | None = Field(
        default=None,
        description="Последняя строка (inclusive). Если None — читаем start_line + 500.",
    )


class _FsSearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pattern: str = Field(..., min_length=1, max_length=500, description="Regex или литерал")
    glob: str = Field(default="", description="Glob фильтр, напр. '*.py' или 'src/**/*.py'")
    max_results: int = Field(default=50, ge=1, le=200, description="Лимит совпадений (1–200)")


class _FsListDirInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(default=".", description="Путь к директории (default: корень проекта)")


@mcp.tool(
    name="fs_read_file",
    annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True},
)
async def fs_read_file(params: _FsReadInput) -> str:
    """Читает файл из sandbox проекта Краба (read-only, ≤500 строк за запрос).

    Returns:
        JSON {"ok": bool, "path": str, "start_line": int, "end_line": int,
              "total_lines": int, "content": str, "truncated": bool}
    """
    try:
        target = _sanitize_path(params.path)
    except ValueError as exc:
        return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)
    if not target.exists():
        return json.dumps({"ok": False, "error": f"not_found: {target}"}, ensure_ascii=False)
    if not target.is_file():
        return json.dumps({"ok": False, "error": f"not_a_file: {target}"}, ensure_ascii=False)
    try:
        with open(target, encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"ok": False, "error": f"read_failed: {exc}"}, ensure_ascii=False)

    total = len(all_lines)
    start = max(1, params.start_line)
    # Максимум 500 строк за вызов
    default_end = start + 499
    end = params.end_line if params.end_line is not None else default_end
    end = min(end, start + 499, total)
    if start > total:
        return json.dumps(
            {"ok": True, "path": str(target), "start_line": start, "end_line": start,
             "total_lines": total, "content": "", "truncated": False},
            ensure_ascii=False,
        )
    slice_lines = all_lines[start - 1 : end]
    return json.dumps(
        {
            "ok": True,
            "path": str(target),
            "start_line": start,
            "end_line": end,
            "total_lines": total,
            "content": "".join(slice_lines),
            "truncated": end < total,
        },
        ensure_ascii=False,
    )


@mcp.tool(
    name="fs_search",
    annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True},
)
async def fs_search(params: _FsSearchInput) -> str:
    """Полнотекстовый поиск по файлам проекта Краба через ripgrep (read-only).

    Returns:
        JSON {"ok": bool, "pattern": str, "count": int,
              "matches": [{"path","line","text"}]}
    """
    rg_cmd = ["rg", "--no-heading", "--line-number", "--color=never",
              "--max-count", str(params.max_results), "-e", params.pattern]
    if params.glob:
        rg_cmd.extend(["--glob", params.glob])
    rg_cmd.append(str(_PROJECT_ROOT))
    try:
        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: subprocess.run(rg_cmd, capture_output=True, text=True, timeout=15),
        )
    except FileNotFoundError:
        return json.dumps({"ok": False, "error": "ripgrep_not_installed"}, ensure_ascii=False)
    except subprocess.TimeoutExpired:
        return json.dumps({"ok": False, "error": "search_timeout"}, ensure_ascii=False)
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)

    matches: list[dict[str, Any]] = []
    for line in (result.stdout or "").splitlines():
        # Формат: path:line:text
        parts = line.split(":", 2)
        if len(parts) < 3:
            continue
        path_s, line_s, text_s = parts
        try:
            line_n = int(line_s)
        except ValueError:
            continue
        matches.append({"path": path_s, "line": line_n, "text": text_s})
        if len(matches) >= params.max_results:
            break
    return json.dumps(
        {"ok": True, "pattern": params.pattern, "count": len(matches), "matches": matches},
        ensure_ascii=False,
    )


@mcp.tool(
    name="fs_list_dir",
    annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True},
)
async def fs_list_dir(params: _FsListDirInput) -> str:
    """Листинг директории (structured `ls -la`).

    Returns:
        JSON {"ok": bool, "path": str, "entries":
              [{"name","type","size","mtime"}]}
    """
    try:
        target = _sanitize_path(params.path)
    except ValueError as exc:
        return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)
    if not target.exists():
        return json.dumps({"ok": False, "error": f"not_found: {target}"}, ensure_ascii=False)
    if not target.is_dir():
        return json.dumps({"ok": False, "error": f"not_a_dir: {target}"}, ensure_ascii=False)
    entries: list[dict[str, Any]] = []
    try:
        for item in sorted(target.iterdir(), key=lambda p: p.name):
            try:
                st = item.stat()
            except OSError:
                continue
            entries.append(
                {
                    "name": item.name,
                    "type": "dir" if item.is_dir() else ("link" if item.is_symlink() else "file"),
                    "size": st.st_size,
                    "mtime": int(st.st_mtime),
                }
            )
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"ok": False, "error": f"list_failed: {exc}"}, ensure_ascii=False)
    return json.dumps(
        {"ok": True, "path": str(target), "entries": entries},
        ensure_ascii=False,
    )


# ═════════════════════════════════════════════════════════════════════════════
# ── GIT TOOLS (read-only) ─────────────────────────────────────────────────────
# ═════════════════════════════════════════════════════════════════════════════


class _GitLogInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    limit: int = Field(default=10, ge=1, le=100, description="Количество коммитов (1–100)")
    file: str = Field(default="", description="Опциональный путь файла (лог по файлу)")


class _GitDiffInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    file: str = Field(default="", description="Опциональный путь файла")
    staged: bool = Field(default=False, description="True — diff staged изменений")


async def _run_git(args: list[str], timeout: float = 10.0) -> tuple[int, str]:
    """Запускает git в _PROJECT_ROOT, возвращает (exit_code, output)."""
    try:
        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: subprocess.run(
                ["git", *args],
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=str(_PROJECT_ROOT),
            ),
        )
        out = (result.stdout + result.stderr).rstrip()
        return result.returncode, out
    except subprocess.TimeoutExpired:
        return 124, "git_timeout"
    except FileNotFoundError:
        return 127, "git_not_installed"


@mcp.tool(
    name="git_status",
    annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True},
)
async def git_status() -> str:
    """Короткий git status (porcelain). READ-ONLY."""
    code, out = await _run_git(["status", "--short", "--branch"])
    return json.dumps({"ok": code == 0, "exit_code": code, "output": out}, ensure_ascii=False)


@mcp.tool(
    name="git_log",
    annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True},
)
async def git_log(params: _GitLogInput) -> str:
    """Последние N коммитов. Если задан file — лог по файлу."""
    args = ["log", f"-n{params.limit}", "--oneline", "--no-decorate"]
    if params.file:
        try:
            fp = _sanitize_path(params.file)
            args.extend(["--", str(fp.relative_to(_PROJECT_ROOT.resolve()))])
        except ValueError as exc:
            return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)
    code, out = await _run_git(args)
    return json.dumps({"ok": code == 0, "exit_code": code, "output": out}, ensure_ascii=False)


@mcp.tool(
    name="git_diff",
    annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True},
)
async def git_diff(params: _GitDiffInput) -> str:
    """git diff (опционально --staged, опционально по файлу). Вывод обрезается до 50k символов."""
    args = ["diff"]
    if params.staged:
        args.append("--staged")
    if params.file:
        try:
            fp = _sanitize_path(params.file)
            args.extend(["--", str(fp.relative_to(_PROJECT_ROOT.resolve()))])
        except ValueError as exc:
            return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)
    code, out = await _run_git(args, timeout=15.0)
    truncated = False
    if len(out) > 50_000:
        out = out[:50_000]
        truncated = True
    return json.dumps(
        {"ok": code == 0, "exit_code": code, "output": out, "truncated": truncated},
        ensure_ascii=False,
    )


# ═════════════════════════════════════════════════════════════════════════════
# ── SYSTEM INFO ──────────────────────────────────────────────────────────────
# ═════════════════════════════════════════════════════════════════════════════


@mcp.tool(
    name="system_info",
    annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True},
)
async def system_info() -> str:
    """CPU/RAM/Disk/uptime/load_avg хоста. Использует psutil если есть, иначе os/shutil."""
    import platform as _pl
    import shutil

    info: dict[str, Any] = {
        "platform": _pl.platform(),
        "python": _pl.python_version(),
    }
    try:
        import psutil  # type: ignore

        info["cpu_count"] = psutil.cpu_count(logical=True)
        info["cpu_percent"] = psutil.cpu_percent(interval=0.1)
        vm = psutil.virtual_memory()
        info["ram"] = {
            "total_mb": round(vm.total / 1024 / 1024),
            "available_mb": round(vm.available / 1024 / 1024),
            "percent": vm.percent,
        }
        du = psutil.disk_usage(str(_PROJECT_ROOT))
        info["disk"] = {
            "total_gb": round(du.total / 1024**3, 2),
            "free_gb": round(du.free / 1024**3, 2),
            "percent": du.percent,
        }
        info["uptime_sec"] = int(__import__("time").time() - psutil.boot_time())
    except ImportError:
        du = shutil.disk_usage(str(_PROJECT_ROOT))
        info["disk"] = {
            "total_gb": round(du.total / 1024**3, 2),
            "free_gb": round(du.free / 1024**3, 2),
        }
        info["cpu_count"] = os.cpu_count()

    try:
        load1, load5, load15 = os.getloadavg()
        info["load_avg"] = {"1m": round(load1, 2), "5m": round(load5, 2), "15m": round(load15, 2)}
    except (OSError, AttributeError):
        info["load_avg"] = None

    return json.dumps(info, ensure_ascii=False, indent=2)


# ═════════════════════════════════════════════════════════════════════════════
# ── HTTP FETCH ───────────────────────────────────────────────────────────────
# ═════════════════════════════════════════════════════════════════════════════


class _HttpFetchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(..., min_length=7, description="URL (http://... или https://...)")
    method: str = Field(default="GET", pattern="^(GET|HEAD)$", description="GET|HEAD")
    timeout: float = Field(default=15.0, ge=1.0, le=60.0, description="Timeout в секундах")


_HTTP_MAX_BYTES = 100 * 1024  # 100KB


@mcp.tool(
    name="http_fetch",
    annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True},
)
async def http_fetch(params: _HttpFetchInput) -> str:
    """HTTP GET/HEAD с redirects. Тело ограничено 100KB (обрезается).

    Returns:
        JSON {"ok","status","headers","body","truncated","final_url"}
    """
    if not (params.url.startswith("http://") or params.url.startswith("https://")):
        return json.dumps({"ok": False, "error": "invalid_scheme"}, ensure_ascii=False)
    try:
        async with httpx.AsyncClient(
            timeout=params.timeout, follow_redirects=True, max_redirects=5
        ) as client:
            r = await client.request(params.method, params.url)
            raw = r.content or b""
            truncated = len(raw) > _HTTP_MAX_BYTES
            raw = raw[:_HTTP_MAX_BYTES]
            try:
                body = raw.decode("utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                body = f"<binary:{len(raw)} bytes>"
            return json.dumps(
                {
                    "ok": True,
                    "status": r.status_code,
                    "final_url": str(r.url),
                    "headers": dict(r.headers),
                    "body": body,
                    "truncated": truncated,
                },
                ensure_ascii=False,
            )
    except httpx.TimeoutException:
        return json.dumps({"ok": False, "error": "timeout"}, ensure_ascii=False)
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)


# ═════════════════════════════════════════════════════════════════════════════
# ── TIME / CALENDAR ──────────────────────────────────────────────────────────
# ═════════════════════════════════════════════════════════════════════════════


class _TimeNowInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    timezone: str = Field(default="Europe/Madrid", description="IANA timezone")


class _TimeParseInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(..., min_length=1, max_length=200, description="Текст: «завтра 15:00»")
    timezone: str = Field(default="Europe/Madrid", description="IANA timezone для относительных")


@mcp.tool(
    name="time_now",
    annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True},
)
async def time_now(params: _TimeNowInput) -> str:
    """Текущее время в указанной timezone (ISO 8601)."""
    from datetime import datetime
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    try:
        tz = ZoneInfo(params.timezone)
    except ZoneInfoNotFoundError:
        return json.dumps(
            {"ok": False, "error": f"unknown_timezone: {params.timezone}"}, ensure_ascii=False
        )
    now = datetime.now(tz)
    return json.dumps(
        {
            "ok": True,
            "iso": now.isoformat(),
            "timezone": params.timezone,
            "unix": int(now.timestamp()),
            "weekday": now.strftime("%A"),
        },
        ensure_ascii=False,
    )


@mcp.tool(
    name="time_parse",
    annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True},
)
async def time_parse(params: _TimeParseInput) -> str:
    """Парсит свободный текст («завтра 15:00», «in 2 hours») в ISO."""
    try:
        import dateparser  # type: ignore
    except ImportError:
        return json.dumps({"ok": False, "error": "dateparser_not_installed"}, ensure_ascii=False)

    parsed = dateparser.parse(
        params.text,
        settings={"TIMEZONE": params.timezone, "RETURN_AS_TIMEZONE_AWARE": True,
                  "PREFER_DATES_FROM": "future"},
    )
    if parsed is None:
        return json.dumps(
            {"ok": False, "error": "parse_failed", "text": params.text}, ensure_ascii=False
        )
    return json.dumps(
        {
            "ok": True,
            "iso": parsed.isoformat(),
            "unix": int(parsed.timestamp()),
            "text": params.text,
            "timezone": params.timezone,
        },
        ensure_ascii=False,
    )


# ═════════════════════════════════════════════════════════════════════════════
# ── DB QUERY (READ-ONLY) ─────────────────────────────────────────────────────
# ═════════════════════════════════════════════════════════════════════════════

import re as _re  # noqa: E402
import sqlite3 as _sqlite3  # noqa: E402

# Белый список БД, к которым разрешён запрос.
_DB_WHITELIST: dict[str, Path] = {
    "archive": Path("/Users/pablito/.openclaw/krab_memory/archive.db"),
    "memory": _PROJECT_ROOT / "memory_db" / "chroma.sqlite3",
}

# Запрещённые write-ключевые слова (regex word-boundaries, case-insensitive).
_FORBIDDEN_SQL_KEYWORDS = (
    "INSERT", "UPDATE", "DELETE", "DROP", "CREATE", "ALTER",
    "ATTACH", "DETACH", "REPLACE", "TRUNCATE", "VACUUM", "REINDEX",
)

# PRAGMA — разрешаем только read-only подмножество (schema_version, table_info и т.п.).
# На всякий случай блокируем PRAGMA writes (с '=').
_PRAGMA_WRITE_RE = _re.compile(r"\bPRAGMA\b[^;]*=", _re.IGNORECASE)


def _is_read_only_sql(sql: str) -> bool:
    """True если SQL — только SELECT / WITH / EXPLAIN и без запретных ключевых слов."""
    s = sql.strip().rstrip(";").strip()
    if not s:
        return False
    # Только один statement (запрет ';' внутри, кроме trailing).
    if ";" in s:
        return False
    head = s.split(None, 1)[0].upper()
    if head not in ("SELECT", "WITH", "EXPLAIN"):
        return False
    # Проверяем отсутствие write-ключевых слов (word boundaries).
    for kw in _FORBIDDEN_SQL_KEYWORDS:
        if _re.search(rf"\b{kw}\b", s, _re.IGNORECASE):
            return False
    if _PRAGMA_WRITE_RE.search(s):
        return False
    return True


class _DbQueryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sql: str = Field(..., min_length=1, max_length=10_000, description="SELECT / WITH / EXPLAIN")
    db_name: str = Field(default="archive", description="archive | memory")
    limit: int = Field(default=100, ge=1, le=500, description="Max rows (cap 500)")


def _run_db_query_sync(sql: str, db_path: Path, limit: int, timeout_sec: float = 10.0) -> dict:
    """Синхронный executor — вызывается через asyncio.to_thread для timeout."""
    conn = _sqlite3.connect(
        f"file:{db_path}?mode=ro",
        uri=True,
        timeout=timeout_sec,
        isolation_level=None,
    )
    try:
        # Пытаемся подгрузить sqlite-vec (для vec_* tables). Best-effort.
        try:
            import sqlite_vec  # type: ignore

            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            conn.enable_load_extension(False)
        except Exception:  # noqa: BLE001
            pass

        # Busy timeout — чтобы не зависнуть надолго.
        conn.execute(f"PRAGMA busy_timeout = {int(timeout_sec * 1000)};")

        cur = conn.execute(sql)
        rows_raw = cur.fetchmany(limit)
        columns = [d[0] for d in (cur.description or [])]
        rows = [list(r) for r in rows_raw]
        return {"columns": columns, "rows": rows, "row_count": len(rows)}
    finally:
        try:
            conn.close()
        except _sqlite3.Error:
            pass


@mcp.tool(
    name="db_query",
    annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True},
)
async def db_query(params: _DbQueryInput) -> str:
    """READ-ONLY SQL query к whitelisted БД (archive/memory).

    Разрешены только SELECT / WITH / EXPLAIN. sqlite-vec extension подгружается
    автоматически для запросов по vec_* таблицам.
    """
    # 1) read-only statement check.
    if not _is_read_only_sql(params.sql):
        return json.dumps(
            {"ok": False, "error": "not_read_only_statement"}, ensure_ascii=False
        )

    # 2) whitelisted db_name check.
    db_path = _DB_WHITELIST.get(params.db_name)
    if db_path is None:
        return json.dumps(
            {
                "ok": False,
                "error": "db_not_whitelisted",
                "allowed": sorted(_DB_WHITELIST.keys()),
            },
            ensure_ascii=False,
        )

    # 3) limit cap (хотя pydantic уже ограничил, страхуемся).
    limit = min(max(1, int(params.limit)), 500)

    # 4) execute с timeout через asyncio.
    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(_run_db_query_sync, params.sql, db_path, limit),
            timeout=10.0,
        )
    except asyncio.TimeoutError:
        return json.dumps({"ok": False, "error": "timeout"}, ensure_ascii=False)
    except _sqlite3.Error as exc:
        return json.dumps(
            {"ok": False, "error": "sql_error", "detail": str(exc)}, ensure_ascii=False
        )
    except Exception as exc:  # noqa: BLE001
        return json.dumps(
            {"ok": False, "error": "sql_error", "detail": str(exc)}, ensure_ascii=False
        )

    return json.dumps(
        {
            "ok": True,
            "db_name": params.db_name,
            "columns": result["columns"],
            "rows": result["rows"],
            "row_count": result["row_count"],
        },
        ensure_ascii=False,
        default=str,
    )


# ═════════════════════════════════════════════════════════════════════════════
# Entry point
# ═════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    mcp.run(transport=_args.transport)
