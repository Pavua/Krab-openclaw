# -*- coding: utf-8 -*-
"""!engine — управление AgentEngine routing (Wave 16-B, Hermes Phase B).

Подкоманды:
  !engine                              — текущий resolution + health обоих движков
  !engine here <openclaw|hermes|auto>  — per-chat override
  !engine here clear                   — снять override
  !engine room <name> <engine|clear>   — per-swarm-room policy
  !engine status                       — health для обоих движков

Owner-only. Feature-flagged bridge. Real routing — Phase C.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pyrogram.types import Message

from ...core.access_control import is_owner_user_id
from ...core.agent_engine_router import (
    VALID,
    get_chat_override,
    resolve_engine,
    set_chat_override,
    set_room_engine,
)
from ...core.logger import get_logger

if TYPE_CHECKING:
    from ...userbot_bridge import KraabUserbot

logger = get_logger(__name__)

_USAGE = (
    "⚙️ **!engine** — управление AgentEngine routing (Wave 16-B)\n\n"
    "`!engine` — показать текущий engine + health\n"
    "`!engine here <openclaw|hermes|auto>` — per-chat override\n"
    "`!engine here clear` — снять override чата\n"
    "`!engine room <name> <openclaw|hermes|auto>` — per-swarm-room\n"
    "`!engine room <name> clear` — снять room policy\n"
    "`!engine status` — health обоих движков\n\n"
    f"Доступные engines: {', '.join(sorted(VALID))}"
)


async def handle_engine(bot: "KraabUserbot", message: Message) -> None:
    """!engine — диспетчер субкоманд. Owner-only."""

    user_id = getattr(getattr(message, "from_user", None), "id", None)
    if not is_owner_user_id(user_id or 0):
        await bot._safe_reply_or_send_new(message, "🔒 `!engine` доступен только владельцу.")
        return

    try:
        from ...core.command_registry import bump_command

        bump_command("engine")
    except Exception:  # noqa: BLE001
        pass

    raw = (bot._get_command_args(message) or "").strip()
    tokens = raw.split() if raw else []
    sub = tokens[0].lower() if tokens else ""

    if not sub or sub in {"help", "?"}:
        await _show_current(bot, message)
        return

    if sub == "here":
        await _handle_here(bot, message, tokens[1:])
        return

    if sub == "room":
        await _handle_room(bot, message, tokens[1:])
        return

    if sub == "status":
        await _show_status(bot, message)
        return

    await bot._safe_reply_or_send_new(
        message,
        f"❌ Неизвестная субкоманда `{sub}`.\n\n{_USAGE}",
    )


async def _show_current(bot: "KraabUserbot", message: Message) -> None:
    """!engine — показать текущее resolution для этого чата."""
    chat_id = getattr(message.chat, "id", None)
    current = resolve_engine(chat_id=chat_id)
    override = get_chat_override(chat_id) if chat_id else None
    env_engine = __import__("os").environ.get("KRAB_AGENT_ENGINE", "openclaw")

    lines = [
        "⚙️ **AgentEngine Router** (Wave 16-B)",
        f"- Resolved engine: `{current}`",
        f"- Chat override: `{override or 'нет'}`",
        f"- Env KRAB_AGENT_ENGINE: `{env_engine}`",
        "",
        "_Phase B: Hermes routing — stub. Real wiring в Phase C._",
    ]
    await bot._safe_reply_or_send_new(message, "\n".join(lines))


async def _handle_here(bot: "KraabUserbot", message: Message, args: list[str]) -> None:
    """!engine here <engine|clear>."""
    chat_id = getattr(message.chat, "id", None)
    if chat_id is None:
        await bot._safe_reply_or_send_new(message, "❌ Не удалось определить chat_id.")
        return

    if not args:
        await bot._safe_reply_or_send_new(
            message,
            f"❌ Укажи engine: `!engine here <{' | '.join(sorted(VALID))} | clear>`",
        )
        return

    target = args[0].lower()
    if target == "clear":
        set_chat_override(chat_id, None)
        await bot._safe_reply_or_send_new(
            message,
            f"✅ Override для чата `{chat_id}` снят — будет использоваться глобальный default.",
        )
        return

    if target not in VALID:
        await bot._safe_reply_or_send_new(
            message,
            f"❌ Неизвестный engine `{target}`. Доступны: {', '.join(sorted(VALID))}",
        )
        return

    try:
        set_chat_override(chat_id, target)  # type: ignore[arg-type]
        await bot._safe_reply_or_send_new(
            message,
            f"✅ Engine для чата `{chat_id}` → `{target}`.",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("engine_set_chat_override_failed", error=str(exc))
        await bot._safe_reply_or_send_new(message, f"❌ Ошибка: {exc}")


async def _handle_room(bot: "KraabUserbot", message: Message, args: list[str]) -> None:
    """!engine room <name> <engine|clear>."""
    if len(args) < 2:  # noqa: PLR2004
        await bot._safe_reply_or_send_new(
            message,
            f"❌ Использование: `!engine room <name> <{' | '.join(sorted(VALID))} | clear>`",
        )
        return

    room_name = args[0].lower()
    target = args[1].lower()

    if target == "clear":
        set_room_engine(room_name, None)
        await bot._safe_reply_or_send_new(
            message,
            f"✅ Room policy для `{room_name}` снята.",
        )
        return

    if target not in VALID:
        await bot._safe_reply_or_send_new(
            message,
            f"❌ Неизвестный engine `{target}`. Доступны: {', '.join(sorted(VALID))}",
        )
        return

    try:
        set_room_engine(room_name, target)  # type: ignore[arg-type]
        await bot._safe_reply_or_send_new(
            message,
            f"✅ Engine для swarm room `{room_name}` → `{target}`.",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("engine_set_room_failed", error=str(exc))
        await bot._safe_reply_or_send_new(message, f"❌ Ошибка: {exc}")


async def _show_status(bot: "KraabUserbot", message: Message) -> None:
    """!engine status — health probe обоих движков."""
    lines = ["⚙️ **AgentEngine Status** (Wave 16-B)"]

    # OpenClaw — всегда accessible (нет отдельного health probe в Phase B)
    lines.append("- `openclaw`: ✅ active (primary engine)")

    # Hermes — проверяем bridge
    try:
        from ...integrations.hermes_acp_bridge import get_hermes_bridge

        bridge = await get_hermes_bridge()
        health = await bridge.health()
        if health.is_healthy:
            latency = f", {health.latency_ms}ms" if health.latency_ms is not None else ""
            lines.append(f"- `hermes`: ✅ healthy{latency}")
        else:
            lines.append(f"- `hermes`: ❌ unavailable — {health.error}")
    except Exception as exc:  # noqa: BLE001
        lines.append(f"- `hermes`: ❌ error — {exc}")

    lines.append("")
    lines.append("_Phase B: routing stub. Production wiring — Phase C._")
    await bot._safe_reply_or_send_new(message, "\n".join(lines))
