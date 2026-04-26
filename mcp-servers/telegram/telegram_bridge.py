# -*- coding: utf-8 -*-
"""
Telegram Bridge — Pyrogram singleton wrapper.

Отвечает исключительно за Telegram API: инициализация клиента, lifecycle,
и все операции с чатами/сообщениями. Не содержит MCP-зависимостей.

Конфигурация через переменные окружения:
  TELEGRAM_API_ID          — числовой App ID (обязательно)
  TELEGRAM_API_HASH        — App Hash (обязательно)
  TELEGRAM_SESSION_NAME    — базовое имя сессии (default: "krab")
  MCP_TELEGRAM_SESSION_DIR — директория для хранения .session файла
                             (default: ~/.krab_mcp_sessions/)
"""
from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path
from typing import Any

from pyrogram import Client
from pyrogram.types import Message

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _session_dir() -> Path:
    """Директория для хранения Telegram session-файла MCP."""
    custom = os.getenv("MCP_TELEGRAM_SESSION_DIR", "").strip()
    if custom:
        p = Path(custom).expanduser()
    else:
        p = Path.home() / ".krab_mcp_sessions"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _session_name() -> str:
    """Имя session-файла: отдельное от боевого Краба, не конфликтует."""
    base = os.getenv("TELEGRAM_SESSION_NAME", "krab").strip()
    return f"{base}_mcp"


def _make_client() -> Client:
    api_id_raw = os.getenv("TELEGRAM_API_ID", "").strip()
    api_hash = os.getenv("TELEGRAM_API_HASH", "").strip()
    if not api_id_raw or not api_hash:
        raise RuntimeError(
            "TELEGRAM_API_ID и TELEGRAM_API_HASH должны быть заданы в .env"
        )
    session_path = str(_session_dir() / _session_name())
    return Client(
        name=session_path,
        api_id=int(api_id_raw),
        api_hash=api_hash,
        no_updates=True,  # MCP сервер делает только pull-операции; push-апдейты не нужны
                          # и вызывают ValueError("Peer id invalid") на неизвестных peer-ах,
                          # что роняет весь процесс.
    )


def _msg_to_dict(msg: Message) -> dict[str, Any]:
    """Преобразует Pyrogram Message в JSON-сериализуемый dict."""
    return {
        "id": msg.id,
        "chat_id": msg.chat.id if msg.chat else None,
        "chat_title": getattr(msg.chat, "title", None) or getattr(msg.chat, "first_name", None),
        "from_user": msg.from_user.first_name if msg.from_user else None,
        "text": msg.text or msg.caption or "",
        "date": msg.date.isoformat() if msg.date else None,
        "has_media": msg.media is not None,
        "media_type": str(msg.media) if msg.media else None,
        "reply_to_message_id": msg.reply_to_message_id,
    }


def _resolve_parse_mode(raw: str | None):
    """Конвертит string parse_mode в pyrogram.enums.ParseMode.

    Поддерживает 'markdown' / 'html' / 'disabled' (case-insensitive).
    None или пустая строка → возвращает None (Pyrogram default).
    """
    if not raw:
        return None
    try:
        from pyrogram.enums import ParseMode
    except ImportError:
        return None
    key = raw.strip().lower()
    mapping = {
        "markdown": ParseMode.MARKDOWN,
        "md": ParseMode.MARKDOWN,
        "html": ParseMode.HTML,
        "disabled": ParseMode.DISABLED,
        "none": ParseMode.DISABLED,
    }
    return mapping.get(key)


class TelegramBridge:
    """
    Singleton-обёртка над Pyrogram Client.

    Использование:
        bridge = TelegramBridge()
        await bridge.start()    # ← вызывается из FastMCP lifespan
        ...
        await bridge.stop()     # ← вызывается из FastMCP lifespan
    """

    def __init__(self) -> None:
        self._client: Client | None = None
        self._client_lock = asyncio.Lock()
        self._operation_lock = asyncio.Lock()

    # ─── Lifecycle ────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Стартует Pyrogram Client. Вызывается из FastMCP lifespan."""
        async with self._client_lock:
            if self._client is not None:
                return
            self._client = _make_client()
            await self._client.start()

    async def stop(self) -> None:
        """Корректно останавливает клиент. Вызывается из FastMCP lifespan."""
        async with self._client_lock:
            if self._client is not None:
                try:
                    await self._client.stop()
                except Exception:  # noqa: BLE001
                    pass
                finally:
                    self._client = None

    @property
    def client(self) -> Client:
        if self._client is None:
            raise RuntimeError("TelegramBridge не инициализирован. Вызови start() сначала.")
        return self._client

    @staticmethod
    def _is_session_lock_error(exc: Exception) -> bool:
        """Определяет transient SQLite-lock на session-файле Pyrogram."""
        return "database is locked" in str(exc).lower()

    @staticmethod
    def _is_connection_error(exc: Exception) -> bool:
        """Определяет ошибки потери соединения Pyrogram (disconnect, timeout)."""
        msg = str(exc).lower()
        return (
            isinstance(exc, (ConnectionError, OSError, RuntimeError))
            or "not initialized" in msg
            or "не инициализирован" in msg
            or "client has not been started" in msg
            or "disconnected" in msg
            or "connection" in msg and "error" in msg
        )

    async def _restart_client_locked(self) -> None:
        """
        Перезапускает Pyrogram-клиент под lock.

        Почему restart допустим:
        - MCP server использует один singleton-клиент;
        - session lock / disconnect чаще всего означает transient failure;
        - один controlled restart дешевле, чем оставлять весь MCP transport в ошибке.
        """
        async with self._client_lock:
            if self._client is not None:
                try:
                    await self._client.stop()
                except Exception:  # noqa: BLE001
                    pass
                finally:
                    self._client = None
            self._client = _make_client()
            await self._client.start()

    async def _ensure_connected(self) -> Client:
        """Возвращает клиент, переподключаясь если сессия отвалилась."""
        if self._client is None:
            await self._restart_client_locked()
        return self._client  # type: ignore[return-value]

    async def _run_client_call(self, callback):
        """
        Сериализует Telegram API вызовы и один раз переживает session-lock или disconnect.

        Почему сериализация нужна:
        - Pyrogram session живёт в sqlite-файле;
        - параллельные операции нескольких MCP tool-call'ов после restart иногда
          ловят `database is locked`;
        - для MCP tooling надёжность важнее, чем максимальный параллелизм.
        """
        async with self._operation_lock:
            try:
                client = await self._ensure_connected()
                return await callback(client)
            except Exception as exc:  # noqa: BLE001
                if not (self._is_session_lock_error(exc) or self._is_connection_error(exc)):
                    raise
                await self._restart_client_locked()
                return await callback(self.client)

    # ─── Telegram API методы ──────────────────────────────────────────────────

    async def get_dialogs(self, limit: int = 20) -> list[dict[str, Any]]:
        """Возвращает список последних диалогов (чаты, группы, каналы)."""
        async def _op(client: Client) -> list[dict[str, Any]]:
            result: list[dict[str, Any]] = []
            async for dialog in client.get_dialogs(limit=limit):
                chat = dialog.chat
                result.append({
                    "id": chat.id,
                    "title": getattr(chat, "title", None) or getattr(chat, "first_name", None),
                    "type": str(chat.type),
                    "username": getattr(chat, "username", None),
                    "unread_count": dialog.unread_messages_count,
                    "top_message": dialog.top_message.text if dialog.top_message else None,
                })
            return result

        return await self._run_client_call(_op)

    async def get_chat_history(
        self, chat_id: int | str, limit: int = 20
    ) -> list[dict[str, Any]]:
        """Возвращает последние сообщения из чата."""
        async def _op(client: Client) -> list[dict[str, Any]]:
            messages: list[dict[str, Any]] = []
            async for msg in client.get_chat_history(chat_id, limit=limit):
                messages.append(_msg_to_dict(msg))
            return messages

        return await self._run_client_call(_op)

    async def send_message(
        self,
        chat_id: int | str,
        text: str,
        *,
        reply_to_message_id: int | None = None,
        quote_text: str | None = None,
        parse_mode: str | None = None,
        disable_web_page_preview: bool = False,
    ) -> dict[str, Any]:
        """Отправляет текстовое сообщение через userbot Pyrogram session.

        Поддерживает userbot capabilities:
        - reply_to_message_id: Telegram отрисует как Reply на сообщение
        - quote_text: цитата фрагмента (Pyrogram quote_text param)
        - parse_mode: 'markdown' / 'html' / 'disabled' / None
        - disable_web_page_preview: отключить link preview
        """

        async def _op(client: Client) -> dict[str, Any]:
            kwargs: dict[str, Any] = {}
            if reply_to_message_id is not None:
                kwargs["reply_to_message_id"] = reply_to_message_id
            if quote_text:
                kwargs["quote_text"] = quote_text
            if parse_mode:
                kwargs["parse_mode"] = _resolve_parse_mode(parse_mode)
            if disable_web_page_preview:
                kwargs["disable_web_page_preview"] = True
            msg = await client.send_message(chat_id, text, **kwargs)
            return _msg_to_dict(msg)

        return await self._run_client_call(_op)

    async def download_media(
        self, chat_id: int | str, message_id: int
    ) -> str:
        """
        Скачивает медиафайл (фото / документ / голосовое) из сообщения.

        Возвращает абсолютный путь к скачанному файлу во временной директории.
        """
        async def _op(client: Client) -> str:
            msgs = await client.get_messages(chat_id, message_ids=message_id)
            if msgs is None:
                raise ValueError(f"Сообщение {message_id} не найдено в чате {chat_id}")
            msg: Message = msgs if not isinstance(msgs, list) else msgs[0]
            if not msg.media:
                raise ValueError(f"Сообщение {message_id} не содержит медиафайла")

            tmp_dir = Path(tempfile.gettempdir()) / "krab_mcp_media"
            tmp_dir.mkdir(parents=True, exist_ok=True)
            file_path = await client.download_media(msg, file_name=str(tmp_dir) + "/")
            return str(file_path)

        return await self._run_client_call(_op)

    async def get_voice_file(
        self, chat_id: int | str, message_id: int
    ) -> str:
        """
        Скачивает голосовое сообщение или audio и возвращает путь к файлу.
        Используется `telegram_transcribe_voice` для передачи в KrabEar.
        """
        return await self.download_media(chat_id, message_id)

    async def search(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        """Глобальный поиск по всем чатам Telegram."""
        async def _op(client: Client) -> list[dict[str, Any]]:
            results: list[dict[str, Any]] = []
            async for msg in client.search_global(query, limit=limit):
                results.append(_msg_to_dict(msg))
            return results

        return await self._run_client_call(_op)

    async def edit_message(
        self,
        chat_id: int | str,
        message_id: int,
        text: str,
        *,
        parse_mode: str | None = None,
        disable_web_page_preview: bool = False,
    ) -> dict[str, Any]:
        """Редактирует ранее отправленное сообщение (userbot)."""

        async def _op(client: Client) -> dict[str, Any]:
            kwargs: dict[str, Any] = {}
            if parse_mode:
                kwargs["parse_mode"] = _resolve_parse_mode(parse_mode)
            if disable_web_page_preview:
                kwargs["disable_web_page_preview"] = True
            msg = await client.edit_message_text(chat_id, message_id, text, **kwargs)
            return _msg_to_dict(msg)

        return await self._run_client_call(_op)

    async def send_photo(
        self,
        chat_id: int | str,
        photo: str,
        caption: str = "",
    ) -> dict[str, Any]:
        """Отправляет фото (локальный путь или URL) с опциональной подписью."""
        async def _op(client: Client) -> dict[str, Any]:
            msg = await client.send_photo(chat_id, photo, caption=caption or None)
            return _msg_to_dict(msg)

        return await self._run_client_call(_op)

    async def send_reaction(
        self,
        chat_id: int | str,
        message_id: int,
        emoji: str | list[str],
    ) -> dict[str, Any]:
        """Ставит реакцию на сообщение."""
        emojis = [emoji] if isinstance(emoji, str) else list(emoji)

        async def _op(client: Client) -> dict[str, Any]:
            # pyrofork send_reaction принимает emoji напрямую как str | list[str]
            await client.send_reaction(chat_id, message_id, emoji=emojis)
            return {"ok": True, "chat_id": str(chat_id), "message_id": message_id, "emoji": emojis}

        return await self._run_client_call(_op)

    async def forward_message(
        self,
        from_chat_id: int | str,
        message_id: int,
        to_chat_id: int | str,
    ) -> dict[str, Any]:
        """Пересылает сообщение из одного чата в другой."""
        async def _op(client: Client) -> dict[str, Any]:
            msgs = await client.forward_messages(to_chat_id, from_chat_id, message_id)
            forwarded = msgs[0] if isinstance(msgs, list) else msgs
            return _msg_to_dict(forwarded)

        return await self._run_client_call(_op)

    async def delete_messages(
        self,
        chat_id: int | str,
        message_ids: int | list[int],
    ) -> dict[str, Any]:
        """Удаляет одно или несколько сообщений."""
        ids = [message_ids] if isinstance(message_ids, int) else list(message_ids)

        async def _op(client: Client) -> dict[str, Any]:
            await client.delete_messages(chat_id, ids)
            return {"ok": True, "deleted": ids}

        return await self._run_client_call(_op)

    async def pin_message(
        self,
        chat_id: int | str,
        message_id: int,
        unpin: bool = False,
    ) -> dict[str, Any]:
        """Закрепляет или открепляет сообщение в чате."""
        async def _op(client: Client) -> dict[str, Any]:
            if unpin:
                await client.unpin_chat_message(chat_id, message_id)
                return {"ok": True, "action": "unpinned", "message_id": message_id}
            else:
                await client.pin_chat_message(chat_id, message_id)
                return {"ok": True, "action": "pinned", "message_id": message_id}

        return await self._run_client_call(_op)

    async def get_message(
        self,
        chat_id: int | str,
        message_id: int,
    ) -> dict[str, Any]:
        """Получает одно сообщение по ID."""
        async def _op(client: Client) -> dict[str, Any]:
            msgs = await client.get_messages(chat_id, message_ids=message_id)
            msg: Message = msgs if not isinstance(msgs, list) else msgs[0]
            if msg is None:
                raise ValueError(f"Сообщение {message_id} не найдено в чате {chat_id}")
            result = _msg_to_dict(msg)
            # Расширенные поля
            result["entities"] = (
                [{"type": str(e.type), "offset": e.offset, "length": e.length} for e in msg.entities]
                if msg.entities
                else []
            )
            return result

        return await self._run_client_call(_op)

    async def send_voice(
        self,
        chat_id: int | str,
        voice_path: str,
        duration: int | None = None,
    ) -> dict[str, Any]:
        """Отправляет голосовое сообщение (.ogg)."""
        async def _op(client: Client) -> dict[str, Any]:
            kwargs: dict = {}
            if duration is not None:
                kwargs["duration"] = duration
            msg = await client.send_voice(chat_id, voice_path, **kwargs)
            return _msg_to_dict(msg)

        return await self._run_client_call(_op)

    async def session_info_json(self) -> str:
        """Возвращает JSON с диагностической инфой о текущей session.

        Используется MCP tool ``telegram_session_info`` для проверки
        is_bot/is_user — критично для понимания userbot capabilities
        (бот не может писать в DM первым).
        """
        import json

        async def _op(client: Client) -> dict[str, Any]:
            me = await client.get_me()
            is_bot = bool(getattr(me, "is_bot", False))
            user_capabilities = [
                "send_message_to_dm_first (write to user_id without /start)",
                "reply_to_message_id (proper Reply UI)",
                "quote_text (cite a fragment)",
                "edit_message (own messages)",
                "send_reaction (emoji reactions)",
                "pin_message",
                "forward_message (no bot limits)",
                "search across all dialogs",
                "read message history of any joined chat",
            ]
            bot_capabilities = [
                "send_message ONLY если user уже сделал /start",
                "send_reaction (limited)",
                "no DM-first",
                "no proper user search",
            ]
            return {
                "ok": True,
                "is_bot": is_bot,
                "user_id": me.id,
                "username": me.username,
                "first_name": me.first_name,
                "session_name": _session_name(),
                "capabilities": bot_capabilities if is_bot else user_capabilities,
                "warning": (
                    "is_bot=True — этой session не хватает userbot capabilities. "
                    "Для активации userbot mode: удали "
                    "~/.krab_mcp_sessions/krab_mcp.session и запусти "
                    "./venv/bin/python mcp-servers/telegram/auth_setup.py "
                    "(потребуется phone+SMS)."
                ) if is_bot else None,
            }

        try:
            result = await self._run_client_call(_op)
        except Exception as exc:  # noqa: BLE001
            result = {"ok": False, "error": str(exc)[:200]}
        return json.dumps(result, ensure_ascii=False, indent=2)
