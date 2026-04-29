# -*- coding: utf-8 -*-
"""
memory_admin_commands — Phase 2 Wave 18 extraction (Session 28).

Команды администрирования Memory Layer:
  !memory  — recent / stats / clear / rebuild

Re-exported из command_handlers.py для обратной совместимости (тесты,
external imports `from src.handlers.command_handlers import handle_memory`).

Использует dual-namespace lookup pattern: тесты могут патчить
`command_handlers._handle_memory_stats` и оно подхватится через `_ch_attr()`.
"""

from __future__ import annotations

import asyncio
import datetime
import pathlib
import sys
import time
from typing import TYPE_CHECKING, Any

from pyrogram.types import Message

from ...core.exceptions import UserInputError
from ...core.logger import get_logger
from ...core.openclaw_workspace import (
    list_workspace_memory_entries as _list_workspace_memory_entries_baseline,
)

if TYPE_CHECKING:
    from ...userbot_bridge import KraabUserbot

_logger_baseline = get_logger(__name__)


class _LoggerProxy:
    """Прокси к command_handlers.logger (тесты патчат его), fallback к локальному."""

    def __getattr__(self, item: str) -> Any:
        from .. import command_handlers as _ch  # noqa: PLC0415

        target = getattr(_ch, "logger", _logger_baseline)
        return getattr(target, item)


logger = _LoggerProxy()


def _ch_attr(name: str, default: Any) -> Any:
    """Dual-namespace lookup: command_handlers namespace first, fallback на default."""
    from .. import command_handlers as _ch  # noqa: PLC0415

    return getattr(_ch, name, default)


_ARCHIVE_DB_PATH_FOR_CLEAR = pathlib.Path.home() / ".openclaw" / "krab_memory" / "archive.db"
_REPAIR_SCRIPT_RELPATH = pathlib.Path("scripts") / "repair_sqlite_vec.py"
_MEMORY_REBUILD_TIMEOUT = 60.0


async def handle_memory(bot: "KraabUserbot", message: Message) -> None:
    """
    !memory recent / stats / clear / rebuild — администрирование Memory Layer.
    """
    raw_args = str(message.text or "").split(maxsplit=2)
    action = raw_args[1].strip().lower() if len(raw_args) > 1 else "recent"
    rest = raw_args[2].strip() if len(raw_args) > 2 else ""

    if action == "stats":
        del bot
        await _ch_attr("_handle_memory_stats", _handle_memory_stats)(message)
        return

    if action == "clear":
        me_id = getattr(getattr(bot, "me", None), "id", None)
        sender_id = getattr(getattr(message, "from_user", None), "id", None)
        if me_id is None or sender_id != me_id:
            await message.reply("\U0001f6ab `!memory clear` доступен только владельцу.")
            return
        await _ch_attr("_handle_memory_clear", _handle_memory_clear)(message, rest)
        return

    if action == "rebuild":
        me_id = getattr(getattr(bot, "me", None), "id", None)
        sender_id = getattr(getattr(message, "from_user", None), "id", None)
        if me_id is None or sender_id != me_id:
            await message.reply("\U0001f6ab `!memory rebuild` доступен только владельцу.")
            return
        del bot
        await _ch_attr("_handle_memory_rebuild", _handle_memory_rebuild)(message)
        return

    del bot
    source_filter = rest

    if action != "recent":
        raise UserInputError(
            user_message=(
                "\U0001f9e0 Формат: `!memory recent [source_filter]` | `!memory stats`"
                " | `!memory clear` | `!memory rebuild`"
            )
        )

    list_entries = _ch_attr(
        "list_workspace_memory_entries", _list_workspace_memory_entries_baseline
    )
    rows = list_entries(limit=8, source_filter=source_filter)
    if not rows:
        await message.reply("\U0001f9e0 В общей памяти пока нет подходящих записей.")
        return
    lines = ["\U0001f9e0 **Последние записи общей памяти**"]
    for item in rows:
        author_suffix = f":{item['author']}" if item.get("author") else ""
        lines.append(
            f"- `{item['date']} {item['time']}` [{item['source']}{author_suffix}] {item['text']}"
        )
    await message.reply("\n".join(lines))


async def _handle_memory_stats(message: Message) -> None:
    """Собирает архив/индексер/валидатор статистику и отправляет reply."""
    archive_stats = _ch_attr("_collect_memory_archive_stats", _collect_memory_archive_stats)()
    indexer_stats = _ch_attr("_collect_memory_indexer_stats", _collect_memory_indexer_stats)()
    validator_stats = _ch_attr("_collect_memory_validator_stats", _collect_memory_validator_stats)()
    formatter = _ch_attr("format_memory_stats", format_memory_stats)
    reply = formatter(archive_stats, indexer_stats, validator_stats)
    await message.reply(reply)


async def _handle_memory_clear(message: Message, args_str: str) -> None:
    """Selective cleanup archive.db — по чату или по дате."""
    import re
    from datetime import datetime

    from ...core.reset_helpers import (
        clear_archive_db_for_chat,
        delete_archive_messages_before,
        list_archive_chats,
    )

    db_path = _ch_attr("_ARCHIVE_DB_PATH_FOR_CLEAR", _ARCHIVE_DB_PATH_FOR_CLEAR)
    if not db_path.exists():
        await message.reply("\U0001f4ed Archive не существует — нечего удалять.")
        return

    chat_match = re.search(r"--chat=(\S+)", args_str)
    before_match = re.search(r"--before=(\d{4}-\d{2}-\d{2})", args_str)
    confirm = "--confirm" in args_str

    if not chat_match and not before_match:
        chats = list_archive_chats(db_path=db_path, limit=20)
        try:
            import sqlite3 as _sq3

            with _sq3.connect(f"file:{db_path}?mode=ro", uri=True) as _c:
                total = _c.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        except Exception:  # noqa: BLE001
            total = sum(r["message_count"] for r in chats)

        lines = [f"\U0001f9e0 **Archive preview** — {_fmt_int_ru(total)} total messages\n"]
        if chats:
            lines.append("**Топ чатов:**")
            for row in chats:
                title_s = (row["title"] or f"chat_{row['chat_id']}")[:40]
                lines.append(
                    f"• `{row['chat_id']}` → {title_s}: {_fmt_int_ru(row['message_count'])} msgs"
                )
        else:
            lines.append("_(чаты не найдены)_")
        lines.append("\n**Использование:**")
        lines.append("• `!memory clear --chat=<id> --confirm`")
        lines.append("• `!memory clear --before=YYYY-MM-DD --confirm`")
        await message.reply("\n".join(lines))
        return

    if not confirm:
        if chat_match:
            chat_id = chat_match.group(1)
            from ...core.reset_helpers import count_archive_messages_for_chat

            count = count_archive_messages_for_chat(chat_id, db_path=db_path)
            await message.reply(
                f"⚠️ Будет удалено **{_fmt_int_ru(count)}** сообщений для чата `{chat_id}`.\n"
                f"Добавьте `--confirm` для подтверждения."
            )
        else:
            date_str = before_match.group(1)  # type: ignore[union-attr]
            try:
                cutoff_ts = int(datetime.strptime(date_str, "%Y-%m-%d").timestamp())
            except ValueError:
                await message.reply(f"❌ Неверный формат даты: `{date_str}`. Ожидается YYYY-MM-DD.")
                return
            import sqlite3 as _sq3

            try:
                with _sq3.connect(f"file:{db_path}?mode=ro", uri=True) as _c:
                    count = _c.execute(
                        "SELECT COUNT(*) FROM messages WHERE date < ?", (cutoff_ts,)
                    ).fetchone()[0]
            except Exception:  # noqa: BLE001
                count = 0
            await message.reply(
                f"⚠️ Будет удалено **{_fmt_int_ru(count)}** сообщений старше `{date_str}`.\n"
                f"Добавьте `--confirm` для подтверждения."
            )
        return

    try:
        if chat_match:
            chat_id = chat_match.group(1)
            deleted = clear_archive_db_for_chat(chat_id, db_path=db_path)
            await message.reply(
                f"\U0001f5d1️ Удалено **{_fmt_int_ru(deleted)}** сообщений чата `{chat_id}` из archive.db.\n"
                f"_(chunks + chunk_messages тоже очищены)_"
            )
        else:
            date_str = before_match.group(1)  # type: ignore[union-attr]
            try:
                cutoff_ts = int(datetime.strptime(date_str, "%Y-%m-%d").timestamp())
            except ValueError:
                await message.reply(f"❌ Неверный формат даты: `{date_str}`. Ожидается YYYY-MM-DD.")
                return
            deleted = delete_archive_messages_before(cutoff_ts, db_path=db_path)
            await message.reply(
                f"\U0001f5d1️ Удалено **{_fmt_int_ru(deleted)}** сообщений старше `{date_str}` из archive.db.\n"
                f"_(осиротевшие chunks тоже очищены)_"
            )
    except Exception as exc:  # noqa: BLE001
        await message.reply(f"❌ Ошибка при очистке archive: {exc}")


async def _handle_memory_rebuild(message: Message) -> None:
    """Запускает repair_sqlite_vec.py в фоне и возвращает результат в reply."""
    from ...core.subprocess_env import clean_subprocess_env  # noqa: PLC0415

    krab_root = pathlib.Path.home() / "Antigravity_AGENTS" / "Краб"
    script_path = krab_root / _REPAIR_SCRIPT_RELPATH

    if not script_path.exists():
        await message.reply(f"❌ Script not found: {_REPAIR_SCRIPT_RELPATH}")
        return

    await message.reply(
        "\U0001f504 Запускаю repair sqlite-vec... (~20s)\n"
        "⚠️ На время repair retrieval memory может быть нестабилен."
    )

    t0 = time.monotonic()
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            str(script_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(krab_root),
            env=clean_subprocess_env(),
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=_MEMORY_REBUILD_TIMEOUT)
        except asyncio.TimeoutError:
            if proc.returncode is None:
                try:
                    proc.terminate()
                except ProcessLookupError:
                    pass
                else:
                    try:
                        await asyncio.wait_for(proc.wait(), timeout=2.0)
                    except asyncio.TimeoutError:
                        try:
                            proc.kill()
                        except ProcessLookupError:
                            pass
                        try:
                            await asyncio.wait_for(proc.wait(), timeout=1.0)
                        except asyncio.TimeoutError:
                            logger.warning(
                                "openclaw_cli_force_killed_but_no_reap",
                                pid=proc.pid,
                            )
            elapsed = time.monotonic() - t0
            await message.reply(f"⚠️ Repair timeout после {elapsed:.0f}s. Проверь лог вручную.")
            logger.warning("memory_rebuild_timeout", elapsed=elapsed)
            return
    except Exception as exc:  # noqa: BLE001
        import traceback as _tb

        logger.error(
            "memory_rebuild_launch_error",
            error=str(exc),
            error_type=type(exc).__name__,
            traceback=_tb.format_exc(),
        )
        await message.reply(f"❌ Ошибка запуска repair: {exc}")
        return

    elapsed = time.monotonic() - t0
    output = stdout.decode("utf-8", errors="replace").strip()
    if len(output) > 2000:
        output = "..." + output[-2000:]

    if proc.returncode == 0:
        summary_line = ""
        for line in output.splitlines():
            if "[DONE]" in line or "[OK]" in line:
                summary_line = line.strip()
                break
        tail = f"\n`{summary_line}`" if summary_line else ""
        snippet = output[-800:]
        await message.reply(f"✅ Repair done за {elapsed:.1f}s.{tail}\n\n```\n{snippet}\n```")
        logger.info("memory_rebuild_done", elapsed=elapsed, returncode=0)
    else:
        snippet = output[-800:]
        await message.reply(
            f"❌ Repair завершился с кодом {proc.returncode} за {elapsed:.1f}s.\n"
            f"```\n{snippet}\n```"
        )
        logger.error(
            "memory_rebuild_failed",
            returncode=proc.returncode,
            elapsed=elapsed,
        )


def _collect_memory_archive_stats() -> dict[str, Any]:
    """Read-only снимок archive.db: counts + size. Graceful fallback."""
    import sqlite3 as _sqlite3
    from pathlib import Path as _Path

    db_path = _Path("~/.openclaw/krab_memory/archive.db").expanduser()
    stats: dict[str, Any] = {"exists": db_path.exists()}
    if not stats["exists"]:
        return stats
    try:
        conn = _sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            stats["messages"] = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
            stats["chats"] = conn.execute("SELECT COUNT(*) FROM chats").fetchone()[0]
            stats["chunks"] = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        finally:
            conn.close()
        stats["size_mb"] = db_path.stat().st_size / 1024 / 1024
    except Exception as exc:  # noqa: BLE001
        stats["error"] = str(exc)
    return stats


def _collect_memory_indexer_stats() -> dict[str, Any]:
    """Снимок состояния real-time индексера; при недоступности — заглушка."""
    try:
        from ...core.memory_indexer_worker import get_indexer  # noqa: PLC0415

        snap = get_indexer().get_stats()
        return {
            "state": "running" if getattr(snap, "is_running", False) else "stopped",
            "queue_size": getattr(snap, "queue_size", 0),
            "queue_maxsize": getattr(snap, "queue_maxsize", 0),
            "processed_total": getattr(snap, "processed_total", 0),
            "failed": dict(getattr(snap, "failed", {}) or {}),
        }
    except Exception:  # noqa: BLE001
        return {"state": "unavailable"}


def _collect_memory_validator_stats() -> dict[str, Any]:
    """Снимок счётчиков memory_validator; при отсутствии модуля — заглушка."""
    try:
        from ...core.memory_validator import memory_validator  # noqa: PLC0415

        stats = dict(getattr(memory_validator, "stats", {}) or {})
        try:
            stats["pending_count"] = len(memory_validator.list_pending())
        except Exception:  # noqa: BLE001
            stats.setdefault("pending_count", 0)
        return stats
    except Exception:  # noqa: BLE001
        return {"error": "not loaded"}


def _fmt_int_ru(value: int) -> str:
    """Целое с пробелом-разделителем тысяч (RU-стиль)."""
    return f"{int(value):,}".replace(",", " ")


def format_memory_stats(
    archive: dict[str, Any],
    indexer: dict[str, Any],
    validator: dict[str, Any],
) -> str:
    """Формирует Markdown-сообщение с агрегатом статистики Memory Layer."""
    lines: list[str] = ["\U0001f9e0 **Memory Layer Stats**", ""]

    lines.append("**Archive.db:**")
    if archive.get("exists"):
        if "error" in archive:
            lines.append(f"• Error: {archive['error']}")
        else:
            lines.append(f"• Messages: **{_fmt_int_ru(archive.get('messages', 0))}**")
            lines.append(f"• Chats: {archive.get('chats', 0)}")
            lines.append(f"• Chunks: {_fmt_int_ru(archive.get('chunks', 0))}")
            size_mb = archive.get("size_mb", 0)
            lines.append(f"• Size: {size_mb:.1f} MB")
    else:
        lines.append("• Not initialized")

    lines.append("")
    lines.append("**Indexer:**")
    state = indexer.get("state", "unknown")
    lines.append(f"• State: `{state}`")
    if state != "unavailable":
        q_size = indexer.get("queue_size", 0)
        q_max = indexer.get("queue_maxsize") or 0
        if q_max:
            lines.append(f"• Queue: {q_size} / {q_max}")
        else:
            lines.append(f"• Queue: {q_size}")
        lines.append(f"• Processed: {_fmt_int_ru(indexer.get('processed_total', 0))}")
        failed = indexer.get("failed") or {}
        if failed:
            parts = ", ".join(f"{k}={v}" for k, v in failed.items())
            lines.append(f"• Failed: {parts}")

    lines.append("")
    lines.append("**Validator:**")
    if "error" in validator:
        lines.append(f"• {validator['error']}")
    else:
        lines.append(f"• Safe: {_fmt_int_ru(validator.get('safe_total', 0))}")
        lines.append(f"• Blocked: {validator.get('injection_blocked_total', 0)}")
        lines.append(f"• Confirmed: {validator.get('confirmed_total', 0)}")
        lines.append(f"• Pending: {validator.get('pending_count', 0)}")

    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    lines.append("")
    lines.append(f"_Последнее обновление: {ts}_")

    return "\n".join(lines)
