# -*- coding: utf-8 -*-
"""!curator — Wave 14-I + 15-C + 16-A Steps 1-3/4.

Подкоманды:
  !curator dry-run [team]           — read-only анализ последних раундов (Step 1).
  !curator propose <team>           — LLM-предложение апдейта промпта (Step 2).
  !curator proposals                — список pending proposals.
  !curator show <id>                — показать конкретный proposal/diff.
  !curator apply <id> [--force]     — применить approved proposal (Step 3).
  !curator rollback <team> [--version N] — откатить overlay к baseline или версии N.
  !curator overlays                 — показать активные overlays.
  !curator help                     — usage hint.

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
    "🧑‍🏫 **SkillCurator** (Waves 14-I + 15-C + 16-A + 16-D, Steps 1-4/4)\n\n"
    "`!curator dry-run [team]` — read-only анализ последних раундов\n"
    "`!curator dry-run` — все 4 команды одним отчётом\n"
    "`!curator propose <team>` — LLM-предложение апдейта промпта (Gemini-3-flash)\n"
    "`!curator proposals` — список pending proposals\n"
    "`!curator show <id>` — показать конкретный proposal/diff\n"
    "`!curator apply <id> [--force]` — применить proposal к live prompt\n"
    "`!curator rollback <team> [--version N]` — откатить к baseline или к версии N\n"
    "`!curator overlays` — показать активные overlays всех команд\n"
    "`!curator ab start <team> <proposal_id> [--rounds N]` — запустить A/B тест\n"
    "`!curator ab status [team]` — статус A/B тестов\n"
    "`!curator ab evaluate <ab_id> [--apply]` — evaluate + опциональный auto-apply\n"
    "`!curator ab cancel <ab_id>` — отменить A/B тест\n"
    "`!curator ab list [team]` — список A/B тестов\n"
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

    if sub == "apply":
        proposal_id = tokens[1] if len(tokens) > 1 else ""
        force = "--force" in tokens
        await _run_apply(bot, message, proposal_id, force=force)
        return

    if sub == "rollback":
        team_arg = tokens[1].lower() if len(tokens) > 1 else ""
        version = -1
        if "--version" in tokens:
            idx = tokens.index("--version")
            if idx + 1 < len(tokens):
                try:
                    version = int(tokens[idx + 1])
                except ValueError:
                    await bot._safe_reply_or_send_new(message, "❌ `--version` должен быть числом.")
                    return
        await _run_rollback(bot, message, team_arg, version=version)
        return

    if sub == "overlays":
        await _run_overlays(bot, message)
        return

    if sub == "ab":
        ab_sub = tokens[1].lower() if len(tokens) > 1 else "help"
        await _run_ab(bot, message, ab_sub, tokens[2:])
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
    """!curator propose <team> — LLM-генерирует diff и сохраняет proposal.

    Wave 38-A: требует KRAB_SKILL_CURATOR_PROPOSE_ENABLED=1 (feature opt-in).
    """

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

    # ENV gate — по умолчанию OFF (Wave 38-A feature opt-in)
    try:
        from ...config import config as _cfg

        if not getattr(_cfg, "KRAB_SKILL_CURATOR_PROPOSE_ENABLED", False):
            await bot._safe_reply_or_send_new(
                message,
                "⛔ SkillCurator propose выключен.\n"
                "Включить: `KRAB_SKILL_CURATOR_PROPOSE_ENABLED=1` в `.env` + перезапуск.",
            )
            return
    except Exception:  # noqa: BLE001 — конфиг может быть недоступен в тестах
        pass

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


async def _run_apply(
    bot: "KraabUserbot", message: Message, proposal_id: str, *, force: bool
) -> None:
    """!curator apply <proposal_id> [--force] — применить proposal к live prompt."""

    if not proposal_id:
        await bot._safe_reply_or_send_new(
            message,
            "❌ Укажи id: `!curator apply <proposal_id> [--force]`",
        )
        return

    await bot._safe_reply_or_send_new(message, f"⏳ Применяю proposal `{proposal_id}`…")
    try:
        ok, msg = await skill_curator.apply_with_approval(proposal_id, force=force)
    except Exception as exc:  # noqa: BLE001
        logger.warning("curator_apply_command_failed", proposal_id=proposal_id, error=str(exc))
        await bot._safe_reply_or_send_new(message, f"❌ Apply error: {exc}")
        return

    icon = "✅" if ok else "❌"
    await bot._safe_reply_or_send_new(message, f"{icon} `{proposal_id}`: {msg}")


async def _run_rollback(
    bot: "KraabUserbot", message: Message, team_arg: str, *, version: int
) -> None:
    """!curator rollback <team> [--version N] — откатить overlay."""

    if not team_arg:
        await bot._safe_reply_or_send_new(
            message,
            "❌ Укажи команду: `!curator rollback <team> [--version N]`\n"
            f"Доступны: {', '.join(sorted(TEAM_REGISTRY))}",
        )
        return
    if team_arg not in TEAM_REGISTRY:
        await bot._safe_reply_or_send_new(
            message,
            f"❌ Команда `{team_arg}` не найдена. Доступны: {', '.join(sorted(TEAM_REGISTRY))}",
        )
        return

    ver_str = "baseline" if version == -1 else f"version {version}"
    await bot._safe_reply_or_send_new(message, f"⏳ Rollback `{team_arg}` → {ver_str}…")
    try:
        ok, msg = await skill_curator.rollback(team_arg, version=version)
    except Exception as exc:  # noqa: BLE001
        logger.warning("curator_rollback_command_failed", team=team_arg, error=str(exc))
        await bot._safe_reply_or_send_new(message, f"❌ Rollback error: {exc}")
        return

    icon = "✅" if ok else "❌"
    await bot._safe_reply_or_send_new(message, f"{icon} `{team_arg}`: {msg}")


async def _run_overlays(bot: "KraabUserbot", message: Message) -> None:
    """!curator overlays — список активных overlays."""

    try:
        from ...core.skill_curator_state import CURATOR_STATE_PATH, CuratorState

        state = CuratorState.load(CURATOR_STATE_PATH)
        overlays = state.active_overlays
    except Exception as exc:  # noqa: BLE001
        logger.warning("curator_overlays_load_failed", error=str(exc))
        await bot._safe_reply_or_send_new(message, f"❌ Не удалось загрузить state: {exc}")
        return

    if not overlays:
        await bot._safe_reply_or_send_new(message, "📭 Активных overlays нет.")
        return

    lines = [f"🧑‍🏫 **Активные overlays** ({len(overlays)})\n"]
    for team, ov in sorted(overlays.items()):
        preview = (ov.get("prompt") or "")[:80].replace("\n", " ")
        lines.append(
            f"- `{team}` v{ov.get('version', '?')} "
            f"(proposal: `{ov.get('proposal_id', '?')}`, "
            f"applied: {ov.get('applied_at', '?')[:10]})\n"
            f"  `{preview}…`"
        )
    lines.append("\n`!curator rollback <team>` — вернуть к baseline")
    await bot._safe_reply_or_send_new(message, "\n".join(lines))


# ---------------------------------------------------------------------------
# A/B framework subcommands (Step 4)
# ---------------------------------------------------------------------------

_AB_USAGE = (
    "🧪 **!curator ab** — A/B framework (Step 4)\n\n"
    "`!curator ab start <team> <proposal_id> [--rounds N]` — запустить A/B тест\n"
    "`!curator ab status [team]` — статус активных A/B тестов\n"
    "`!curator ab evaluate <ab_id> [--apply]` — evaluate + опциональный auto-apply\n"
    "`!curator ab cancel <ab_id>` — отменить A/B тест\n"
    "`!curator ab list [team]` — список всех A/B тестов\n"
)


async def _run_ab(bot: "KraabUserbot", message: Message, sub: str, args: list[str]) -> None:
    """Диспетчер !curator ab <sub> субкоманд."""

    if sub in {"help", "?", ""}:
        await bot._safe_reply_or_send_new(message, _AB_USAGE)
        return

    if sub == "start":
        await _ab_start(bot, message, args)
    elif sub == "status":
        await _ab_status(bot, message, args)
    elif sub == "evaluate":
        await _ab_evaluate(bot, message, args)
    elif sub == "cancel":
        await _ab_cancel(bot, message, args)
    elif sub == "list":
        await _ab_list(bot, message, args)
    else:
        await bot._safe_reply_or_send_new(
            message, f"❌ Неизвестная A/B субкоманда `{sub}`.\n\n{_AB_USAGE}"
        )


async def _ab_start(bot: "KraabUserbot", message: Message, args: list[str]) -> None:
    """!curator ab start <team> <proposal_id> [--rounds N]."""

    if len(args) < 2:
        await bot._safe_reply_or_send_new(
            message,
            "❌ Укажи: `!curator ab start <team> <proposal_id> [--rounds N]`",
        )
        return

    team_arg = args[0].lower()
    proposal_id = args[1]

    if team_arg not in TEAM_REGISTRY:
        await bot._safe_reply_or_send_new(
            message,
            f"❌ Команда `{team_arg}` не найдена. Доступны: {', '.join(sorted(TEAM_REGISTRY))}",
        )
        return

    n_rounds = 10
    if "--rounds" in args:
        idx = args.index("--rounds")
        if idx + 1 < len(args):
            try:
                n_rounds = max(1, int(args[idx + 1]))
            except ValueError:
                await bot._safe_reply_or_send_new(message, "❌ `--rounds` должен быть числом.")
                return

    await bot._safe_reply_or_send_new(
        message,
        f"⏳ Запускаю A/B тест для `{team_arg}` (proposal: `{proposal_id}`, rounds={n_rounds})…",
    )

    try:
        result = skill_curator.start_ab_test(team_arg, proposal_id, n_rounds=n_rounds)
    except Exception as exc:  # noqa: BLE001
        logger.warning("curator_ab_start_failed", team=team_arg, error=str(exc))
        await bot._safe_reply_or_send_new(message, f"❌ A/B start error: {exc}")
        return

    if result is None:
        await bot._safe_reply_or_send_new(
            message,
            f"⚠️ Для команды `{team_arg}` уже есть running A/B тест. "
            "Отмени его через `!curator ab cancel <ab_id>` или дождись завершения.",
        )
        return

    await bot._safe_reply_or_send_new(
        message,
        f"✅ A/B тест запущен!\n"
        f"- ab_id: `{result['ab_id']}`\n"
        f"- team: `{result['team']}`\n"
        f"- rounds target: {result['n_rounds_target']}\n"
        f"- proposal: `{result['candidate_proposal_id']}`\n\n"
        f"Отслеживай: `!curator ab status {team_arg}`",
    )


async def _ab_status(bot: "KraabUserbot", message: Message, args: list[str]) -> None:
    """!curator ab status [team] — статус A/B тестов."""

    team_filter = args[0].lower() if args else None

    teams = [team_filter] if team_filter else list(sorted(TEAM_REGISTRY))
    lines = ["🧪 **A/B тесты (running)**\n"]
    found = False

    for team in teams:
        try:
            ab_data = skill_curator.get_active_ab_test(team)
        except Exception as exc:  # noqa: BLE001
            logger.warning("curator_ab_status_failed", team=team, error=str(exc))
            continue
        if not ab_data:
            continue
        found = True
        rounds_done = len(ab_data.get("rounds") or [])
        n_target = ab_data.get("n_rounds_target", 10)
        lines.append(
            f"- `{team}`: `{ab_data['ab_id']}` "
            f"({ab_data.get('status')}, {rounds_done}/{n_target} rounds)\n"
            f"  started: {(ab_data.get('started_at') or '')[:16]}"
        )

    if not found:
        scope = f" для `{team_filter}`" if team_filter else ""
        await bot._safe_reply_or_send_new(message, f"📭 Активных A/B тестов{scope} нет.")
        return

    await bot._safe_reply_or_send_new(message, "\n".join(lines))


async def _ab_evaluate(bot: "KraabUserbot", message: Message, args: list[str]) -> None:
    """!curator ab evaluate <ab_id> [--apply]."""

    if not args:
        await bot._safe_reply_or_send_new(
            message, "❌ Укажи: `!curator ab evaluate <ab_id> [--apply]`"
        )
        return

    ab_id = args[0]
    auto_apply = "--apply" in args

    await bot._safe_reply_or_send_new(message, f"⏳ Evaluating A/B тест `{ab_id}`…")

    try:
        if auto_apply:
            result, applied = await skill_curator.evaluate_ab_test_and_apply(ab_id)
        else:
            result = skill_curator.evaluate_ab_test(ab_id)
            applied = False
    except Exception as exc:  # noqa: BLE001
        logger.warning("curator_ab_evaluate_failed", ab_id=ab_id, error=str(exc))
        await bot._safe_reply_or_send_new(message, f"❌ Evaluate error: {exc}")
        return

    winner = result.get("winner") or "—"
    status = result.get("status", "?")
    reason = result.get("reason", "—")
    metrics = result.get("metrics", {})
    ctrl = metrics.get("control", {})
    cand = metrics.get("candidate", {})
    rounds_done = result.get("rounds_completed", "?")
    n_target = result.get("n_rounds_target", "?")

    apply_note = ""
    if auto_apply:
        if applied:
            apply_note = "\n✅ Candidate применён автоматически."
        elif winner == "candidate":
            apply_note = f"\n⚠️ Auto-apply: {result.get('auto_apply_msg') or result.get('auto_apply_error') or 'failed'}"

    body = (
        f"🧪 **A/B evaluate** — `{ab_id}`\n"
        f"- Status: {status}\n"
        f"- Rounds: {rounds_done}/{n_target}\n"
        f"- Winner: **{winner}**\n"
        f"- Reason: {reason}\n\n"
        f"**Метрики:**\n"
        f"- Control: SR={ctrl.get('success_rate', '?')}, "
        f"cost={ctrl.get('cost_usd_avg', '?')}, lat={ctrl.get('latency_s_avg', '?')}s\n"
        f"- Candidate: SR={cand.get('success_rate', '?')}, "
        f"cost={cand.get('cost_usd_avg', '?')}, lat={cand.get('latency_s_avg', '?')}s"
        f"{apply_note}"
    )
    await bot._safe_reply_or_send_new(message, body)


async def _ab_cancel(bot: "KraabUserbot", message: Message, args: list[str]) -> None:
    """!curator ab cancel <ab_id>."""

    if not args:
        await bot._safe_reply_or_send_new(message, "❌ Укажи: `!curator ab cancel <ab_id>`")
        return

    ab_id = args[0]
    try:
        ok = skill_curator.cancel_ab_test(ab_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("curator_ab_cancel_failed", ab_id=ab_id, error=str(exc))
        await bot._safe_reply_or_send_new(message, f"❌ Cancel error: {exc}")
        return

    icon = "✅" if ok else "❌"
    msg = "отменён" if ok else "не найден или уже завершён"
    await bot._safe_reply_or_send_new(message, f"{icon} A/B тест `{ab_id}`: {msg}.")


async def _ab_list(bot: "KraabUserbot", message: Message, args: list[str]) -> None:
    """!curator ab list [team] — список A/B тестов."""

    team_filter = args[0].lower() if args else None
    if team_filter and team_filter not in TEAM_REGISTRY:
        await bot._safe_reply_or_send_new(
            message,
            f"❌ Команда `{team_filter}` не найдена. Доступны: {', '.join(sorted(TEAM_REGISTRY))}",
        )
        return

    try:
        tests = skill_curator.list_ab_tests(team=team_filter)
    except Exception as exc:  # noqa: BLE001
        logger.warning("curator_ab_list_failed", error=str(exc))
        await bot._safe_reply_or_send_new(message, f"❌ List error: {exc}")
        return

    if not tests:
        scope = f" для `{team_filter}`" if team_filter else ""
        await bot._safe_reply_or_send_new(message, f"📭 A/B тестов{scope} нет.")
        return

    lines = [f"🧪 **A/B тесты** (всего: {len(tests)})\n"]
    for t in tests[:20]:
        rounds_done = len(t.get("rounds") or [])
        n_target = t.get("n_rounds_target", 10)
        lines.append(
            f"- `{t.get('ab_id', '?')}` "
            f"({t.get('team', '?')}, {t.get('status', '?')}, "
            f"{rounds_done}/{n_target} rounds, "
            f"winner={t.get('decision') or '—'})"
        )
    if len(tests) > 20:
        lines.append(f"\n_…и ещё {len(tests) - 20}_")
    lines.append("\nПодробнее: `!curator ab evaluate <ab_id>`")
    await bot._safe_reply_or_send_new(message, "\n".join(lines))
