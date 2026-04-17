# -*- coding: utf-8 -*-
"""
nightly_summary.py — ежедневная сводка активности Krab для owner.

Data sources:
- archive.db: сообщений добавлено сегодня (memory_archive)
- cost_analytics: расходы за сегодня
- inbox_service: открытые attention items
- swarm_artifact_store: раунды свёрма за сегодня
- krab_scheduler: pending reminders

Delivery: DM владельцу в NIGHTLY_SUMMARY_HOUR (default 23, local time).
Паттерн: аналогичен WeeklyDigestService — деградирует тихо, логирует.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from typing import Any, Optional

from .logger import get_logger

logger = get_logger(__name__)

# Интервал проверки: каждую минуту (планировщик сам решает когда отправлять)
_CHECK_INTERVAL_SEC: int = 60
# Окно отправки: ±1 минута от целевого часа
_SEND_WINDOW_MIN: int = 1


def _now_local() -> datetime:
    """Текущее локальное время."""
    return datetime.now().astimezone()


def _today_utc_start() -> str:
    """ISO-строка начала сегодняшнего дня UTC (для фильтрации archive.db)."""
    now_utc = datetime.now(timezone.utc)
    return now_utc.replace(hour=0, minute=0, second=0, microsecond=0).isoformat(timespec="seconds")


def _today_unix_start() -> float:
    """Unix timestamp начала сегодняшнего дня UTC."""
    now_utc = datetime.now(timezone.utc)
    return now_utc.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


async def generate_summary() -> str:
    """Генерирует Markdown daily digest для owner."""
    now = _now_local()
    today_utc_start = _today_utc_start()
    today_ts = _today_unix_start()

    lines: list[str] = [
        f"🦀 **Krab Daily Digest** — {now:%Y-%m-%d}",
        "",
    ]

    # --- Archive: сообщений сегодня ---
    _append_archive_stats(lines, today_utc_start)

    # --- Swarm: раундов сегодня ---
    _append_swarm_stats(lines, today_ts)

    # --- Cost: расходы сегодня ---
    _append_cost_stats(lines, today_ts)

    # --- Inbox: открытые issues ---
    _append_inbox_stats(lines)

    # --- Pending reminders ---
    _append_reminder_stats(lines)

    return "\n".join(lines)


def _append_archive_stats(lines: list[str], today_utc_start: str) -> None:
    """Добавляет статистику memory archive."""
    try:
        import sqlite3
        from pathlib import Path

        db = Path("~/.openclaw/krab_memory/archive.db").expanduser()
        if not db.exists():
            return
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            total: int = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
            today_count: int = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE timestamp >= ?",
                (today_utc_start,),
            ).fetchone()[0]
            size_mb = db.stat().st_size / 1024 / 1024
        finally:
            conn.close()

        lines.append(f"**Memory Archive**: {total:,} msgs total ({size_mb:.1f} MB)")
        lines.append(f"• Сегодня: +{today_count} messages")
        lines.append("")
    except Exception as exc:  # noqa: BLE001
        logger.debug("nightly_summary_archive_error", error=str(exc))


def _append_swarm_stats(lines: list[str], today_ts: float) -> None:
    """Добавляет статистику swarm rounds за сегодня."""
    try:
        from .swarm_artifact_store import swarm_artifact_store

        all_artifacts = swarm_artifact_store.list_artifacts(limit=200)
        today_artifacts = [
            a for a in all_artifacts if float(a.get("timestamp", 0)) >= today_ts
        ]
        if today_artifacts:
            teams: dict[str, int] = {}
            for a in today_artifacts:
                t = str(a.get("team") or "unknown")
                teams[t] = teams.get(t, 0) + 1
            team_str = ", ".join(f"{t}: {n}" for t, n in sorted(teams.items()))
            lines.append(f"**Swarm**: {len(today_artifacts)} раундов сегодня ({team_str})")
            lines.append("")
    except Exception as exc:  # noqa: BLE001
        logger.debug("nightly_summary_swarm_error", error=str(exc))


def _append_cost_stats(lines: list[str], today_ts: float) -> None:
    """Добавляет FinOps статистику за сегодня."""
    try:
        from .cost_analytics import cost_analytics

        today_calls = [r for r in cost_analytics._calls if r.timestamp >= today_ts]
        if today_calls:
            cost_usd = sum(r.cost_usd for r in today_calls)
            tokens = sum(r.input_tokens + r.output_tokens for r in today_calls)
            lines.append(
                f"**FinOps**: ${cost_usd:.4f} сегодня"
                f" ({len(today_calls)} вызовов, {tokens:,} токенов)"
            )
            lines.append("")
    except Exception as exc:  # noqa: BLE001
        logger.debug("nightly_summary_cost_error", error=str(exc))


def _append_inbox_stats(lines: list[str]) -> None:
    """Добавляет сводку открытых issues из inbox."""
    try:
        from .inbox_service import inbox_service

        open_items = inbox_service.list_items(status="open", limit=100)
        errors = [it for it in open_items if it.get("severity") == "error"]
        warnings = [it for it in open_items if it.get("severity") == "warning"]
        if errors or warnings:
            lines.append(
                f"**Inbox**: {len(errors)} ошибок, {len(warnings)} предупреждений"
            )
            # Показываем последние 3 error
            for it in errors[-3:]:
                lines.append(f"  • ❌ {str(it.get('title', ''))[:60]}")
            lines.append("")
    except Exception as exc:  # noqa: BLE001
        logger.debug("nightly_summary_inbox_error", error=str(exc))


def _append_reminder_stats(lines: list[str]) -> None:
    """Добавляет информацию о pending reminders."""
    try:
        from .scheduler import krab_scheduler

        pending = krab_scheduler.list_reminders()
        if pending:
            lines.append(f"**Reminders**: {len(pending)} pending")
    except Exception as exc:  # noqa: BLE001
        logger.debug("nightly_summary_reminders_error", error=str(exc))


async def send_nightly_summary(
    bot: Any,
    owner_chat_id: Optional[str] = None,
) -> bool:
    """
    Генерирует и отправляет daily digest owner в DM.
    Вызывается из nightly_summary_loop по расписанию.
    """
    try:
        summary = await generate_summary()

        if not owner_chat_id:
            from .. import config

            ids = getattr(config, "OWNER_USER_IDS", [])
            if not ids:
                logger.warning("nightly_summary_no_owner")
                return False
            owner_chat_id = str(ids[0])

        chat_id: Any = int(owner_chat_id) if str(owner_chat_id).lstrip("-").isdigit() else owner_chat_id
        await bot.send_message(chat_id, summary, parse_mode="markdown")
        logger.info("nightly_summary_sent", chat_id=owner_chat_id)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.error("nightly_summary_send_failed", error=str(exc))
        return False


class NightlySummaryService:
    """
    Сервис ночной сводки.

    Запускается как asyncio task; каждую минуту проверяет время,
    в настроенный час (NIGHTLY_SUMMARY_HOUR, default 23) отправляет digest.
    """

    def __init__(self) -> None:
        self._sent_today: Optional[str] = None  # дата последней отправки "YYYY-MM-DD"
        self._bot: Any = None

    def bind_bot(self, bot: Any) -> None:
        """Привязывает Pyrogram клиент для отправки."""
        self._bot = bot

    async def _loop(self) -> None:
        """Фоновый loop: каждую минуту проверяет, пора ли слать digest."""
        target_hour = int(os.environ.get("NIGHTLY_SUMMARY_HOUR", "23"))
        while True:
            try:
                await asyncio.sleep(_CHECK_INTERVAL_SEC)
                now = _now_local()
                date_str = now.strftime("%Y-%m-%d")
                # Отправляем один раз в сутки в нужный час
                if (
                    now.hour == target_hour
                    and now.minute < _SEND_WINDOW_MIN
                    and self._sent_today != date_str
                ):
                    self._sent_today = date_str
                    if self._bot is not None:
                        await send_nightly_summary(self._bot)
                    else:
                        logger.warning("nightly_summary_bot_not_bound")
            except asyncio.CancelledError:
                return
            except Exception as exc:  # noqa: BLE001
                logger.warning("nightly_summary_loop_error", error=str(exc))

    def start(self) -> "asyncio.Task[None]":
        """Запускает фоновую задачу и возвращает Task."""
        task = asyncio.get_event_loop().create_task(
            self._loop(), name="krab_nightly_summary"
        )
        logger.info(
            "nightly_summary_loop_started",
            hour=int(os.environ.get("NIGHTLY_SUMMARY_HOUR", "23")),
        )
        return task


# Синглтон
nightly_summary_service = NightlySummaryService()

__all__ = [
    "NightlySummaryService",
    "nightly_summary_service",
    "generate_summary",
    "send_nightly_summary",
]
