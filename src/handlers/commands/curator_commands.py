# -*- coding: utf-8 -*-
"""!curator — Wave 14-I Step 1/4: dry-run analyzer (read-only).

Подкоманды:
  !curator dry-run [team]   — анализ последних раундов (read-only).
  !curator help             — usage hint.

Без LLM, без mutations. Owner-only.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pyrogram.types import Message

from ...core.access_control import is_owner_user_id
from ...core.logger import get_logger
from ...core.skill_curator import skill_curator
from ...core.swarm_bus import TEAM_REGISTRY

if TYPE_CHECKING:
    from ...userbot_bridge import KraabUserbot

logger = get_logger(__name__)


_USAGE = (
    "🧑‍🏫 **SkillCurator** (Wave 14-I, dry-run only)\n\n"
    "`!curator dry-run [team]` — анализ последних раундов команды (read-only)\n"
    "`!curator dry-run` — все 4 команды одним отчётом\n"
    "`!curator help` — эта справка\n\n"
    f"Доступные команды: {', '.join(sorted(TEAM_REGISTRY))}"
)


async def handle_curator(bot: "KraabUserbot", message: Message) -> None:
    """!curator — диспетчер субкоманд. Owner-only."""

    user_id = getattr(getattr(message, "from_user", None), "id", None)
    if not is_owner_user_id(user_id or 0):
        await bot._safe_reply_or_send_new(message, "🔒 `!curator` доступен только владельцу.")
        return

    try:
        from ...core.command_registry import bump_command

        bump_command("curator")
    except Exception:  # noqa: BLE001 — counter best-effort
        pass

    raw = (bot._get_command_args(message) or "").strip()
    tokens = raw.split() if raw else []
    sub = tokens[0].lower() if tokens else "help"

    if sub in {"help", "?", ""}:
        await bot._safe_reply_or_send_new(message, _USAGE)
        return

    if sub == "dry-run":
        team_arg = tokens[1].lower() if len(tokens) > 1 else ""
        await _run_dry_run(bot, message, team_arg)
        return

    await bot._safe_reply_or_send_new(
        message,
        f"❌ Неизвестная субкоманда `{sub}`.\n\n{_USAGE}",
    )


async def _run_dry_run(bot: "KraabUserbot", message: Message, team_arg: str) -> None:
    """Запускает analyzer и отправляет markdown отчёт."""

    teams: list[str]
    if team_arg:
        if team_arg not in TEAM_REGISTRY:
            await bot._safe_reply_or_send_new(
                message,
                f"❌ Команда `{team_arg}` не найдена. Доступны: {', '.join(sorted(TEAM_REGISTRY))}",
            )
            return
        teams = [team_arg]
    else:
        teams = sorted(TEAM_REGISTRY)

    reports = []
    for team in teams:
        try:
            reports.append(skill_curator.analyze_recent_rounds(team, days=7))
        except Exception as exc:  # noqa: BLE001
            logger.warning("curator_dry_run_failed", team=team, error=str(exc))

    if not reports:
        await bot._safe_reply_or_send_new(message, "⚠️ Не удалось собрать отчёт.")
        return

    markdown = skill_curator.render_combined_markdown(reports)

    # Сохраняем на диск (read-only relative to swarm state — мы пишем только в curator/reports)
    try:
        suffix = team_arg or "all"
        skill_curator.save_report(suffix, markdown)
    except Exception as exc:  # noqa: BLE001
        logger.warning("curator_report_persist_failed", error=str(exc))

    await bot._safe_reply_or_send_new(message, markdown)
