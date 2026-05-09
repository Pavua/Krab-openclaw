# -*- coding: utf-8 -*-
"""
!dreaming — управление OpenClaw Dreaming integration (Wave 44-N-cli).

Owner-only команда для просмотра статуса/diary и сервисных операций над
OpenClaw Dreaming feature (memory consolidation). Артефакты в
`~/.openclaw/workspace-main-messaging/memory/.dreams/`.

Subcommands:
    !dreaming               — alias for status
    !dreaming status        — текущий статус (enabled, last update, counts)
    !dreaming diary         — содержимое diary (truncated to 2000 chars)
    !dreaming repair        — repairDreamingArtifacts (archives state)
    !dreaming dedupe        — dedupeDreamDiary (safe)
    !dreaming backfill      — backfillDreamDiary (rebuild from events)
    !dreaming reset         — DESTRUCTIVE: требует `!dreaming reset confirm`
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pyrogram.types import Message

from ...core.access_control import AccessLevel
from ...core.exceptions import UserInputError
from ...core.logger import get_logger

if TYPE_CHECKING:
    from ...userbot_bridge import KraabUserbot

logger = get_logger(__name__)

_DIARY_TRUNCATE = 2000

_USAGE = (
    "🌙 **!dreaming** — OpenClaw Dreaming (owner)\n"
    "```\n"
    "!dreaming                — статус\n"
    "!dreaming status         — статус (enabled / last update / counts)\n"
    "!dreaming diary          — содержимое diary (первые 2000 символов)\n"
    "!dreaming repair         — repairDreamingArtifacts\n"
    "!dreaming dedupe         — dedupeDreamDiary (safe)\n"
    "!dreaming backfill       — backfillDreamDiary\n"
    "!dreaming reset confirm  — DESTRUCTIVE: reset diary\n"
    "```"
)


def _format_status(status: dict[str, Any]) -> str:
    """Telegram-friendly markdown статуса dreaming."""
    if not status:
        return "🌙 **Dreaming**: статус пуст (вероятно, фича не активирована)."
    enabled = status.get("enabled")
    icon = "✅" if enabled else "⭕"
    last_update = (
        status.get("last_diary_update")
        or status.get("lastDiaryUpdate")
        or status.get("last_update")
        or "—"
    )
    events = (
        status.get("events_count")
        or status.get("eventsCount")
        or status.get("recent_events")
        or "—"
    )
    short_term = (
        status.get("short_term_recall_size")
        or status.get("shortTermRecallSize")
        or status.get("recall_size")
        or "—"
    )
    diary_path = status.get("diary_path") or status.get("diaryPath") or "—"
    return (
        f"🌙 **Dreaming**\n"
        f"• Enabled: {icon} {'on' if enabled else 'off'}\n"
        f"• Last diary update: `{last_update}`\n"
        f"• Recent events: `{events}`\n"
        f"• Short-term recall: `{short_term}`\n"
        f"• Diary: `{diary_path}`"
    )


def _format_diary(payload: dict[str, Any]) -> str:
    """Diary preview — truncate до 2000 символов."""
    found = payload.get("found", True)
    path = payload.get("path", "—")
    content = payload.get("content") or ""
    if not found:
        return f"🌙 **Diary** не найден (`{path}`)."
    if not content:
        return f"🌙 **Diary** `{path}` пуст."
    truncated = content[:_DIARY_TRUNCATE]
    suffix = ""
    if len(content) > _DIARY_TRUNCATE:
        suffix = (
            f"\n\n_…обрезано ({len(content) - _DIARY_TRUNCATE} символов). Полный diary: `{path}`._"
        )
    return f"🌙 **Diary** `{path}`\n```\n{truncated}\n```{suffix}"


def _format_op_result(op: str, result: dict[str, Any]) -> str:
    """Универсальный форматтер результата сервисной операции."""
    ok = result.get("ok", True)
    icon = "✅" if ok else "❌"
    summary_keys = ("summary", "message", "details", "removed", "added", "archived")
    parts: list[str] = []
    for k in summary_keys:
        v = result.get(k)
        if v is not None:
            parts.append(f"• {k}: `{v}`")
    body = "\n".join(parts) if parts else "_(no details)_"
    return f"{icon} **dreaming.{op}** done\n{body}"


async def handle_dreaming(bot: "KraabUserbot", message: Message) -> None:
    """Owner-only !dreaming router."""
    access_profile = bot._get_access_profile(message.from_user)
    if access_profile.level != AccessLevel.OWNER:
        raise UserInputError(user_message="❌ Только owner.")

    raw = (message.text or "").strip()
    parts = raw.split()
    sub = parts[1].lower() if len(parts) >= 2 else "status"
    extra = parts[2].lower() if len(parts) >= 3 else ""

    # Lazy import — не тянем openclaw client без необходимости (и упрощает mocking).
    from ...openclaw_client import openclaw_client  # noqa: PLC0415

    try:
        if sub in ("", "status"):
            status = await openclaw_client.dreaming_status()
            await message.reply(_format_status(status))
            return

        if sub == "diary":
            payload = await openclaw_client.dream_diary()
            await message.reply(_format_diary(payload))
            return

        if sub == "repair":
            result = await openclaw_client.dreaming_repair()
            await message.reply(_format_op_result("repair", result))
            logger.info("dreaming_repair_invoked", chat_id=message.chat.id)
            return

        if sub == "dedupe":
            result = await openclaw_client.dream_diary_dedupe()
            await message.reply(_format_op_result("dedupe", result))
            logger.info("dreaming_dedupe_invoked", chat_id=message.chat.id)
            return

        if sub == "backfill":
            result = await openclaw_client.dream_diary_backfill()
            await message.reply(_format_op_result("backfill", result))
            logger.info("dreaming_backfill_invoked", chat_id=message.chat.id)
            return

        if sub == "reset":
            if extra != "confirm":
                raise UserInputError(
                    user_message=(
                        "⚠️ **DESTRUCTIVE**: `!dreaming reset` стирает diary.\n"
                        "Подтверди: `!dreaming reset confirm`"
                    )
                )
            result = await openclaw_client.dream_diary_reset()
            await message.reply(_format_op_result("reset", result))
            logger.warning(
                "dreaming_reset_invoked",
                chat_id=message.chat.id,
                user_id=getattr(message.from_user, "id", None),
            )
            return

        raise UserInputError(user_message=f"❌ Неизвестная subcommand `{sub}`.\n{_USAGE}")
    except UserInputError:
        raise
    except Exception as exc:
        logger.warning(
            "dreaming_command_failed",
            sub=sub,
            error=f"{type(exc).__name__}: {exc}",
        )
        raise UserInputError(
            user_message=f"❌ dreaming.{sub} failed: `{type(exc).__name__}: {exc}`"
        ) from exc


__all__ = ["handle_dreaming"]
