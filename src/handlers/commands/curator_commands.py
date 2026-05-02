# -*- coding: utf-8 -*-
"""!curator — Wave 14-I + 15-C Steps 1-2/4.

Подкоманды:
  !curator dry-run [team]    — read-only анализ последних раундов (Step 1).
  !curator propose <team>    — LLM-предложение апдейта промпта (Step 2).
  !curator proposals         — список pending proposals.
  !curator show <id>         — показать конкретный proposal/diff.
  !curator help              — usage hint.

Owner-only. Step 2 требует доступного Gemini API key — иначе fail-soft.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pyrogram.types import Message

from ...core.access_control import is_owner_user_id
from ...core.logger import get_logger
from ...core.skill_curator import skill_curator
from ...core.swarm_bus import TEAM_REGISTRY
from ...core.swarm_team_prompts import get_team_system_prompt

if TYPE_CHECKING:
    from ...userbot_bridge import KraabUserbot

logger = get_logger(__name__)


_USAGE = (
    "🧑‍🏫 **SkillCurator** (Waves 14-I + 15-C, Steps 1-2/4)\n\n"
    "`!curator dry-run [team]` — read-only анализ последних раундов\n"
    "`!curator dry-run` — все 4 команды одним отчётом\n"
    "`!curator propose <team>` — LLM-предложение апдейта промпта (Gemini-3-flash)\n"
    "`!curator proposals` — список pending proposals\n"
    "`!curator show <id>` — показать конкретный proposal/diff\n"
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

    if sub == "propose":
        team_arg = tokens[1].lower() if len(tokens) > 1 else ""
        await _run_propose(bot, message, team_arg)
        return

    if sub == "proposals":
        team_arg = tokens[1].lower() if len(tokens) > 1 else ""
        await _run_proposals_list(bot, message, team_arg)
        return

    if sub == "show":
        proposal_id = tokens[1] if len(tokens) > 1 else ""
        await _run_proposal_show(bot, message, proposal_id)
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


async def _run_propose(bot: "KraabUserbot", message: Message, team_arg: str) -> None:
    """!curator propose <team> — LLM-генерирует diff и сохраняет proposal."""

    if not team_arg:
        await bot._safe_reply_or_send_new(
            message,
            "❌ Укажи команду: `!curator propose <team>`\n"
            f"Доступны: {', '.join(sorted(TEAM_REGISTRY))}",
        )
        return
    if team_arg not in TEAM_REGISTRY:
        await bot._safe_reply_or_send_new(
            message,
            f"❌ Команда `{team_arg}` не найдена. Доступны: {', '.join(sorted(TEAM_REGISTRY))}",
        )
        return

    try:
        report = skill_curator.analyze_recent_rounds(team_arg, days=7)
    except Exception as exc:  # noqa: BLE001
        logger.warning("curator_propose_analyze_failed", team=team_arg, error=str(exc))
        await bot._safe_reply_or_send_new(message, f"❌ Анализ не удался: {exc}")
        return

    if report.rounds_analyzed == 0:
        await bot._safe_reply_or_send_new(
            message,
            f"⚠️ Нет данных за окно для команды `{team_arg}` — proposal не сгенерирован.",
        )
        return

    current_prompt = get_team_system_prompt(team_arg)

    try:
        proposal = await skill_curator.propose_prompt_update(team_arg, current_prompt, report)
    except Exception as exc:  # noqa: BLE001
        logger.warning("curator_propose_failed", team=team_arg, error=str(exc))
        await bot._safe_reply_or_send_new(message, f"❌ Proposal failed: {exc}")
        return

    if proposal is None:
        await bot._safe_reply_or_send_new(
            message,
            "⚠️ LLM-провайдер недоступен (нет Gemini API key или request failed). "
            "Proposal не создан. Проверь GEMINI_API_KEY / GEMINI_API_KEY_PAID.",
        )
        return

    diff_preview = proposal.proposed_diff or "(diff пуст — модель не предложила изменений)"
    if len(diff_preview) > 2000:
        diff_preview = diff_preview[:2000] + "\n... (truncated)"

    summary = (
        f"🧑‍🏫 **SkillCurator proposal** — `{proposal.proposal_id}` (status: {proposal.status})\n"
        f"- Команда: `{proposal.team}`\n"
        f"- Confidence: {proposal.confidence}\n"
        f"- Focus: {', '.join(proposal.focus) or '—'}\n"
        f"- Model: `{proposal.model}`\n"
        f"- Rationale: {proposal.rationale or '—'}\n\n"
        f"```diff\n{diff_preview}\n```\n\n"
        f"Approval/rollback — Step 3 (TODO). См. `!curator show {proposal.proposal_id}`."
    )
    await bot._safe_reply_or_send_new(message, summary)


async def _run_proposals_list(bot: "KraabUserbot", message: Message, team_arg: str) -> None:
    """!curator proposals — список pending/historical proposals."""

    team_filter = team_arg or None
    if team_filter and team_filter not in TEAM_REGISTRY:
        await bot._safe_reply_or_send_new(
            message,
            f"❌ Команда `{team_filter}` не найдена. Доступны: {', '.join(sorted(TEAM_REGISTRY))}",
        )
        return

    proposals = skill_curator.list_proposals(team=team_filter)
    if not proposals:
        scope = f" для `{team_filter}`" if team_filter else ""
        await bot._safe_reply_or_send_new(message, f"📭 Proposals{scope} нет.")
        return

    lines = [f"📋 **SkillCurator proposals** (всего: {len(proposals)})\n"]
    for entry in proposals[:20]:
        lines.append(
            f"- `{entry.get('proposal_id', '?')}` "
            f"({entry.get('team', '?')}, {entry.get('status', '?')}, "
            f"conf={entry.get('confidence', 0)})"
        )
    if len(proposals) > 20:
        lines.append(f"\n_…и ещё {len(proposals) - 20}_")
    lines.append("\nПодробнее: `!curator show <id>`")
    await bot._safe_reply_or_send_new(message, "\n".join(lines))


async def _run_proposal_show(bot: "KraabUserbot", message: Message, proposal_id: str) -> None:
    """!curator show <id> — показать proposal."""

    if not proposal_id:
        await bot._safe_reply_or_send_new(message, "❌ Укажи id: `!curator show <proposal_id>`")
        return
    data = skill_curator.load_proposal(proposal_id)
    if not data:
        await bot._safe_reply_or_send_new(message, f"❌ Proposal `{proposal_id}` не найден.")
        return

    diff = data.get("proposed_diff") or "(пустой diff)"
    if len(diff) > 3000:
        diff = diff[:3000] + "\n... (truncated)"

    body = (
        f"🧑‍🏫 **Proposal `{data.get('proposal_id', proposal_id)}`**\n"
        f"- Team: `{data.get('team')}`\n"
        f"- Status: {data.get('status')}\n"
        f"- Confidence: {data.get('confidence')}\n"
        f"- Model: `{data.get('model')}`\n"
        f"- Generated: {data.get('generated_at')}\n"
        f"- Focus: {', '.join(data.get('focus') or []) or '—'}\n"
        f"- Rationale: {data.get('rationale') or '—'}\n\n"
        f"```diff\n{diff}\n```"
    )
    await bot._safe_reply_or_send_new(message, body)
