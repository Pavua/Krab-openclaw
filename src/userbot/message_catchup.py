# -*- coding: utf-8 -*-
"""Wave 46-A / Wave 48-A: MessageCatchupMixin — startup catch-up.

Зачем:
- Production bug (Session 43): после restart 22:12→22:21 Pyrogram
  updates_subscriber не auto-fetches missed events. 5 сообщений owner'а
  22:18-22:21 (msg 1325454-1325461 в chat 312322764) НЕ были ingested.
  Wave 39-D split_brain detection ловит downtime, но не past missed messages.
- Решение: после ``session=ready`` (logger.info("userbot_started", ...)),
  сразу poll ``get_chat_history`` для каждого target chat и сравнить с
  persistent ``_last_seen_message_id``. Unseen сообщения — manually
  через ``_process_message``.

Wave 48-A: catchup расширен на multiple chats (не только owner DM).
Если Krab restart происходит во время swarm session, swarm messages могут
быть lost — никто не реагирует, дальнейшие team interactions срываются.
Targets: owner DM + Krab Swarm group (configurable).

Persistent state:
- File: ``~/.openclaw/krab_runtime_state/last_seen_messages.json``
- Schema: ``{"<chat_id>": {"last_seen_msg_id": int, "updated_at_utc": "ISO"}}``
- Atomic write: tmp + os.replace.

Контракт:
- ``_load_last_seen() -> dict[int, int]`` — чтение state файла, fail-open {}.
- ``_save_last_seen(chat_id, msg_id)`` — atomic write, fail-warning.
- ``_catchup_chat_history(chat_id, *, max_lookback)`` — Wave 48-A:
  generic per-chat catchup; replay unseen messages.
- ``_catchup_owner_dm(*, max_lookback)`` — Wave 46-A wrapper для owner DM.
- ``_catchup_all_owner_chats()`` — iterate по всем target chats.
- ``_resolve_catchup_target_chats()`` — resolve target chat list (Wave 48-A).
- ``_record_seen_message(chat_id, msg_id)`` — hook для _process_message.

Defensive design:
- Catchup НЕ должен failить Krab startup. Любая ошибка → log warning + skip chat.
- Per-chat resilience: failure в одном chat не блокирует остальные.
- НЕ trigger live API в tests (всё через mocks).
- max_lookback default 20, override через env ``KRAB_STARTUP_CATCHUP_LIMIT``.
- Target chats: ``KRAB_STARTUP_CATCHUP_CHATS`` (CSV), иначе owner DM + swarm group.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from pyrogram import Client

logger = structlog.get_logger("Krab.userbot.message_catchup")

# Дефолтный путь — consistent с другими krab_runtime_state/*.json.
_DEFAULT_STATE_FILENAME = "last_seen_messages.json"

# Wave 52-G: history файл — JSONL append-only, FIFO max 100 entries.
_DEFAULT_HISTORY_FILENAME = "catchup_history.jsonl"
_CATCHUP_HISTORY_MAX_ENTRIES = 100


def _resolve_history_path() -> Path:
    """Путь к JSONL файлу истории catchup-сессий (Wave 52-G)."""
    base_dir = Path(
        os.environ.get("KRAB_RUNTIME_STATE_DIR")
        or str(Path.home() / ".openclaw" / "krab_runtime_state")
    ).expanduser()
    return base_dir / _DEFAULT_HISTORY_FILENAME


def _record_catchup_history(
    *,
    started_at: float,
    completed_at: float,
    target_count: int,
    per_chat_stats: list[dict[str, Any]],
) -> None:
    """Wave 52-G: persist одну запись о завершении catchup-сессии.

    Защищено от ошибок ФС: при любой OSError только warning, без raise.
    После append проверяет длину файла; если > _CATCHUP_HISTORY_MAX_ENTRIES —
    тримит до последних N (FIFO).
    """
    try:
        path = _resolve_history_path()
        path.parent.mkdir(parents=True, exist_ok=True)

        # Aggregate totals из per_chat_stats.
        total_caught = 0
        total_skipped = 0
        for s in per_chat_stats:
            try:
                total_caught += int(s.get("caught_up", 0) or 0)
                total_skipped += int(s.get("skipped_self", 0) or 0)
            except (TypeError, ValueError):
                continue

        entry: dict[str, Any] = {
            "started_at_utc": datetime.fromtimestamp(started_at, tz=timezone.utc).isoformat(),
            "completed_at_utc": datetime.fromtimestamp(completed_at, tz=timezone.utc).isoformat(),
            "duration_sec": round(max(0.0, completed_at - started_at), 3),
            "target_count": int(target_count),
            "total_caught_up": total_caught,
            "total_skipped_self": total_skipped,
            "by_chat": per_chat_stats,
        }

        line = json.dumps(entry, ensure_ascii=False)
        # Append + fsync.
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass

        # Trim FIFO до последних _CATCHUP_HISTORY_MAX_ENTRIES.
        try:
            with open(path, encoding="utf-8") as f:
                lines = f.readlines()
            if len(lines) > _CATCHUP_HISTORY_MAX_ENTRIES:
                tail = lines[-_CATCHUP_HISTORY_MAX_ENTRIES:]
                tmp_fd, tmp_path = tempfile.mkstemp(
                    prefix=".catchup_history_",
                    suffix=".jsonl.tmp",
                    dir=str(path.parent),
                )
                try:
                    with os.fdopen(tmp_fd, "w", encoding="utf-8") as tf:
                        tf.writelines(tail)
                    os.replace(tmp_path, path)
                except OSError:
                    try:
                        os.unlink(tmp_path)
                    except OSError:
                        pass
                    raise
        except OSError as exc:
            logger.warning("catchup_history_trim_failed", error=str(exc))
    except OSError as exc:
        logger.warning("catchup_history_record_failed", error=str(exc))
    except Exception as exc:  # noqa: BLE001
        # Не валим catchup, пишем warning.
        logger.warning(
            "catchup_history_record_unexpected",
            error=str(exc),
            error_type=type(exc).__name__,
        )


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


# Wave 48-A: дефолтный chat_id Krab Swarm forum-группы.
# Override через env ``KRAB_SWARM_GROUP_ID``.
_DEFAULT_SWARM_GROUP_ID = -1003703978531


def _resolve_swarm_group_id() -> int | None:
    """Resolve Krab Swarm group chat_id.

    Приоритет:
      1. ``KRAB_SWARM_GROUP_ID`` env.
      2. ``_DEFAULT_SWARM_GROUP_ID`` константа.
      3. None если env установлен в "" (явный disable).
    """
    raw = os.environ.get("KRAB_SWARM_GROUP_ID")
    if raw is None:
        return _DEFAULT_SWARM_GROUP_ID
    raw = raw.strip()
    if not raw:
        return None  # явный disable
    try:
        return int(raw)
    except ValueError:
        logger.warning("swarm_group_id_invalid", value=raw)
        return _DEFAULT_SWARM_GROUP_ID


def _parse_catchup_chats_env() -> list[int] | None:
    """Парсит ``KRAB_STARTUP_CATCHUP_CHATS`` (CSV chat_ids).

    Возвращает:
      - list[int] если env установлен и содержит хотя бы один valid id.
      - None если env пустой/не установлен (использовать defaults).

    Невалидные id silently skipped + warning logged.
    """
    raw = os.environ.get("KRAB_STARTUP_CATCHUP_CHATS", "").strip()
    if not raw:
        return None
    result: list[int] = []
    for token in raw.split(","):
        s = token.strip()
        if not s:
            continue
        try:
            result.append(int(s))
        except ValueError:
            logger.warning("catchup_chat_id_invalid", value=s)
            continue
    return result if result else None


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

    def _resolve_catchup_target_chats(self) -> list[int]:
        """Wave 48-A: список chat_id для startup catchup.

        Приоритет:
          1. ``KRAB_STARTUP_CATCHUP_CHATS`` env (CSV) — полная замена defaults.
          2. Defaults: owner DM + Krab Swarm group (если resolved).

        Дедупликация с сохранением порядка. Невалидные id из env уже
        отфильтрованы в ``_parse_catchup_chats_env`` (с warning).
        """
        env_override = _parse_catchup_chats_env()
        if env_override is not None:
            # Дедуп с сохранением порядка
            seen: set[int] = set()
            ordered: list[int] = []
            for cid in env_override:
                if cid not in seen:
                    seen.add(cid)
                    ordered.append(cid)
            return ordered

        candidates: list[int | None] = [
            self._resolve_catchup_owner_chat_id(),
            _resolve_swarm_group_id(),
        ]
        seen2: set[int] = set()
        result: list[int] = []
        for cid in candidates:
            if cid is None:
                continue
            if cid in seen2:
                continue
            seen2.add(cid)
            result.append(cid)
        return result

    async def _catchup_chat_history(
        self, chat_id: int, *, max_lookback: int | None = None
    ) -> dict[str, int]:
        """Wave 48-A: generic catch-up для одного chat'а.

        Возвращает структурированный результат:
          ``{"caught_up": int, "skipped_self": int, "history_size": int,
             "last_seen_before": int, "last_seen_after": int}``

        Алгоритм:
          1. ``client.get_chat_history(chat_id, limit=max_lookback)``.
          2. Filter messages с ``id > last_seen[chat_id]``.
          3. Для каждого unseen — ``await self._process_message(msg)``.
          4. Update ``last_seen[chat_id] = max(seen_ids)``.

        Все ошибки log + return zeros (Krab startup НЕ должен fail).
        """
        if max_lookback is None:
            max_lookback = _resolve_max_lookback()

        zero = {
            "caught_up": 0,
            "skipped_self": 0,
            "history_size": 0,
            "last_seen_before": 0,
            "last_seen_after": 0,
        }

        client = getattr(self, "client", None)
        if client is None:
            logger.warning("startup_catchup_skipped", chat_id=chat_id, reason="no_client")
            return zero

        try:
            last_seen_map = self._load_last_seen()
            last_seen_id = last_seen_map.get(int(chat_id), 0)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "startup_catchup_load_state_failed",
                chat_id=chat_id,
                error=str(exc),
            )
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
            return zero

        if not history:
            logger.info("startup_catchup_empty_history", chat_id=chat_id)
            return {
                "caught_up": 0,
                "skipped_self": 0,
                "history_size": 0,
                "last_seen_before": last_seen_id,
                "last_seen_after": last_seen_id,
            }

        # Фильтруем unseen: id > last_seen_id. Сортируем oldest→newest для
        # корректного порядка replay (FIFO).
        unseen = [m for m in history if (getattr(m, "id", 0) or 0) > last_seen_id]
        unseen.sort(key=lambda m: getattr(m, "id", 0) or 0)

        # Wave 46-C: track max_id даже для self-messages, чтобы state не
        # отставал и каждый restart не пытался reprocess их. Skip только
        # фактический dispatch (_process_message).
        replayed = 0
        skipped_self = 0
        max_id = last_seen_id
        for msg in unseen:
            mid = getattr(msg, "id", 0) or 0
            # Wave 46-C: skip own outgoing — Krab's own send.
            # Это критично: production bug 09.05 — catchup поднял Krab's own
            # inbox listing message, NLU classifier сматчил "команд" substring
            # и dispatched !swarm на самого себя.
            # Strict ``is True`` check — Pyrogram Message.outgoing всегда bool.
            outgoing_attr = getattr(msg, "outgoing", False)
            is_outgoing = outgoing_attr is True
            from_user = getattr(msg, "from_user", None)
            is_self = False
            if from_user is not None:
                self_attr = getattr(from_user, "is_self", False)
                is_self = self_attr is True
            if is_outgoing or is_self:
                if mid > max_id:
                    max_id = mid
                skipped_self += 1
                continue
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
            skipped_self=skipped_self,
            history_size=len(history),
            last_seen_before=last_seen_id,
            last_seen_after=max_id,
        )
        return {
            "caught_up": replayed,
            "skipped_self": skipped_self,
            "history_size": len(history),
            "last_seen_before": last_seen_id,
            "last_seen_after": max_id,
        }

    async def _catchup_owner_dm(self, *, max_lookback: int | None = None) -> int:
        """Wave 46-A wrapper: catch-up только owner DM. Возвращает кол-во replayed.

        Сохраняем как backward-compat для tests/callers, ожидающих int.
        Внутри делегирует в ``_catchup_chat_history``.
        """
        chat_id = self._resolve_catchup_owner_chat_id()
        if chat_id is None:
            logger.info("startup_catchup_skipped", reason="no_owner_chat_id")
            return 0
        stats = await self._catchup_chat_history(chat_id, max_lookback=max_lookback)
        return int(stats.get("caught_up", 0))

    async def _catchup_all_owner_chats(self) -> dict[int, int]:
        """Wave 48-A: catch-up для всех target chats. Возвращает {chat_id: caught_up}.

        Targets resolved через ``_resolve_catchup_target_chats``: env
        override ``KRAB_STARTUP_CATCHUP_CHATS`` либо defaults
        (owner DM + Krab Swarm group).

        Per-chat resilience: failure в одном chat НЕ блокирует остальные —
        ошибка логируется и переход к следующему target'у.

        Финальный structured log включает per-chat stats + totals.
        """
        result: dict[int, int] = {}
        per_chat_stats: list[dict[str, Any]] = []
        targets = self._resolve_catchup_target_chats()
        # Wave 52-G: фиксируем wall-clock начала.
        started_at = time.time()
        if not targets:
            logger.info("startup_catchup_skipped", reason="no_targets")
            # Wave 52-G: записываем даже пустую сессию (нулевые таргеты).
            _record_catchup_history(
                started_at=started_at,
                completed_at=time.time(),
                target_count=0,
                per_chat_stats=[],
            )
            return result

        total_caught_up = 0
        total_skipped_self = 0
        for chat_id in targets:
            try:
                stats = await self._catchup_chat_history(chat_id)
            except Exception as exc:  # noqa: BLE001
                # Per-chat resilience: один chat не валит остальные.
                logger.warning(
                    "startup_catchup_chat_failed",
                    chat_id=chat_id,
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
                # Wave 51-A: prometheus counter — alert при > 3/час.
                try:
                    from src.core.prometheus_metrics import record_startup_catchup_chat_failed

                    record_startup_catchup_chat_failed(chat_id=chat_id)
                except Exception:  # noqa: BLE001
                    pass
                per_chat_stats.append(
                    {
                        "chat_id": chat_id,
                        "caught_up": 0,
                        "skipped_self": 0,
                        "history_size": 0,
                        "error": str(exc),
                    }
                )
                result[chat_id] = 0
                continue
            caught = int(stats.get("caught_up", 0))
            skipped = int(stats.get("skipped_self", 0))
            history_size = int(stats.get("history_size", 0))
            total_caught_up += caught
            total_skipped_self += skipped
            result[chat_id] = caught
            per_chat_stats.append(
                {
                    "chat_id": chat_id,
                    "caught_up": caught,
                    "skipped_self": skipped,
                    "history_size": history_size,
                    "error": None,
                }
            )

        logger.info(
            "startup_catchup_complete_multi",
            chats=per_chat_stats,
            total_caught_up=total_caught_up,
            total_skipped_self=total_skipped_self,
            target_count=len(targets),
        )
        # Wave 52-G: persist history (defensive — никогда не прерывает catchup).
        _record_catchup_history(
            started_at=started_at,
            completed_at=time.time(),
            target_count=len(targets),
            per_chat_stats=per_chat_stats,
        )
        return result

    async def _run_startup_catchup_safe(self) -> None:
        """Точка вызова из startup hook. Полностью defensive — никогда не raise.

        Wave 46-A: вызывается из ``KraabUserbot.start`` сразу после
        ``logger.info("userbot_started", ...)``.
        Wave 48-A: теперь вызывает multi-chat ``_catchup_all_owner_chats``.
        """
        try:
            await self._catchup_all_owner_chats()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "startup_catchup_unexpected_failure",
                error=str(exc),
                error_type=type(exc).__name__,
            )
