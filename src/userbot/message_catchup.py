# -*- coding: utf-8 -*-
"""Wave 46-A: MessageCatchupMixin — startup catch-up для пропущенных сообщений.

Зачем:
- Production bug (Session 43): после restart 22:12→22:21 Pyrogram
  updates_subscriber не auto-fetches missed events. 5 сообщений owner'а
  22:18-22:21 (msg 1325454-1325461 в chat 312322764) НЕ были ingested.
  Wave 39-D split_brain detection ловит downtime, но не past missed messages.
- Решение: после ``session=ready`` (logger.info("userbot_started", ...)),
  сразу poll ``get_chat_history`` для owner DM и сравнить с
  persistent ``_last_seen_message_id``. Unseen сообщения — manually
  через ``_process_message``.

Persistent state:
- File: ``~/.openclaw/krab_runtime_state/last_seen_messages.json``
- Schema: ``{"<chat_id>": {"last_seen_msg_id": int, "updated_at_utc": "ISO"}}``
- Atomic write: tmp + os.replace.

Контракт:
- ``_load_last_seen() -> dict[int, int]`` — чтение state файла, fail-open {}.
- ``_save_last_seen(chat_id, msg_id)`` — atomic write, fail-warning.
- ``_catchup_owner_dm(*, max_lookback)`` — fetch history + replay unseen.
- ``_catchup_all_owner_chats()`` — iterate по всем owner-DM chats.
- ``_record_seen_message(chat_id, msg_id)`` — hook для _process_message.

Defensive design:
- Catchup НЕ должен failить Krab startup. Любая ошибка → log warning + return 0.
- НЕ trigger live API в tests (всё через mocks).
- max_lookback default 20, override через env ``KRAB_STARTUP_CATCHUP_LIMIT``.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from pyrogram import Client

logger = structlog.get_logger("Krab.userbot.message_catchup")

# Дефолтный путь — consistent с другими krab_runtime_state/*.json.
_DEFAULT_STATE_FILENAME = "last_seen_messages.json"


def _resolve_state_path() -> Path:
    """Получить путь к persistent state файлу.

    Уважает env ``KRAB_RUNTIME_STATE_DIR`` (тот же что использует bridge).
    """
    base_dir = Path(
        os.environ.get("KRAB_RUNTIME_STATE_DIR")
        or str(Path.home() / ".openclaw" / "krab_runtime_state")
    ).expanduser()
    return base_dir / _DEFAULT_STATE_FILENAME


def _resolve_max_lookback(default: int = 20) -> int:
    """Лимит лукапа в get_chat_history. Env override ``KRAB_STARTUP_CATCHUP_LIMIT``."""
    raw = os.environ.get("KRAB_STARTUP_CATCHUP_LIMIT", "").strip()
    if not raw:
        return default
    try:
        v = int(raw)
        return max(1, min(v, 200))  # safety clamp
    except ValueError:
        return default


def _resolve_owner_chat_id() -> int | None:
    """Resolve owner DM chat_id. Приоритет:

    1. ``OWNER_NOTIFY_CHAT_ID`` env (числовой) — основной источник.
    2. None — катчап будет skipped.

    Используется как fallback вне instance-контекста (например, из tests).
    """
    raw = os.environ.get("OWNER_NOTIFY_CHAT_ID", "").strip()
    if not raw:
        # config.OWNER_NOTIFY_CHAT_ID может быть подгружен динамически
        try:
            from ..config import config  # noqa: PLC0415

            raw = (getattr(config, "OWNER_NOTIFY_CHAT_ID", "") or "").strip()
        except Exception:  # noqa: BLE001
            raw = ""
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


class MessageCatchupMixin:
    """Mixin: startup catch-up + persistent last_seen_message_id."""

    # Ожидаемые атрибуты host-класса (KraabUserbot):
    client: "Client | None"
    me: object | None
    _owner_notify_target: int | str

    # ────────────────────────────────────────────────────────────────────
    # Persistent state I/O
    # ────────────────────────────────────────────────────────────────────

    def _last_seen_state_path(self) -> Path:
        """Путь к JSON state файлу (overridable через env)."""
        return _resolve_state_path()

    def _load_last_seen(self) -> dict[int, int]:
        """Прочитать state с диска. Возвращает {chat_id_int: last_seen_msg_id}.

        Fail-open: если файла нет / битый JSON / ошибка чтения — {}.
        """
        path = self._last_seen_state_path()
        if not path.exists():
            return {}
        try:
            raw = path.read_text(encoding="utf-8")
            data = json.loads(raw) if raw.strip() else {}
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning(
                "last_seen_load_failed",
                path=str(path),
                error=str(exc),
            )
            return {}
        result: dict[int, int] = {}
        if not isinstance(data, dict):
            return result
        for k, v in data.items():
            try:
                cid = int(k)
                if isinstance(v, dict):
                    msg_id = int(v.get("last_seen_msg_id", 0) or 0)
                else:
                    msg_id = int(v or 0)
                if msg_id > 0:
                    result[cid] = msg_id
            except (TypeError, ValueError):
                continue
        return result

    def _save_last_seen(self, chat_id: int, msg_id: int) -> None:
        """Atomic write обновления для одного чата.

        Стратегия: read-modify-write через tmp + ``os.replace``. Под нагрузкой
        горячего пути (каждое сообщение) это OK — файл небольшой (десятки чатов),
        write < 1ms на SSD.
        """
        if msg_id <= 0:
            return
        try:
            path = self._last_seen_state_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            current = self._load_last_seen()
            existing = current.get(int(chat_id), 0)
            if msg_id <= existing:
                return  # монотонно растущий id; не пишем
            current[int(chat_id)] = int(msg_id)
            payload: dict[str, Any] = {
                str(cid): {
                    "last_seen_msg_id": mid,
                    "updated_at_utc": datetime.now(timezone.utc).isoformat(),
                }
                for cid, mid in current.items()
            }
            # Atomic replace
            tmp_fd, tmp_path = tempfile.mkstemp(
                prefix=".last_seen_", suffix=".json.tmp", dir=str(path.parent)
            )
            try:
                with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                    json.dump(payload, f, ensure_ascii=False, indent=2)
                os.replace(tmp_path, path)
            except OSError:
                # cleanup tmp при ошибке
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
        except OSError as exc:
            logger.warning(
                "last_seen_save_failed",
                chat_id=chat_id,
                msg_id=msg_id,
                error=str(exc),
            )

    def _record_seen_message(self, chat_id: int | str, msg_id: int) -> None:
        """Hook вызывается из ``_process_message`` после успешной ingestion.

        Wrapper над ``_save_last_seen`` с защитой от bad input.
        """
        try:
            cid = int(chat_id)
            mid = int(msg_id)
        except (TypeError, ValueError):
            return
        if mid <= 0:
            return
        self._save_last_seen(cid, mid)

    # ────────────────────────────────────────────────────────────────────
    # Owner chat resolution
    # ────────────────────────────────────────────────────────────────────

    def _resolve_catchup_owner_chat_id(self) -> int | None:
        """Получить owner DM chat_id для catchup.

        Приоритет:
          1. ``self._owner_notify_target`` если int (из ``OWNER_NOTIFY_CHAT_ID``).
          2. ``OWNER_NOTIFY_CHAT_ID`` env через _resolve_owner_chat_id().
          3. None → catchup skipped.

        Не используем "me" (Saved Messages) — это userbot's own account, там
        owner-сообщения не появляются. Catchup нужен именно для DM от owner'а.
        """
        target = getattr(self, "_owner_notify_target", None)
        if isinstance(target, int):
            return target
        return _resolve_owner_chat_id()

    # ────────────────────────────────────────────────────────────────────
    # Main catchup entry-points
    # ────────────────────────────────────────────────────────────────────

    async def _catchup_owner_dm(self, *, max_lookback: int | None = None) -> int:
        """Catch-up для owner DM. Возвращает количество replayed messages.

        Алгоритм:
          1. Resolve owner_chat_id (None → 0).
          2. ``client.get_chat_history(chat_id, limit=max_lookback)``.
          3. Filter messages с ``id > last_seen[chat_id]``.
          4. Для каждого unseen — ``await self._process_message(msg)``.
          5. Update ``last_seen[chat_id] = max(seen_ids)``.

        Все ошибки log + return 0 (Krab startup НЕ должен fail).
        """
        if max_lookback is None:
            max_lookback = _resolve_max_lookback()

        chat_id = self._resolve_catchup_owner_chat_id()
        if chat_id is None:
            logger.info("startup_catchup_skipped", reason="no_owner_chat_id")
            return 0

        client = getattr(self, "client", None)
        if client is None:
            logger.warning("startup_catchup_skipped", reason="no_client")
            return 0

        try:
            last_seen_map = self._load_last_seen()
            last_seen_id = last_seen_map.get(int(chat_id), 0)
        except Exception as exc:  # noqa: BLE001
            logger.warning("startup_catchup_load_state_failed", error=str(exc))
            last_seen_id = 0

        # Собираем history; pyrogram возвращает async-iterator от newest к oldest.
        try:
            history: list[Any] = []
            async for msg in client.get_chat_history(chat_id, limit=max_lookback):
                history.append(msg)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "startup_catchup_fetch_failed",
                chat_id=chat_id,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return 0

        if not history:
            logger.info("startup_catchup_empty_history", chat_id=chat_id)
            return 0

        # Фильтруем unseen: id > last_seen_id. Сортируем oldest→newest для
        # корректного порядка replay (FIFO).
        unseen = [m for m in history if (getattr(m, "id", 0) or 0) > last_seen_id]
        unseen.sort(key=lambda m: getattr(m, "id", 0) or 0)

        replayed = 0
        max_id = last_seen_id
        for msg in unseen:
            mid = getattr(msg, "id", 0) or 0
            try:
                await self._process_message(msg)
                replayed += 1
                if mid > max_id:
                    max_id = mid
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "startup_catchup_replay_failed",
                    chat_id=chat_id,
                    msg_id=mid,
                    error=str(exc),
                )
                # Продолжаем — один битый msg не блокирует остальные.

        if max_id > last_seen_id:
            self._save_last_seen(int(chat_id), int(max_id))

        logger.info(
            "startup_catchup_complete",
            chat_id=chat_id,
            caught_up=replayed,
            history_size=len(history),
            last_seen_before=last_seen_id,
            last_seen_after=max_id,
        )
        return replayed

    async def _catchup_all_owner_chats(self) -> dict[int, int]:
        """Catch-up для всех owner-DM chats. Возвращает {chat_id: replayed}.

        В текущей реализации — только один owner DM (resolve через
        OWNER_NOTIFY_CHAT_ID). Метод оставлен для будущего расширения
        (Krab Swarm group, дополнительные allowed DMs).
        """
        result: dict[int, int] = {}
        chat_id = self._resolve_catchup_owner_chat_id()
        if chat_id is None:
            return result
        try:
            replayed = await self._catchup_owner_dm()
            result[chat_id] = replayed
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "startup_catchup_all_failed",
                chat_id=chat_id,
                error=str(exc),
            )
        return result

    async def _run_startup_catchup_safe(self) -> None:
        """Точка вызова из startup hook. Полностью defensive — никогда не raise.

        Wave 46-A: вызывается из ``KraabUserbot.start`` сразу после
        ``logger.info("userbot_started", ...)``.
        """
        try:
            await self._catchup_owner_dm()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "startup_catchup_unexpected_failure",
                error=str(exc),
                error_type=type(exc).__name__,
            )
