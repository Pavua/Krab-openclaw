# -*- coding: utf-8 -*-
"""
NLU command gate (Wave 44-O-nlu-wire) — pre-LLM dispatch владельческих
natural-language фраз в !command.

Алгоритм:
    1. Только owner DM (is_self=True + private chat).
    2. extract_command_intent(text) — keyword templates → CommandIntent.
    3. confidence ≥ 0.8 + non-destructive → dispatch handler напрямую,
       подменив message.text на rendered "!cmd ...".
    4. 0.5 ≤ confidence < 0.8 → reply с подтверждением, store pending.
    5. < 0.5 / None → fall through.

Feature gate: KRAB_NLU_INTENT_DISPATCH_ENABLED=1 (default OFF).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ..core.command_intent_extractor import CommandIntent, extract_command_intent
from ..core.logger import get_logger

if TYPE_CHECKING:
    from pyrogram.types import Message

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Pending-confirmation state (per-chat, ephemeral, in-memory)
# ---------------------------------------------------------------------------

_PENDING: dict[str, "PendingIntent"] = {}
_PENDING_TTL_SEC = 120.0


@dataclass
class PendingIntent:
    chat_id: str
    intent: CommandIntent
    created_ts: float


def _now() -> float:
    import time

    return time.time()


def _confirm_yes(text: str) -> bool:
    t = (text or "").strip().lower()
    return t in {"да", "ага", "yes", "y", "ok", "ок", "подтверждаю", "+", "давай"}


def _confirm_no(text: str) -> bool:
    t = (text or "").strip().lower()
    return t in {"нет", "no", "n", "отмена", "cancel", "-", "не надо"}


# ---------------------------------------------------------------------------
# Feature flag
# ---------------------------------------------------------------------------


def is_enabled() -> bool:
    """KRAB_NLU_INTENT_DISPATCH_ENABLED env flag, default OFF."""
    return os.environ.get("KRAB_NLU_INTENT_DISPATCH_ENABLED", "0").strip() in (
        "1",
        "true",
        "TRUE",
        "yes",
    )


# ---------------------------------------------------------------------------
# Owner DM detection
# ---------------------------------------------------------------------------


def _is_owner_dm(message: "Message", *, is_self: bool) -> bool:
    """Owner DM: message от OWNER access-level + private chat.

    Wave 44-O-nlu-fix (2026-05-09): первоначальная проверка `is_self=True`
    была неверна. is_self=True означает "msg от самого Krab account'а
    (yung_nagato)" — это случай когда Krab пишет себе/relay. Реальный
    owner — это p0lrd, у которого AccessLevel.OWNER, а is_self=False.
    Правильная проверка: from_user.id в OWNER allowlist (через
    src.core.access_control.OWNER_USER_IDS) AND chat type private.
    """
    try:
        from pyrogram import enums  # noqa: PLC0415

        chat = getattr(message, "chat", None)
        ctype = getattr(chat, "type", None)
        if ctype not in (enums.ChatType.PRIVATE, enums.ChatType.BOT):
            return False

        # Owner check — is_self OR from_user is in OWNER allowlist.
        if is_self:
            return True

        from_user = getattr(message, "from_user", None)
        from_user_id = getattr(from_user, "id", None)
        if from_user_id is None:
            return False

        # Try config.OWNER_USER_IDS. Fallback: 312322764 (p0lrd).
        try:
            from ..config import config as _config  # noqa: PLC0415

            raw = list(getattr(_config, "OWNER_USER_IDS", []) or [])
            # raw может содержать str/int, плюс username — берём только int IDs.
            owner_ids: set[int] = set()
            for item in raw:
                try:
                    owner_ids.add(int(item))
                except (TypeError, ValueError):
                    pass
            if not owner_ids:
                owner_ids = {312322764}
        except Exception:  # noqa: BLE001
            owner_ids = {312322764}
        return int(from_user_id) in owner_ids
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------------------
# Command dispatch — map !cmd → handler
# ---------------------------------------------------------------------------


async def _dispatch_handler(bot: Any, message: "Message", intent: CommandIntent) -> bool:
    """Dispatch handler for resolved intent. Returns True on success."""
    cmd = intent.command.lstrip("!").lower()

    # Lazy imports — minimise startup cost + ease of test monkeypatch.
    handler = None
    try:
        if cmd == "swarm":
            from ..handlers.commands.swarm_commands import handle_swarm

            handler = handle_swarm
        elif cmd == "status":
            from ..handlers.commands.system_commands import handle_status

            handler = handle_status
        elif cmd == "quota":
            from ..handlers.commands.observability_commands import handle_quota

            handler = handle_quota
        elif cmd == "proactive":
            from ..handlers.commands.proactive import handle_proactive

            handler = handle_proactive
        elif cmd == "memory":
            from ..handlers.commands.memory_admin_commands import handle_memory

            handler = handle_memory
        elif cmd == "cron":
            from ..handlers.commands.scheduler_commands import handle_cron

            handler = handle_cron
    except Exception as exc:  # noqa: BLE001
        logger.warning("nlu_dispatch_import_failed", cmd=cmd, error=str(exc))
        return False

    if handler is None:
        logger.info("nlu_dispatch_unknown_command", cmd=cmd)
        return False

    # Synthesize message.text = rendered "!cmd ..." and dispatch.
    # Guard rendered: must start with "!".
    rendered = (intent.rendered or intent.command).strip()
    if not rendered.startswith("!"):
        rendered = f"{intent.command} {rendered}".strip()

    original_text = getattr(message, "text", None)
    try:
        try:
            object.__setattr__(message, "text", rendered)
        except Exception:  # noqa: BLE001
            try:
                message.text = rendered  # type: ignore[misc]
            except Exception:  # noqa: BLE001
                pass
        await handler(bot, message)
        return True
    finally:
        # Restore — даже если handler упал.
        try:
            object.__setattr__(message, "text", original_text)
        except Exception:  # noqa: BLE001
            try:
                message.text = original_text  # type: ignore[misc]
            except Exception:  # noqa: BLE001
                pass


# ---------------------------------------------------------------------------
# Pending state utilities (для тестов и confirmation flow)
# ---------------------------------------------------------------------------


def _gc_pending() -> None:
    now = _now()
    expired = [k for k, v in _PENDING.items() if (now - v.created_ts) > _PENDING_TTL_SEC]
    for k in expired:
        _PENDING.pop(k, None)


def get_pending(chat_id: str) -> PendingIntent | None:
    _gc_pending()
    return _PENDING.get(str(chat_id))


def clear_pending(chat_id: str) -> None:
    _PENDING.pop(str(chat_id), None)


def _store_pending(chat_id: str, intent: CommandIntent) -> None:
    _PENDING[str(chat_id)] = PendingIntent(
        chat_id=str(chat_id),
        intent=intent,
        created_ts=_now(),
    )


# ---------------------------------------------------------------------------
# Public gate API
# ---------------------------------------------------------------------------


async def try_nlu_command_dispatch(
    bot: Any,
    message: "Message",
    *,
    query: str,
    chat_id: str,
    is_self: bool,
) -> bool:
    """Pre-LLM gate: попытаться превратить NL-фразу в !command.

    Returns:
        True если intent был обработан (dispatched ИЛИ pending confirmation
        ИЛИ negative confirmation cleared) — caller должен пропустить LLM call.
        False если fall-through к стандартному LLM flow.
    """
    if not is_enabled():
        return False

    if not _is_owner_dm(message, is_self=is_self):
        logger.debug("nlu_gate_skipped_not_owner_dm", chat_id=chat_id)
        return False

    text = (query or "").strip()
    if not text:
        return False

    # ------ Pending confirmation flow ------
    pending = get_pending(chat_id)
    if pending is not None:
        if _confirm_yes(text):
            logger.info(
                "nlu_command_intent_confirmed",
                chat_id=chat_id,
                rendered=pending.intent.rendered,
            )
            clear_pending(chat_id)
            ok = await _dispatch_handler(bot, message, pending.intent)
            if not ok:
                try:
                    await message.reply(f"Не удалось выполнить `{pending.intent.rendered}`.")
                except Exception:  # noqa: BLE001
                    pass
            return True
        if _confirm_no(text):
            logger.info("nlu_command_intent_rejected", chat_id=chat_id)
            clear_pending(chat_id)
            try:
                await message.reply("Окей, отменил.")
            except Exception:  # noqa: BLE001
                pass
            return True
        # Не confirmation — clearим pending и валим на обычный pipeline.
        clear_pending(chat_id)

    # Don't intercept explicit !command — обычный pipeline их и так маршрутизирует.
    if text.lstrip().startswith("!"):
        return False

    try:
        intent = await extract_command_intent(
            text,
            owner_only=True,
            is_owner=True,
            use_llm=False,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("nlu_extract_failed", chat_id=chat_id, error=str(exc))
        return False

    if intent is None:
        logger.debug("nlu_command_intent_skipped", chat_id=chat_id, reason="no_intent")
        return False

    # destructive guard уже снижает confidence в extractor — здесь
    # дополнительно блокируем dispatch при destructive=True.
    if intent.destructive and intent.confidence >= 0.8:
        intent.confidence = 0.7  # safety net

    if intent.confidence >= 0.8 and not intent.destructive:
        logger.info(
            "nlu_command_intent_dispatched",
            chat_id=chat_id,
            cmd=intent.command,
            rendered=intent.rendered,
            confidence=round(intent.confidence, 2),
        )
        ok = await _dispatch_handler(bot, message, intent)
        if not ok:
            # dispatch failed (unknown handler) → fall through to LLM
            return False
        return True

    if 0.5 <= intent.confidence < 0.8:
        logger.info(
            "nlu_command_intent_pending_confirm",
            chat_id=chat_id,
            cmd=intent.command,
            rendered=intent.rendered,
            confidence=round(intent.confidence, 2),
        )
        _store_pending(chat_id, intent)
        try:
            await message.reply(f"Понял как: `{intent.rendered}`. Подтвердить? (да/нет)")
        except Exception as exc:  # noqa: BLE001
            logger.warning("nlu_pending_reply_failed", error=str(exc))
            clear_pending(chat_id)
            return False
        return True

    logger.debug(
        "nlu_command_intent_skipped",
        chat_id=chat_id,
        reason="low_confidence",
        confidence=round(intent.confidence, 2),
    )
    return False
