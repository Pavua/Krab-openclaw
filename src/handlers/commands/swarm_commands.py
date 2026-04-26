# -*- coding: utf-8 -*-
"""
swarm_commands - Phase 2 Wave 8 extraction (Session 27).

Команда ``!swarm`` со всеми subcommands (teams/research/summary/info/stats/
report/setup/schedule/jobs/unschedule/memory/task/listen/status/channels/
artifacts/setchat) и адаптер ``_AgentRoomRouterAdapter`` вынесены сюда.

Re-exported from command_handlers.py для обратной совместимости:
- ``userbot_bridge`` импортирует ``_AgentRoomRouterAdapter`` лениво из
  command_handlers (cron prompt + W32 hotfix);
- ``commands.ai_commands`` тоже импортирует адаптер лениво из command_handlers
  (handle_agent fallback);
- тесты ``test_cron_prompt_context`` патчат
  ``command_handlers._AgentRoomRouterAdapter``;
- тесты ``test_swarm_status_deep`` патчат
  ``command_handlers._swarm_status_deep_report`` — поэтому
  ``_swarm_status_deep_report`` остаётся в command_handlers, а
  swarm_commands импортирует его лениво (через namespace модуля).

См. ``docs/CODE_SPLITS_PLAN.md`` Phase 2 - domain extractions.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pyrogram.types import Message

from ...config import config
from ...core.access_control import AccessLevel
from ...core.exceptions import UserInputError
from ...core.logger import get_logger
from ...core.telegram_buttons import build_swarm_team_buttons
from ...openclaw_client import openclaw_client

if TYPE_CHECKING:
    from ...userbot_bridge import KraabUserbot

logger = get_logger(__name__)


def _split_text(text: str) -> list[str]:
    """Lazy proxy к ``command_handlers._split_text_for_telegram``.

    Помогает избежать циркулярного импорта на старте.
    """
    from ..command_handlers import _split_text_for_telegram

    return _split_text_for_telegram(text)


class _AgentRoomRouterAdapter:
    """
    Легковесный адаптер роевого запуска для userbot-команд.

    Почему отдельно:
    - AgentRoom ожидает контракт `route_query(prompt, skip_swarm=True)`;
    - userbot работает напрямую через `openclaw_client.send_message_stream`;
    - адаптер связывает эти два слоя без изменения core-логики.
    """

    def __init__(
        self,
        *,
        chat_id: str,
        system_prompt: str,
        team_name: str | None = None,
    ) -> None:
        self.chat_id = chat_id
        self.system_prompt = system_prompt
        # Имя команды свёрма — пробрасывается в per-team tool allowlist
        # через ContextVar. None/"" → фильтр не применяется (backward-compat).
        self.team_name = team_name or None

    async def route_query(self, prompt: str, skip_swarm: bool = False, **_: Any) -> str:
        """
        Выполняет один роевой шаг через OpenClaw stream.

        `skip_swarm` принят для совместимости контракта AgentRoom.
        """
        del skip_swarm
        chunks: list[str] = []
        # Увеличенный лимит: модели нужен бюджет на tool_calls JSON + финальный ответ.
        # 700 токенов слишком мало для tool call (200+ токенов на JSON аргументы).
        max_output_tokens = int(getattr(config, "SWARM_ROLE_MAX_OUTPUT_TOKENS", 4096) or 4096)

        # Выставляем ContextVar с командой — он будет прочитан в
        # `openclaw_client._openclaw_completion_once` и применён к manifest'у.
        # Сброс в finally обязателен, иначе протечёт на не-swarm запросы.
        from ...core.swarm_tool_allowlist import reset_current_team, set_current_team

        _token = set_current_team(self.team_name)
        try:
            async for chunk in openclaw_client.send_message_stream(
                message=prompt,
                chat_id=self.chat_id,
                system_prompt=self.system_prompt,
                force_cloud=bool(getattr(config, "FORCE_CLOUD", False)),
                max_output_tokens=max_output_tokens,
            ):
                chunks.append(str(chunk))
        finally:
            reset_current_team(_token)
        return "".join(chunks).strip()


async def handle_swarm(bot: "KraabUserbot", message: Message) -> None:
    """
    Запуск роевого обсуждения с поддержкой именованных команд и делегирования.

    Формат:
      !swarm <тема>                   — дефолтный room (аналитик→критик→интегратор)
      !swarm <команда> <тема>         — именованная команда (traders/coders/analysts/creative)
      !swarm teams                    — список доступных команд
      !swarm loop [N] <тема>          — итеративный режим (дефолтная команда)
      !swarm <команда> loop [N] <тема>— итеративный режим для команды
    """
    from ...core.swarm import AgentRoom
    from ...core.swarm_bus import (
        TEAM_REGISTRY,
        list_teams,
        resolve_team_name,
        swarm_bus,
    )
    from ...core.swarm_memory import swarm_memory

    args = bot._get_command_args(message).strip()
    if not args:
        # Показываем inline-кнопки выбора команды вместо текстовой справки
        await message.reply(
            "🐝 **Swarm** — выбери команду или укажи тему:\n"
            "`!swarm <тема>` — базовый room\n"
            "`!swarm <команда> <тема>` — именованная команда\n\n"
            "Быстрый выбор команды:",
            reply_markup=build_swarm_team_buttons(),
        )
        return

    # !swarm teams — справка
    if args.lower() in {"teams", "команды", "help"}:
        await message.reply(list_teams())
        return

    # !swarm task — task board operations
    if args.lower().startswith("task"):
        from ...core.swarm_task_board import swarm_task_board

        task_tokens = args.split(maxsplit=2)
        sub = task_tokens[1].lower() if len(task_tokens) > 1 else "board"

        if sub == "board":
            summary = swarm_task_board.get_board_summary()
            lines = ["📋 **Swarm Task Board**"]
            for status_name, count in sorted(summary.get("by_status", {}).items()):
                emoji = {
                    "pending": "⏳",
                    "in_progress": "🔄",
                    "done": "✅",
                    "failed": "❌",
                    "blocked": "🚫",
                }.get(status_name, "•")
                lines.append(f"  {emoji} {status_name}: {count}")
            if summary.get("by_team"):
                lines.append("**По командам:**")
                for team_name, count in sorted(summary["by_team"].items()):
                    lines.append(f"  {team_name}: {count}")
            lines.append(f"Всего: {summary.get('total', 0)}")
            await message.reply("\n".join(lines))
            return

        if sub == "list":
            team_filter = task_tokens[2].strip().lower() if len(task_tokens) > 2 else None
            tasks = swarm_task_board.list_tasks(team=team_filter, limit=10)
            if not tasks:
                await message.reply("📋 Task board пуст.")
                return
            lines = ["📋 **Tasks:**"]
            for t in tasks:
                emoji = {"pending": "⏳", "in_progress": "🔄", "done": "✅", "failed": "❌"}.get(
                    t.status, "•"
                )
                lines.append(f"{emoji} `{t.task_id[:8]}` [{t.team}] {t.title} ({t.priority})")
            await message.reply("\n".join(lines))
            return

        if sub == "create":
            # !swarm task create <team> <title>
            create_parts = (task_tokens[2] if len(task_tokens) > 2 else "").split(maxsplit=1)
            if len(create_parts) < 2:
                raise UserInputError(user_message="❌ Формат: `!swarm task create <team> <title>`")
            team_name = create_parts[0].lower()
            title = create_parts[1]
            task = swarm_task_board.create_task(
                team=team_name,
                title=title,
                description="",
                priority="medium",
                created_by="owner",
            )
            await message.reply(f"✅ Task `{task.task_id[:8]}` создан для **{team_name}**: {title}")
            return

        if sub == "done":
            tid = task_tokens[2].strip() if len(task_tokens) > 2 else ""
            if not tid:
                raise UserInputError(user_message="❌ Формат: `!swarm task done <task_id>`")
            # Ищем по prefix
            all_tasks = swarm_task_board.list_tasks(limit=200)
            match = next((t for t in all_tasks if t.task_id.startswith(tid)), None)
            if not match:
                raise UserInputError(user_message=f"❌ Task `{tid}` не найден")
            swarm_task_board.complete_task(match.task_id, result="completed by owner")
            await message.reply(f"✅ Task `{match.task_id[:8]}` → done")
            return

        if sub == "fail":
            tid = task_tokens[2].strip() if len(task_tokens) > 2 else ""
            if not tid:
                raise UserInputError(user_message="❌ Формат: `!swarm task fail <task_id>`")
            all_tasks = swarm_task_board.list_tasks(limit=200)
            match = next((t for t in all_tasks if t.task_id.startswith(tid)), None)
            if not match:
                raise UserInputError(user_message=f"❌ Task `{tid}` не найден")
            swarm_task_board.fail_task(match.task_id, reason="failed by owner")
            await message.reply(f"❌ Task `{match.task_id[:8]}` → failed")
            return

        if sub == "assign":
            # !swarm task assign <id> — помечает in_progress и подсказывает launch
            tid = task_tokens[2].strip() if len(task_tokens) > 2 else ""
            if not tid:
                raise UserInputError(user_message="❌ Формат: `!swarm task assign <task_id>`")
            all_tasks = swarm_task_board.list_tasks(limit=200)
            match = next((t for t in all_tasks if t.task_id.startswith(tid)), None)
            if not match:
                raise UserInputError(user_message=f"❌ Task `{tid}` не найден")
            if match.status == "done":
                raise UserInputError(user_message=f"Task `{match.task_id[:8]}` уже завершён")
            swarm_task_board.update_task(match.task_id, status="in_progress")
            await message.reply(
                f"🔄 Task `{match.task_id[:8]}` → **in_progress**\n\n"
                f"Для запуска swarm round:\n"
                f"`!swarm {match.team} {match.title}`"
            )
            return

        if sub in {"status", "show", "get"}:
            tid = task_tokens[2].strip() if len(task_tokens) > 2 else ""
            if not tid:
                raise UserInputError(user_message="❌ Формат: `!swarm task status <task_id>`")
            all_tasks = swarm_task_board.list_tasks(limit=200)
            match = next((t for t in all_tasks if t.task_id.startswith(tid)), None)
            if not match:
                raise UserInputError(user_message=f"❌ Task `{tid}` не найден")
            emoji = {"pending": "⏳", "in_progress": "🔄", "done": "✅", "failed": "❌"}.get(
                match.status, "•"
            )
            lines = [
                f"{emoji} **Task: {match.task_id[:12]}**",
                f"Team: {match.team}",
                f"Title: {match.title}",
                f"Status: {match.status}",
                f"Priority: {match.priority}",
                f"Created by: {match.created_by}",
                f"Created: {match.created_at}",
                f"Updated: {match.updated_at}",
            ]
            if match.result:
                lines.append(f"Result: {match.result[:200]}")
            if match.parent_task_id:
                lines.append(f"Parent: {match.parent_task_id[:12]}")
            await message.reply("\n".join(lines))
            return

        if sub == "priority":
            # !swarm task priority <id> <level>
            parts = (task_tokens[2] if len(task_tokens) > 2 else "").split(maxsplit=1)
            if len(parts) < 2:
                raise UserInputError(
                    user_message="❌ Формат: `!swarm task priority <id> low|medium|high|critical`"
                )
            tid, level = parts[0].strip(), parts[1].strip().lower()
            if level not in {"low", "medium", "high", "critical"}:
                raise UserInputError(
                    user_message="❌ Приоритеты: `low`, `medium`, `high`, `critical`"
                )
            all_tasks = swarm_task_board.list_tasks(limit=200)
            match = next((t for t in all_tasks if t.task_id.startswith(tid)), None)
            if not match:
                raise UserInputError(user_message=f"❌ Task `{tid}` не найден")
            swarm_task_board.update_task(match.task_id, priority=level)
            await message.reply(f"📌 Task `{match.task_id[:8]}` → priority **{level}**")
            return

        if sub == "count":
            # Быстрый count по статусам
            summary = swarm_task_board.get_board_summary()
            by_status = summary.get("by_status", {})
            total = summary.get("total", 0)
            parts = [f"{s}: {c}" for s, c in sorted(by_status.items())]
            await message.reply(f"📊 Tasks: {total} ({', '.join(parts)})")
            return

        if sub == "clear":
            # Очищает done/failed tasks из board
            all_tasks = swarm_task_board.list_tasks(limit=500)
            cleared = 0
            for t in all_tasks:
                if t.status in {"done", "failed"}:
                    # Удаляем через update на "archived" статус — FIFO cleanup потом удалит
                    swarm_task_board.update_task(t.task_id, status="done")
                    cleared += 1
            swarm_task_board.cleanup_old()
            await message.reply(f"🧹 Очищено {cleared} done/failed tasks")
            return

        raise UserInputError(
            user_message=(
                "📋 Task Board:\n"
                "`!swarm task board` — сводка\n"
                "`!swarm task list [team]` — список задач\n"
                "`!swarm task create <team> <title>` — создать\n"
                "`!swarm task done <id>` — завершить\n"
                "`!swarm task fail <id>` — отметить как failed\n"
                "`!swarm task assign <id>` — запустить swarm round для задачи\n"
                "`!swarm task status <id>` — детальный view\n"
                "`!swarm task priority <id> <level>` — изменить приоритет\n"
                "`!swarm task count` — быстрый count по статусам\n"
                "`!swarm task clear` — очистить done/failed"
            )
        )

    # !swarm summary — сводка текущей сессии (задачи, артефакты, раунды)
    if args.lower() in {"summary", "сводка"}:
        from ...core.swarm_artifact_store import swarm_artifact_store
        from ...core.swarm_task_board import swarm_task_board

        board = swarm_task_board.get_board_summary()
        by_status = board.get("by_status", {})
        total_tasks = board.get("total", 0)

        arts = swarm_artifact_store.list_artifacts(limit=200)
        total_rounds = len(arts)
        # суммируем время всех раундов
        total_duration = sum(a.get("duration_sec", 0) for a in arts)

        # команды, участвовавшие в сессии
        teams_seen: set[str] = {a.get("team", "?") for a in arts}

        lines = ["📊 **Swarm Summary**", ""]
        # --- задачи ---
        lines.append("**Задачи:**")
        status_emoji = {
            "done": "✅",
            "pending": "⏳",
            "in_progress": "🔄",
            "failed": "❌",
            "blocked": "🚫",
        }
        for st in ("done", "in_progress", "pending", "failed", "blocked"):
            cnt = by_status.get(st, 0)
            if cnt:
                lines.append(f"  {status_emoji.get(st, '•')} {st}: {cnt}")
        lines.append(f"  Итого задач: {total_tasks}")
        lines.append("")
        # --- артефакты ---
        lines.append("**Артефакты:**")
        lines.append(f"  📦 Раундов сохранено: {total_rounds}")
        if total_duration:
            lines.append(f"  ⏱ Суммарное время: {total_duration:.0f}с")
        if teams_seen:
            lines.append(f"  🐝 Команды: {', '.join(sorted(teams_seen))}")

        await message.reply("\n".join(lines))
        return

    # !swarm artifacts [team] — список артефактов
    if args.lower().startswith("artifacts") or args.lower().startswith("артефакт"):
        from ...core.swarm_artifact_store import swarm_artifact_store

        art_tokens = args.split(maxsplit=1)
        team_filter = art_tokens[1].strip().lower() if len(art_tokens) > 1 else None
        arts = swarm_artifact_store.list_artifacts(team=team_filter, limit=10)
        if not arts:
            await message.reply("📦 Артефактов пока нет.")
            return
        lines = ["📦 **Swarm Artifacts:**"]
        for a in arts:
            ts = a.get("timestamp_iso", "?")[:16]
            team = a.get("team", "?")
            topic = (a.get("topic") or "?")[:50]
            dur = a.get("duration_sec", 0)
            lines.append(f"  [{team}] {topic} ({dur:.0f}s, {ts})")
        await message.reply("\n".join(lines))
        return

    # !swarm report [team] — последние markdown reports
    if args.lower().startswith("report") or args.lower().startswith("отчёт"):
        from pathlib import Path

        report_dir = Path.home() / ".openclaw" / "krab_runtime_state" / "reports"
        if not report_dir.exists():
            await message.reply("📄 Отчётов пока нет.")
            return
        files = sorted(report_dir.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)[:5]
        if not files:
            await message.reply("📄 Отчётов пока нет.")
            return
        lines = ["📄 **Последние отчёты:**"]
        for f in files:
            name = f.name.replace(".md", "")
            size_kb = f.stat().st_size / 1024
            lines.append(f"  `{name}` ({size_kb:.1f} KB)")
        lines.append(f"\nВсего: {len(list(report_dir.glob('*.md')))} отчётов")
        await message.reply("\n".join(lines))
        return

    # !swarm listen on/off — управление team listeners
    if args.lower().startswith("listen"):
        from ...core.swarm_team_listener import is_listeners_enabled, set_listeners_enabled

        listen_tokens = args.split(maxsplit=1)
        if len(listen_tokens) > 1:
            val = listen_tokens[1].strip().lower()
            if val in {"on", "1", "true", "yes"}:
                set_listeners_enabled(True)
                await message.reply(
                    "🎧 Team listeners: **ON**\nTeam-аккаунты отвечают в ЛС и на mention."
                )
            elif val in {"off", "0", "false", "no"}:
                set_listeners_enabled(False)
                await message.reply("🔇 Team listeners: **OFF**\nTeam-аккаунты молчат.")
            else:
                await message.reply("❌ Формат: `!swarm listen on` или `!swarm listen off`")
        else:
            status = "ON ✅" if is_listeners_enabled() else "OFF 🔇"
            await message.reply(f"🎧 Team listeners: **{status}**")
        return

    # !swarm status deep — комплексная диагностика swarm-системы (owner-only)
    if args.lower() in {"status deep", "status"}:
        access_profile = bot._get_access_profile(message.from_user)
        if access_profile.level != AccessLevel.OWNER:
            raise UserInputError(user_message="🔒 `!swarm status deep` доступен только владельцу.")
        # Lazy import через namespace модуля — чтобы тесты могли patch'ить
        # `command_handlers._swarm_status_deep_report` (см. test_swarm_status_deep).
        from .. import command_handlers as _ch

        report = await _ch._swarm_status_deep_report()
        await message.reply(report)
        return

    # !swarm info <team> — детальная инфо о команде
    if args.lower().startswith("info"):
        info_tokens = args.split(maxsplit=1)
        team_arg = info_tokens[1].strip().lower() if len(info_tokens) > 1 else ""
        if not team_arg:
            raise UserInputError(user_message="❌ Формат: `!swarm info <team>`")
        resolved = resolve_team_name(team_arg)
        if not resolved:
            raise UserInputError(user_message=f"❌ Команда `{team_arg}` не найдена")
        roles = TEAM_REGISTRY.get(resolved, [])
        from ...core.swarm_artifact_store import swarm_artifact_store
        from ...core.swarm_task_board import swarm_task_board

        tasks = swarm_task_board.list_tasks(team=resolved, limit=5)
        arts = swarm_artifact_store.list_artifacts(team=resolved, limit=3)
        lines = [f"🐝 **Команда: {resolved}**", ""]
        lines.append("**Роли:**")
        for r in roles:
            lines.append(f"  {r.get('emoji', '•')} {r.get('title', r.get('name', '?'))}")
        if tasks:
            lines.append(f"\n**Задачи ({len(tasks)}):**")
            for t in tasks:
                emoji = {"pending": "⏳", "in_progress": "🔄", "done": "✅", "failed": "❌"}.get(
                    t.status, "•"
                )
                lines.append(f"  {emoji} {t.title[:50]} ({t.status})")
        if arts:
            lines.append(f"\n**Артефакты ({len(arts)}):**")
            for a in arts:
                lines.append(
                    f"  📦 {(a.get('topic') or '?')[:40]} ({a.get('duration_sec', 0):.0f}s)"
                )
        await message.reply("\n".join(lines))
        return

    # !swarm stats — сводная статистика по всем командам
    if args.lower().startswith("stats") or args.lower().startswith("стат"):
        from ...core.swarm_artifact_store import swarm_artifact_store
        from ...core.swarm_task_board import swarm_task_board
        from ...core.swarm_team_listener import is_listeners_enabled

        board = swarm_task_board.get_board_summary()
        arts = swarm_artifact_store.list_artifacts(limit=100)
        lines = [
            "📊 **Swarm Stats**",
            f"Tasks: {board.get('total', 0)} (done: {board.get('by_status', {}).get('done', 0)})",
            f"Artifacts: {len(arts)}",
            f"Listeners: {'ON' if is_listeners_enabled() else 'OFF'}",
        ]
        if board.get("by_team"):
            lines.append("**По командам:**")
            for team_name, count in sorted(board["by_team"].items()):
                team_arts = len([a for a in arts if a.get("team", "").lower() == team_name.lower()])
                lines.append(f"  {team_name}: {count} tasks, {team_arts} artifacts")
        await message.reply("\n".join(lines))
        return

    # !swarm memory [team] — история прогонов
    if args.lower().startswith("memory") or args.lower().startswith("память"):
        mem_tokens = args.split(maxsplit=1)
        if len(mem_tokens) > 1:
            mem_arg = mem_tokens[1].strip().lower()
            if mem_arg == "clear":
                teams = swarm_memory.all_teams()
                total = sum(swarm_memory.clear_team(t) for t in teams)
                await message.reply(f"🧹 Очищена память всех команд ({total} записей)")
            else:
                team = resolve_team_name(mem_arg) or mem_arg
                await message.reply(swarm_memory.format_history(team, count=7))
        else:
            # Показать сводку по всем командам
            teams = swarm_memory.all_teams()
            if not teams:
                await message.reply("🧠 Память свёрма пуста — ещё не было прогонов.")
            else:
                lines = ["🧠 **Память свёрма:**\n"]
                for t in teams:
                    stats = swarm_memory.get_team_stats(t)
                    lines.append(
                        f"**{t}** — {stats['total_runs']} прогонов, "
                        f"последний: {stats.get('last_run', '—')}"
                    )
                lines.append("\n`!swarm memory <команда>` — подробная история")
                await message.reply("\n".join(lines))
        return

    # !swarm schedule <team> <interval> <topic> [--workflow research|report] — создание рекуррентной задачи
    if args.lower().startswith("schedule") or args.lower().startswith("расписание"):
        import re as _re

        from ...core.swarm_scheduler import WorkflowType, parse_interval, swarm_scheduler

        # Извлекаем --workflow флаг до разбора позиционных аргументов
        workflow_match = _re.search(r"--workflow\s+(\S+)", args)
        workflow_type = WorkflowType.STANDARD
        if workflow_match:
            wf_raw = workflow_match.group(1).lower()
            try:
                workflow_type = WorkflowType(wf_raw)
            except ValueError:
                valid_wf = ", ".join(w.value for w in WorkflowType)
                await message.reply(f"❌ Неизвестный workflow: `{wf_raw}`\nДопустимые: {valid_wf}")
                return
            # Убираем --workflow ... из args для разбора позиционных аргументов
            args_clean = _re.sub(r"--workflow\s+\S+", "", args).strip()
        else:
            args_clean = args

        sched_tokens = args_clean.split(maxsplit=3)  # schedule traders 4h <BTC тема>
        if len(sched_tokens) < 4:
            await message.reply(
                "📅 Формат: `!swarm schedule <команда> <интервал> <тема> [--workflow research|report]`\n"
                "Примеры:\n"
                "  `!swarm schedule traders 4h анализ BTC`\n"
                "  `!swarm schedule analysts 6h крипторынок --workflow research`\n"
                "  `!swarm schedule coders 1d прогресс --workflow report`\n"
                "Интервалы: `30m`, `1h`, `4h`, `1d`"
            )
            return
        sched_team = resolve_team_name(sched_tokens[1])
        if not sched_team:
            await message.reply(
                f"❌ Команда '{sched_tokens[1]}' не найдена. Доступны: {', '.join(TEAM_REGISTRY)}"
            )
            return
        try:
            interval = parse_interval(sched_tokens[2])
        except ValueError as e:
            await message.reply(f"❌ {e}")
            return
        sched_topic = sched_tokens[3] if len(sched_tokens) > 3 else "общий анализ"
        try:
            job = swarm_scheduler.add_job(
                team=sched_team,
                topic=sched_topic,
                interval_sec=interval,
                workflow_type=workflow_type,
            )
            interval_h = interval / 3600
            interval_str = f"{interval_h:.1f}ч" if interval_h >= 1 else f"{interval // 60}мин"
            wf_emoji = {"standard": "🔄", "research": "🔬", "report": "📊"}.get(
                job.workflow_type, "🔄"
            )
            await message.reply(
                f"📅 Задача создана!\n"
                f"ID: `{job.job_id}`\n"
                f"Команда: **{sched_team}** каждые {interval_str}\n"
                f"Тема: _{sched_topic}_\n"
                f"Workflow: {wf_emoji} `{job.workflow_type}`"
            )
        except (RuntimeError, ValueError) as e:
            await message.reply(f"❌ {e}")
        return

    # !swarm unschedule <id> — удаление задачи
    if args.lower().startswith("unschedule") or args.lower().startswith("отмена"):
        from ...core.swarm_scheduler import swarm_scheduler

        unsched_tokens = args.split(maxsplit=1)
        if len(unsched_tokens) < 2:
            await message.reply("❌ Укажи ID задачи: `!swarm unschedule <id>`")
            return
        job_id = unsched_tokens[1].strip()
        if swarm_scheduler.remove_job(job_id):
            await message.reply(f"✅ Задача `{job_id}` удалена")
        else:
            await message.reply(f"❌ Задача `{job_id}` не найдена")
        return

    # !swarm jobs — список рекуррентных задач
    if args.lower() in {"jobs", "задачи", "schedule"}:
        from ...core.swarm_scheduler import swarm_scheduler

        await message.reply(swarm_scheduler.format_jobs())
        return

    # !swarm channels — статус swarm-групп
    if args.lower() in {"channels", "группы", "каналы"}:
        from ...core.swarm_channels import swarm_channels

        await message.reply(swarm_channels.format_status())
        return

    # !swarm setup — создание Forum-группы с топиками
    if args.lower() in {"setup", "настройка", "форум"}:
        from ...core.swarm_channels import swarm_channels

        chat = message.chat
        # Группы/супергруппы всегда имеют отрицательный chat_id
        is_group = chat.id < 0

        if is_group:
            # Вызвано в группе — пробуем создать топики напрямую
            msg = await message.reply("📡 Создаю топики...")
            try:
                topic_ids = await swarm_channels.setup_topics_in_existing(chat.id)
                if topic_ids:
                    topics_text = "\n".join(
                        f"  **{team}** → topic `{tid}`" for team, tid in topic_ids.items()
                    )
                    await msg.edit_text(
                        f"✅ **Forum Topics настроены!**\n\n{topics_text}\n\n"
                        f"Свёрм транслирует раунды в топики.\n"
                        f"Пиши в топик во время раунда — Краб подхватит как директиву."
                    )
                else:
                    await msg.edit_text(
                        "⚠️ Не удалось создать топики.\n"
                        "Убедись что **Topics** включены: Group Info → Edit → Topics.\n"
                        "После включения повтори `!swarm setup`."
                    )
            except Exception as exc:  # noqa: BLE001
                await msg.edit_text(
                    f"⚠️ Ошибка создания топиков: `{exc}`\n\n"
                    f"Включи **Topics** в настройках группы и повтори."
                )
        else:
            # Вызвано в личке — создаём новую группу
            msg = await message.reply("📡 Создаю группу **🐝 Krab Swarm**...")
            try:
                result = await swarm_channels.setup_forum()
                if result.get("topic_ids"):
                    topics_text = "\n".join(
                        f"  **{team}** → topic `{tid}`" for team, tid in result["topic_ids"].items()
                    )
                    await msg.edit_text(
                        f"✅ **Krab Swarm Forum создан!**\n\n"
                        f"Chat ID: `{result['chat_id']}`\n{topics_text}\n\n"
                        f"Свёрм транслирует раунды в топики."
                    )
                else:
                    await msg.edit_text(
                        f"⚠️ **Группа создана** (Chat ID: `{result['chat_id']}`)\n\n"
                        f"Включи **Topics**: Group Info → Edit → Topics\n"
                        f"Затем набери `!swarm setup` **в этой группе**."
                    )
            except Exception as exc:  # noqa: BLE001
                await msg.edit_text(f"❌ Ошибка: {exc}")
        return

    # !swarm setchat <team> — привязать текущую группу к команде
    if args.lower().startswith("setchat") or args.lower().startswith("привязать"):
        from ...core.swarm_channels import swarm_channels

        setchat_tokens = args.split(maxsplit=1)
        if len(setchat_tokens) < 2:
            await message.reply(
                "📡 Формат: `!swarm setchat <команда>`\n"
                "Вызывай эту команду **в группе**, которую хочешь привязать.\n"
                "Пример: в группе «Трейдеры» напиши `!swarm setchat traders`"
            )
            return
        team = resolve_team_name(setchat_tokens[1].strip())
        if not team:
            await message.reply(
                f"❌ Команда '{setchat_tokens[1]}' не найдена. Доступны: {', '.join(TEAM_REGISTRY)}"
            )
            return
        chat_id = message.chat.id
        swarm_channels.register_team_chat(team, chat_id)
        await message.reply(f"📡 Группа привязана к команде **{team}**\nChat ID: `{chat_id}`")
        return

    # !swarm research <тема> — research pipeline с обязательным web_search.
    # После завершения research hook self-reflection создаёт follow-up задачи
    # в swarm_task_board (или reminders_queue для time-based триггеров).
    if args.lower().startswith("research") or args.lower().startswith("исследование"):
        from ...core.swarm_research_pipeline import SwarmResearchPipeline

        research_tokens = args.split(maxsplit=1)
        if len(research_tokens) < 2 or not research_tokens[1].strip():
            raise UserInputError(
                user_message=(
                    "🔬 Укажи тему исследования.\nПример: `!swarm research тренды AI 2025`"
                )
            )
        raw_topic = research_tokens[1].strip()
        msg = await message.reply(
            f"🔬 **Research Pipeline** [analysts]\nТема: `{raw_topic}`\n_web_search обязателен_"
        )
        try:
            chat_id = str(message.chat.id)
            user = message.from_user
            access_profile = bot._get_access_profile(user)
            is_allowed_sender = bot._is_allowed_sender(user)
            system_prompt = bot._build_system_prompt_for_sender(
                is_allowed_sender=is_allowed_sender,
                access_level=access_profile.level,
            )

            def _router_factory(tn: str) -> _AgentRoomRouterAdapter:
                return _AgentRoomRouterAdapter(
                    chat_id=f"swarm:{chat_id}:{tn}",
                    system_prompt=system_prompt,
                    team_name=tn,
                )

            # Self-reflection singletons (optional — graceful degradation).
            # openclaw_client импортирован на уровне модуля; task_board подтягиваем локально.
            _reflect_board = None
            try:
                from ...core.swarm_task_board import swarm_task_board as _reflect_board
            except Exception as _reflect_exc:  # noqa: BLE001
                logger.warning(
                    "swarm_research_reflect_wiring_failed",
                    error=str(_reflect_exc),
                )

            pipeline = SwarmResearchPipeline()
            result_text = await pipeline.run(
                raw_topic,
                router_factory=_router_factory,
                swarm_bus=swarm_bus,
                openclaw_client=openclaw_client,
                task_board=_reflect_board,
                reflect=True,
            )
            chunks = _split_text(result_text)
            await msg.edit(chunks[0])
            for part in chunks[1:]:
                await message.reply(part)
        except Exception as e:
            logger.error("swarm_research_error", error=str(e), exc_info=True)
            safe_err = str(e).replace("`", "'")[:500]
            try:
                await msg.edit(
                    f"❌ Ошибка Research: {safe_err}" if safe_err else "❌ Ошибка Research"
                )
            except Exception:  # noqa: BLE001
                pass
        return

    # Парсим: [team_name] [loop [N]] <topic>
    tokens = args.split()
    team_key: str | None = None
    loop_mode = False
    loop_rounds = 2

    # Проверяем первый токен на имя команды
    maybe_team = resolve_team_name(tokens[0])
    if maybe_team:
        team_key = maybe_team
        tokens = tokens[1:]

    # Проверяем loop
    if tokens and tokens[0].lower() == "loop":
        loop_mode = True
        tokens = tokens[1:]
        if tokens and tokens[0].isdigit():
            loop_rounds = min(int(tokens[0]), int(getattr(config, "SWARM_LOOP_MAX_ROUNDS", 3) or 3))
            tokens = tokens[1:]

    topic = " ".join(tokens).strip()
    if not topic:
        raise UserInputError(user_message="🐝 Укажи тему! Пример: `!swarm traders анализируй BTC`")

    team_label = f" [{team_key}]" if team_key else ""
    mode_label = f" loop×{loop_rounds}" if loop_mode else ""
    msg = await message.reply(f"🐝 **Запуск Swarm{team_label}{mode_label}...**\nТема: `{topic}`")

    try:
        chat_id = str(message.chat.id)
        user = message.from_user
        access_profile = bot._get_access_profile(user)
        is_allowed_sender = bot._is_allowed_sender(user)
        system_prompt = bot._build_system_prompt_for_sender(
            is_allowed_sender=is_allowed_sender,
            access_level=access_profile.level,
        )

        def router_factory(team_name: str) -> "_AgentRoomRouterAdapter":
            """Создаёт адаптер роутера для указанной команды."""
            return _AgentRoomRouterAdapter(
                chat_id=f"swarm:{chat_id}:{team_name}",
                system_prompt=system_prompt,
                team_name=team_name,
            )

        roles = TEAM_REGISTRY.get(team_key) if team_key else None
        room = AgentRoom(roles=roles)
        router = router_factory(team_key or "default")

        if loop_mode:
            result_text = await room.run_loop(
                topic,
                router,
                rounds=loop_rounds,
                max_rounds=int(getattr(config, "SWARM_LOOP_MAX_ROUNDS", 3) or 3),
                next_round_clip=int(getattr(config, "SWARM_LOOP_NEXT_ROUND_CLIP", 4000) or 4000),
                _bus=swarm_bus,
                _router_factory=router_factory,
                _team_name=team_key or "default",
            )
        else:
            result_text = await room.run_round(
                topic,
                router,
                _bus=swarm_bus,
                _router_factory=router_factory,
                _team_name=team_key or "default",
            )

        chunks = _split_text(result_text)
        await msg.edit(chunks[0])
        for part in chunks[1:]:
            await message.reply(part)

    except Exception as e:
        logger.error("swarm_error", error=str(e), error_type=type(e).__name__, exc_info=True)
        safe_err = str(e).replace("`", "'")[:500]
        try:
            await msg.edit(f"❌ Ошибка Swarm: {safe_err}" if safe_err else "❌ Ошибка Swarm")
        except Exception:  # noqa: BLE001
            pass


__all__ = ["_AgentRoomRouterAdapter", "handle_swarm"]
