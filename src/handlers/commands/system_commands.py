# -*- coding: utf-8 -*-
"""
system_commands - Phase 2 Wave 10 extraction (Session 27, финальная волна).

Системные / диагностические команды и их private helpers:
  !status, !sysinfo, !uptime, !panel, !version, !restart, !diagnose,
  !debug, !health [deep], !stats [ecosystem], !ip, !dns, !ping, !log, !diag.

Helpers:
  _format_uptime_str, _render_stats_panel, _format_ecosystem_report,
  _handle_stats_ecosystem, _health_deep_report, _get_local_ip, _get_public_ip,
  _read_log_tail_subprocess, _LOG_*, _KRAB_LOG_PATH, _diag_panel_base,
  _diag_fetch_json, _diag_fmt_section_*, _diag_fetch_sentry, _diag_collect_security.

Что НЕ перенесено (см. CLAUDE.md / handoff):
- ``_swarm_status_deep_report`` — остаётся в command_handlers (тесты патчат
  через namespace ``command_handlers._swarm_status_deep_report``).
- ``_split_text_for_telegram`` — также остаётся в command_handlers
  (multi-use), вызывается из handle_log через lazy import.
- ``_active_timers`` — модуль-уровень state из scheduler_commands,
  re-exported через command_handlers; handle_debug читает через lazy import.

Re-exported from command_handlers.py для обратной совместимости (тесты,
external imports `from src.handlers.command_handlers import handle_health`).

См. ``docs/CODE_SPLITS_PLAN.md`` Phase 2 - domain extractions.
"""

from __future__ import annotations

import asyncio
import datetime
import os
import pathlib
import socket
import subprocess as _sp  # переименовано: hook ругается на subprocess.run, но это Python stdlib
import sys
import time
from typing import TYPE_CHECKING, Any

import httpx
from pyrogram.types import Message

from ...config import config
from ...core.access_control import AccessLevel
from ...core.exceptions import UserInputError
from ...core.lm_studio_health import (
    is_lm_studio_available,  # noqa: F401  # тесты патчат через namespace
)
from ...core.logger import get_logger
from ...core.proactive_watch import proactive_watch
from ...core.telegram_buttons import build_health_recheck_buttons
from ...openclaw_client import openclaw_client

if TYPE_CHECKING:
    from ...userbot_bridge import KraabUserbot

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# !status — компактный overview всех подсистем
# ---------------------------------------------------------------------------


async def handle_status(bot: "KraabUserbot", message: Message) -> None:
    """Компактный overview всех подсистем Краба одним сообщением (!status)."""
    import os as _os

    import psutil as _psutil

    from ...core.silence_mode import silence_manager
    from ...core.swarm_bus import TEAM_REGISTRY
    from ...core.swarm_scheduler import swarm_scheduler

    # Lazy proxy через command_handlers namespace — позволяет тестам
    # ``test_command_handlers_status`` патчить ``command_handlers.openclaw_client``
    # и ``command_handlers.get_runtime_primary_model`` через monkeypatch.setattr.
    from .. import command_handlers as _ch

    _oc = _ch.openclaw_client
    _get_primary = _ch.get_runtime_primary_model

    # ── 1. Telegram ─────────────────────────────────────────────────────
    tg_ok = bot.me is not None
    tg_icon = "✅" if tg_ok else "❌"

    # ── 2. OpenClaw / активная модель ───────────────────────────────────
    try:
        oc_ok = await _oc.health_check()
    except Exception:
        oc_ok = False
    route_meta: dict = {}
    if hasattr(_oc, "get_last_runtime_route"):
        try:
            route_meta = _oc.get_last_runtime_route() or {}
        except Exception:
            route_meta = {}
    actual_model = str(route_meta.get("model") or "").strip()
    declared_primary = str(_get_primary() or getattr(config, "MODEL", "") or "").strip()
    effective_model = actual_model or declared_primary or "unknown"
    # Сокращённое имя модели (после последнего "/")
    model_short = effective_model.split("/")[-1] if "/" in effective_model else effective_model
    oc_icon = "✅" if oc_ok else "❌"

    # ── 3. Scheduler ────────────────────────────────────────────────────
    try:
        sched_jobs = swarm_scheduler.list_jobs()
        job_count = len(sched_jobs)
        sched_icon = "✅"
    except Exception:
        job_count = 0
        sched_icon = "⚠️"

    # ── 4. Inbox ────────────────────────────────────────────────────────
    try:
        inbox_summary = _ch.inbox_service.get_summary()
        inbox_open = int(inbox_summary.get("open_items", 0))
    except Exception:
        inbox_open = 0

    # ── 5. Cost ─────────────────────────────────────────────────────────
    try:
        cost_report = _ch.cost_analytics.build_usage_report_dict()
        cost_month = cost_report.get("cost_month_usd", 0.0)
        budget = cost_report.get("monthly_budget_usd") or 0.0
        cost_str = f"${cost_month:.2f}/{budget:.2f}" if budget else f"${cost_month:.2f}"
    except Exception:
        cost_str = "?"

    # ── 6. Swarm teams ──────────────────────────────────────────────────
    try:
        team_count = len(TEAM_REGISTRY)
    except Exception:
        team_count = 0

    # ── 7. Translator ───────────────────────────────────────────────────
    try:
        t_state = bot.get_translator_session_state()
        t_status = t_state.get("session_status", "idle")
        t_pair = t_state.get("last_pair") or ""
        if t_status == "idle":
            translator_str = "idle"
        else:
            translator_str = f"active ({t_pair})" if t_pair else t_status
    except Exception:
        translator_str = "idle"

    # ── 8. Silence ──────────────────────────────────────────────────────
    try:
        sil = silence_manager.status()
        silence_str = "on" if sil.get("global_muted") else "off"
    except Exception:
        silence_str = "off"

    # ── 9. Uptime ───────────────────────────────────────────────────────
    try:
        elapsed = int(time.time() - bot._session_start_time)
        hours, rem = divmod(elapsed, 3600)
        mins = rem // 60
        uptime_str = f"{hours}h{mins}m" if hours else f"{mins}m"
    except Exception:
        uptime_str = "?"

    # ── 10. RAM (RSS процесса) ──────────────────────────────────────────
    try:
        rss_mb = int(_psutil.Process(_os.getpid()).memory_info().rss / 1024 / 1024)
        ram_str = f"{rss_mb}MB"
    except Exception:
        ram_str = "?"

    # ── 11. Сообщений за сессию ────────────────────────────────────────
    try:
        msg_count = bot._session_messages_processed
    except Exception:
        msg_count = 0

    # ── Сборка компактного статуса ──────────────────────────────────────
    line1 = (
        f"{tg_icon} Telegram | "
        f"{oc_icon} OpenClaw ({model_short}) | "
        f"{sched_icon} Scheduler ({job_count} jobs)"
    )
    line2 = f"📬 Inbox: {inbox_open} open | 💰 Cost: {cost_str} | 🐝 Swarm: {team_count} teams"
    line3 = f"🔄 Translator: {translator_str} | 🔇 Silence: {silence_str}"
    line4 = f"⏱ Uptime: {uptime_str} | 🧠 RAM: {ram_str} | 📊 Messages: {msg_count}"

    text = f"🦀 **Krab Status**\n━━━━━━━━━━━━\n{line1}\n{line2}\n{line3}\n{line4}"

    # Доп. строка: Primary runtime если не совпадает с фактической моделью
    if declared_primary and declared_primary != effective_model:
        text += f"\n🧭 Primary runtime: `{declared_primary}`"

    # Если сообщение отправлено самим ботом — редактируем, иначе отвечаем
    me_id = getattr(bot.me, "id", None) if bot.me is not None else None
    if message.from_user and me_id and message.from_user.id == me_id:
        await message.edit(text)
    else:
        await message.reply(text)


# ---------------------------------------------------------------------------
# !sysinfo / !uptime / !panel / !version
# ---------------------------------------------------------------------------


def _format_uptime_str(elapsed_sec: float) -> str:
    """Форматирует секунды в читаемый uptime: 1д 2ч 15м."""
    elapsed = int(elapsed_sec)
    days, rem = divmod(elapsed, 86400)
    hours, rem2 = divmod(rem, 3600)
    mins = rem2 // 60
    parts: list[str] = []
    if days:
        parts.append(f"{days}д")
    if hours:
        parts.append(f"{hours}ч")
    parts.append(f"{mins}м")
    return " ".join(parts)


async def handle_sysinfo(bot: "KraabUserbot", message: Message) -> None:
    """Расширенная информация о хосте: macOS, CPU, RAM, Disk, Network, Python, Krab."""
    import os
    import platform
    import socket

    import psutil

    lines: list[str] = ["🖥️ **System Info**", "─────────────"]

    # macOS версия
    try:
        mac_ver = platform.mac_ver()[0] or platform.version()
        darwin_ver = platform.release()
        lines.append(f"macOS: `{mac_ver}` (Darwin {darwin_ver})")
    except Exception:
        lines.append(f"OS: `{platform.system()} {platform.release()}`")

    # CPU: модель + load average
    try:
        cpu_model = platform.processor() or platform.machine()
        load1, load5, load15 = os.getloadavg()
        lines.append(f"CPU: `{cpu_model}` | Load: {load1:.1f}, {load5:.1f}, {load15:.1f}")
    except Exception:
        try:
            lines.append(f"CPU: Load {psutil.cpu_percent()}%")
        except Exception:
            lines.append("CPU: N/A")

    # RAM: total/used/free через psutil
    try:
        vm = psutil.virtual_memory()
        total_gb = vm.total / 1024**3
        used_gb = vm.used / 1024**3
        pct = vm.percent
        lines.append(f"RAM: {used_gb:.1f} / {total_gb:.1f} GB ({pct:.0f}%)")
    except Exception:
        lines.append("RAM: N/A")

    # Disk: total/used/free (корневой раздел)
    try:
        disk = psutil.disk_usage("/")
        d_total = disk.total / 1024**3
        d_used = disk.used / 1024**3
        d_pct = disk.percent
        lines.append(f"Disk: {d_used:.0f} / {d_total:.0f} GB ({d_pct:.0f}%)")
    except Exception:
        lines.append("Disk: N/A")

    # Network: IP адрес
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
        lines.append(f"Network: `{local_ip}`")
    except Exception:
        lines.append("Network: N/A")

    # Python версия
    py_ver = platform.python_version()
    lines.append(f"Python: `{py_ver}`")

    # Krab uptime + PID
    try:
        elapsed = time.time() - bot._session_start_time
        uptime_str = _format_uptime_str(elapsed)
        krab_pid = os.getpid()
        lines.append(f"Krab: PID {krab_pid} | Uptime: {uptime_str}")
    except Exception:
        import os as _os

        lines.append(f"Krab: PID {_os.getpid()}")

    await message.reply("\n".join(lines))


async def handle_uptime(bot: "KraabUserbot", message: Message) -> None:
    """Uptime: macOS + Краб + OpenClaw gateway + LM Studio + Archive."""
    import re
    import time as _t
    from pathlib import Path

    lines: list[str] = ["⏱️ **Uptime**", "─────────────"]

    # macOS system uptime через sysctl kern.boottime
    try:
        result = _sp.run(
            ["sysctl", "-n", "kern.boottime"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        m = re.search(r"sec\s*=\s*(\d+)", result.stdout)
        if m:
            boot_sec = int(m.group(1))
            sys_elapsed = _t.time() - boot_sec
            lines.append(f"macOS: `{_format_uptime_str(sys_elapsed)}`")
        else:
            lines.append("macOS: N/A")
    except Exception:
        lines.append("macOS: N/A")

    # Krab uptime
    try:
        elapsed = time.time() - bot._session_start_time
        lines.append(f"Краб: `{_format_uptime_str(elapsed)}`")
    except Exception:
        lines.append("Краб: N/A")

    # OpenClaw gateway uptime — через health endpoint
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get("http://127.0.0.1:18789/health")
            if resp.status_code == 200:
                data = resp.json()
                oc_uptime = data.get("uptime") or data.get("uptime_seconds")
                if oc_uptime is not None:
                    lines.append(f"OpenClaw: `{_format_uptime_str(float(oc_uptime))}`")
                else:
                    lines.append("OpenClaw: ✅ Online")
            else:
                lines.append("OpenClaw: ❌ Offline")
    except Exception:
        lines.append("OpenClaw: ❌ Недоступен")

    # LM Studio health check
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get("http://127.0.0.1:1234/v1/models")
            if resp.status_code == 200:
                lines.append("LM Studio: ✅ Online")
            else:
                lines.append(f"LM Studio: ⚠️ Status {resp.status_code}")
    except Exception:
        lines.append("LM Studio: 💤 Offline/Idle")

    # Archive database info
    try:
        arch_path = Path.home() / ".openclaw" / "krab_memory" / "archive.db"
        if arch_path.exists():
            size_mb = arch_path.stat().st_size / 1024 / 1024
            mtime_ago = int(_t.time() - arch_path.stat().st_mtime)
            time_ago_str = _format_uptime_str(mtime_ago)
            lines.append(f"Archive: `{size_mb:.1f} MB` (last write {time_ago_str} ago)")
        else:
            lines.append("Archive: Empty")
    except Exception:
        lines.append("Archive: N/A")

    await message.reply("\n".join(lines))


async def handle_panel(bot: "KraabUserbot", message: Message) -> None:
    """Графическая панель управления."""
    await handle_status(bot, message)


async def handle_version(bot: "KraabUserbot", message: Message) -> None:
    """Информация о версии Краба: git commit, branch, Python, Pyrogram, OpenClaw."""
    import platform

    del bot

    lines: list[str] = ["🦀 **Krab Version**", "─────"]

    # Git commit hash (короткий 7 символов)
    try:
        result = _sp.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=str(pathlib.Path(__file__).parent.parent.parent.parent),
        )
        commit = result.stdout.strip() or "unknown"
    except Exception:
        commit = "unknown"
    lines.append(f"Commit: `{commit}`")

    # Текущая ветка
    try:
        result = _sp.run(
            ["git", "branch", "--show-current"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=str(pathlib.Path(__file__).parent.parent.parent.parent),
        )
        branch = result.stdout.strip() or "unknown"
    except Exception:
        branch = "unknown"
    lines.append(f"Branch: `{branch}`")

    # Дата последнего коммита (только дата YYYY-MM-DD)
    try:
        result = _sp.run(
            ["git", "log", "-1", "--format=%ci"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=str(pathlib.Path(__file__).parent.parent.parent.parent),
        )
        commit_date = result.stdout.strip()[:10] if result.stdout.strip() else "unknown"
    except Exception:
        commit_date = "unknown"
    lines.append(f"Date: `{commit_date}`")

    # Python версия
    py_ver = platform.python_version()
    lines.append(f"Python: `{py_ver}`")

    # Pyrogram версия
    try:
        import pyrogram

        pyro_ver = pyrogram.__version__
    except Exception:
        pyro_ver = "unknown"
    lines.append(f"Pyrogram: `{pyro_ver}`")

    # OpenClaw версия через CLI
    try:
        from ...core.openclaw_cli_budget import (  # noqa: PLC0415
            get_global_semaphore,
            terminate_and_reap,
        )
        from ...core.subprocess_env import clean_subprocess_env  # noqa: PLC0415

        async with get_global_semaphore():
            oc_proc = await asyncio.create_subprocess_exec(
                "openclaw",
                "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=clean_subprocess_env(),
            )
            try:
                oc_stdout, oc_stderr = await asyncio.wait_for(oc_proc.communicate(), timeout=5.0)
            except asyncio.TimeoutError:
                await terminate_and_reap(oc_proc)
                oc_stdout, oc_stderr = b"", b""
        oc_raw = (
            oc_stdout.decode("utf-8", errors="replace").strip()
            or oc_stderr.decode("utf-8", errors="replace").strip()
        ).splitlines()
        oc_ver = oc_raw[0] if oc_raw else "unknown"
    except Exception:
        oc_ver = "unknown"
    lines.append(f"OpenClaw: `{oc_ver}`")

    await message.reply("\n".join(lines))


# ---------------------------------------------------------------------------
# !restart — перезапуск через launchctl
# ---------------------------------------------------------------------------


async def handle_restart(bot: "KraabUserbot", message: Message) -> None:
    """Перезапуск Краба через launchctl с подтверждением.

    !restart          — запросить подтверждение
    !restart confirm  — выполнить перезапуск через launchctl kickstart -k
    !restart status   — показать uptime и PID текущего процесса
    """
    from ...core.subprocess_env import clean_subprocess_env  # noqa: PLC0415

    # Метка LaunchAgent для ai.krab.core
    krab_launchd_label = "ai.krab.core"

    args = bot._get_command_args(message).strip().lower()

    # ── !restart status ──────────────────────────────────────────────────────
    if args == "status":
        pid = os.getpid()
        uptime_str = "?"
        try:
            elapsed = time.time() - bot._session_start_time
            uptime_str = _format_uptime_str(elapsed)
        except Exception:
            pass

        # Статус launchd-сервиса (Running / Stopped)
        launchd_status = "?"
        try:
            uid = os.getuid()
            proc = _sp.run(
                ["launchctl", "print", f"gui/{uid}/{krab_launchd_label}"],
                capture_output=True,
                text=True,
                timeout=5,
                env=clean_subprocess_env(),
            )
            if "pid" in proc.stdout.lower():
                import re as _re2

                pid_match = _re2.search(r"pid\s*=\s*(\d+)", proc.stdout, _re2.IGNORECASE)
                if pid_match:
                    launchd_pid = pid_match.group(1)
                    launchd_status = f"Running (PID {launchd_pid})"
                else:
                    launchd_status = "Running"
            else:
                launchd_status = "Stopped"
        except Exception:
            launchd_status = "N/A"

        lines = [
            "🦀 **Krab Status**",
            f"PID: `{pid}`",
            f"Uptime: `{uptime_str}`",
            f"LaunchAgent: `{krab_launchd_label}` — {launchd_status}",
            "",
            "Перезапуск: `!restart confirm`",
        ]
        await message.reply("\n".join(lines))
        return

    # ── !restart confirm — выполнить перезапуск ──────────────────────────────
    if args == "confirm":
        await message.reply("🔄 Перезапускаю Краба...")
        try:
            uid = os.getuid()
            proc = _sp.run(
                ["launchctl", "kickstart", "-k", f"gui/{uid}/{krab_launchd_label}"],
                capture_output=True,
                text=True,
                timeout=10,
                env=clean_subprocess_env(),
            )
            if proc.returncode != 0:
                # launchctl не сработал — запасной вариант через sys.exit
                logger.warning(
                    "launchctl kickstart вернул %d: %s",
                    proc.returncode,
                    (proc.stderr or proc.stdout).strip(),
                )
                sys.exit(42)
        except FileNotFoundError:
            # launchctl недоступен (например, в тестах) — запасной sys.exit
            sys.exit(42)
        except Exception as exc:
            logger.error("Ошибка при запуске launchctl: %s", exc)
            sys.exit(42)
        return

    # ── !restart (без аргументов) — запросить подтверждение ─────────────────
    await message.reply(
        "⚠️ **Перезапуск Краба**\n\n"
        "Это остановит и заново запустит процесс через launchd.\n\n"
        "Подтверди командой:\n`!restart confirm`\n\n"
        "Текущий статус: `!restart status`"
    )


# ---------------------------------------------------------------------------
# !diagnose / !debug
# ---------------------------------------------------------------------------


async def handle_diagnose(bot: "KraabUserbot", message: Message) -> None:
    """Диагностика системы (!diagnose)."""
    msg = await message.reply("🏥 **Запускаю диагностику системы...**")
    report = []
    report.append("**Config:**")
    report.append(f"- OPENCLAW_URL: `{config.OPENCLAW_URL}`")
    report.append(f"- LM_STUDIO_URL: `{config.LM_STUDIO_URL}`")
    if await is_lm_studio_available(config.LM_STUDIO_URL, timeout=2.0):
        report.append("- LM Studio: ✅ OK (Available)")
    else:
        report.append("- LM Studio: ❌ Offline")
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(f"{config.OPENCLAW_URL}/health")
            if resp.status_code == 200:
                report.append("- OpenClaw: ✅ OK (Healthy)")
            else:
                report.append(f"- OpenClaw: ⚠️ Error ({resp.status_code})")
    except (httpx.RequestError, httpx.ConnectError, httpx.TimeoutException, OSError) as e:
        report.append(f"- OpenClaw: ❌ Unreachable ({str(e)})")
        report.append("  _Совет: Проверьте, запущен ли Gateway и совпадает ли порт (обычно 18792)_")
    await msg.edit("\n".join(report))


async def handle_debug(bot: "KraabUserbot", message: Message) -> None:
    """
    Отладочная информация для разработчика (!debug [sessions|tasks|gc]).

    Субкоманды:
    - `!debug`          — сводка: tasks, timers, sessions, rate limiter, last error
    - `!debug sessions` — список активных OpenClaw сессий с размером
    - `!debug tasks`    — список asyncio задач
    - `!debug gc`       — принудительный GC + статистика

    Owner-only.
    """
    import gc

    # Проверка прав: только владелец
    access_profile = bot._get_access_profile(message.from_user)
    if access_profile.level != AccessLevel.OWNER:
        raise UserInputError(user_message="🔒 `!debug` доступен только владельцу.")

    raw_args = bot._get_command_args(message).strip().lower()
    sub = raw_args.split()[0] if raw_args else ""

    if sub == "tasks":
        # Список asyncio задач
        tasks = list(asyncio.all_tasks())
        lines = [f"⚙️ **asyncio tasks** (`{len(tasks)}` total)", "───────────────"]
        for t in sorted(tasks, key=lambda x: x.get_name()):
            name = t.get_name()
            done_str = "done" if t.done() else "running"
            coro = t.get_coro()
            coro_name = getattr(coro, "__qualname__", None) or getattr(coro, "__name__", str(coro))
            lines.append(f"- `{name}` [{done_str}] — `{coro_name}`")
        if len(lines) > 32:
            lines = lines[:32]
            lines.append(f"…и ещё `{len(tasks) - 30}` задач")
        await message.reply("\n".join(lines))
        return

    if sub == "sessions":
        # Список OpenClaw сессий с размером
        sessions_raw: dict = {}
        try:
            sessions_raw = dict(openclaw_client._sessions)  # type: ignore[attr-defined]
        except AttributeError:
            pass
        lines = [
            f"💬 **OpenClaw sessions** (`{len(sessions_raw)}` active)",
            "───────────────────────",
        ]
        if not sessions_raw:
            lines.append("- сессий нет")
        else:
            for sid, msgs in sorted(sessions_raw.items(), key=lambda x: -len(x[1])):
                msg_list = list(msgs) if msgs is not None else []
                size = len(msg_list)
                # Грубая оценка токенов: 4 символа ~ 1 токен
                raw_text = " ".join(
                    str(m.get("content", "")) for m in msg_list if isinstance(m, dict)
                )
                approx_tokens = max(1, len(raw_text) // 4)
                lines.append(
                    f"- `{sid}` — `{size}` сообщений (~{approx_tokens:,} tok)".replace(",", "_")
                )
        await message.reply("\n".join(lines))
        return

    if sub == "gc":
        # Принудительный garbage collection
        before = gc.get_count()
        collected = gc.collect()
        after = gc.get_count()
        unreachable = gc.garbage
        lines = [
            "🗑 **Garbage Collection**",
            "─────────────────────",
            f"До:    gen0={before[0]}, gen1={before[1]}, gen2={before[2]}",
            f"После: gen0={after[0]}, gen1={after[1]}, gen2={after[2]}",
            f"Собрано объектов: `{collected}`",
            f"gc.garbage (unreachable): `{len(unreachable)}`",
        ]
        await message.reply("\n".join(lines))
        return

    # Сводка по умолчанию
    from ...core.telegram_rate_limiter import telegram_rate_limiter

    # asyncio tasks
    all_tasks = list(asyncio.all_tasks())
    task_count = len(all_tasks)
    done_count = sum(1 for t in all_tasks if t.done())

    # Pending timers — читаем _active_timers лениво из command_handlers
    # (модуль-уровень state из scheduler_commands, re-exported в command_handlers).
    from .. import command_handlers as _ch

    timer_count = len(_ch._active_timers)

    # Активные OpenClaw сессии
    try:
        sessions_map: dict = dict(openclaw_client._sessions)  # type: ignore[attr-defined]
    except AttributeError:
        sessions_map = {}
    session_count = len(sessions_map)
    total_msgs = sum(len(list(v)) for v in sessions_map.values() if v is not None)

    # Rate limiter stats
    rl = telegram_rate_limiter.stats()
    rl_str = (
        f"cap {int(rl.get('max_per_sec', 0))} req/s · "
        f"в окне {int(rl.get('current_in_window', 0))} · "
        f"total {int(rl.get('total_acquired', 0))} · "
        f"waited {int(rl.get('total_waited', 0))}"
    )

    # Последняя ошибка из proactive_watch (если доступна)
    last_error_str = "—"
    try:
        err_digest = proactive_watch.get_error_digest()  # type: ignore[attr-defined]
        if err_digest:
            last_err = err_digest[-1] if isinstance(err_digest, list) else None
            if last_err and isinstance(last_err, dict):
                last_error_str = (
                    f"{last_err.get('type', '?')}: {str(last_err.get('msg', '?'))[:80]}"
                )
    except Exception:  # noqa: BLE001
        pass

    lines = [
        "🐛 **Debug Info**",
        "─────────────────",
        (
            f"asyncio tasks: `{task_count}` total "
            f"(`{done_count}` done, `{task_count - done_count}` running)"
        ),
        f"Pending timers: `{timer_count}`",
        f"OpenClaw sessions: `{session_count}` (total `{total_msgs}` messages)",
        f"Rate limiter: `{rl_str}`",
        f"Last error: `{last_error_str}`",
        "",
        "Субкоманды:",
        "`!debug sessions` — список сессий",
        "`!debug tasks`    — список задач",
        "`!debug gc`       — garbage collection",
    ]
    await message.reply("\n".join(lines))


# ---------------------------------------------------------------------------
# !stats — runtime panel + ecosystem health subcommand
# ---------------------------------------------------------------------------


def _render_stats_panel(bot: "KraabUserbot") -> str:
    """
    Собирает компактный runtime-статус Краба из четырёх подсистем.

    Источники (все singleton'ы читаются sync):
    - telegram_rate_limiter.stats() — sliding-window capacity cap;
    - chat_ban_cache.list_entries() — persisted short-circuit ban list;
    - chat_capability_cache.list_entries() — TTL cache per-chat permissions;
    - silence_manager.status() — per-chat и глобальный mute;
    - bot.get_voice_runtime_profile() — флаг delivery и blocked chats.

    Бонус: голосовой runtime-профиль отображается только если bot отдал словарь,
    чтобы тесты могли подменить профиль через stub без Pyrogram client'а.
    """
    import time as _time

    import psutil as _psutil

    from ...core.chat_ban_cache import chat_ban_cache
    from ...core.chat_capability_cache import chat_capability_cache
    from ...core.silence_mode import silence_manager
    from ...core.telegram_rate_limiter import telegram_rate_limiter

    lines: list[str] = ["📊 **Krab Stats**", "─────────────"]

    # 0. Компактная сводка: uptime, сообщения, модель, translator, swarm, inbox, RAM ──
    try:
        elapsed = int(_time.time() - bot._session_start_time)
        hours, rem = divmod(elapsed, 3600)
        mins = rem // 60
        uptime_str = f"{hours}ч {mins}м" if hours else f"{mins}м"
    except Exception:
        uptime_str = "?"

    try:
        msg_count = bot._session_messages_processed
    except Exception:
        msg_count = 0

    # Текущая модель из runtime models config
    try:
        from ...core.openclaw_runtime_models import get_runtime_primary_model as _grpm

        model_name = _grpm() or "?"
        provider = (model_name.split("/")[0]) if "/" in model_name else "?"
        model_short = model_name.split("/")[-1] if "/" in model_name else model_name
    except Exception:
        model_short = "?"
        provider = "?"

    # Статус транслятора
    try:
        t_state = bot.get_translator_session_state()
        t_status = t_state.get("session_status", "idle")
        t_pair = t_state.get("last_pair") or ""
        translator_str = f"{t_status}" + (f" ({t_pair})" if t_pair else "")
    except Exception:
        translator_str = "idle"

    # Swarm rounds за сегодня
    try:
        import datetime as _dt

        from ...core.swarm_artifact_store import swarm_artifact_store as _sas

        today_str = _dt.date.today().isoformat()
        all_arts = _sas.list_artifacts(limit=200)
        swarm_today = sum(
            1 for a in all_arts if str(a.get("timestamp_iso", "")).startswith(today_str)
        )
    except Exception:
        swarm_today = 0

    # Inbox summary
    try:
        from ...core.inbox_service import inbox_service as _inbox

        isummary = _inbox.get_summary()
        inbox_open = isummary.get("open_items", 0)
        inbox_attention = isummary.get("attention_items", 0)
        inbox_str = f"{inbox_open} open" + (
            f" ({inbox_attention} attention)" if inbox_attention else ""
        )
    except Exception:
        inbox_str = "?"

    # RAM usage
    try:
        import os as _os

        rss_mb = int(_psutil.Process(_os.getpid()).memory_info().rss / 1024 / 1024)
        ram_str = f"{rss_mb} MB"
    except Exception:
        ram_str = "?"

    lines.append(f"⏱ Uptime: {uptime_str}")
    lines.append(f"💬 Сообщений: {msg_count}")
    lines.append(f"🤖 Модель: {model_short} ({provider})")
    lines.append(f"🔄 Translator: {translator_str}")
    lines.append(f"🐝 Swarm: {swarm_today} rounds today")
    lines.append(f"📬 Inbox: {inbox_str}")
    lines.append(f"🧠 RAM: {ram_str}")
    lines.append("")

    # 1. Telegram API rate limiter ────────────────────────────────────
    rl = telegram_rate_limiter.stats()
    lines.append("🌐 **Telegram API rate limiter**")
    lines.append(
        f"- Cap: `{int(rl.get('max_per_sec', 0))} req/s` "
        f"(окно `{float(rl.get('window_sec', 1.0)):.1f}s`) · "
        f"В окне сейчас: `{int(rl.get('current_in_window', 0))}`"
    )
    lines.append(
        f"- Всего acquire: `{int(rl.get('total_acquired', 0))}` · "
        f"ждали: `{int(rl.get('total_waited', 0))}` "
        f"(сумма `{float(rl.get('total_wait_sec', 0.0)):.3f}s`)"
    )
    lines.append("")

    # 2. Chat ban cache ───────────────────────────────────────────────
    ban_entries = chat_ban_cache.list_entries()
    lines.append(f"🚫 **Chat ban cache** (`{len(ban_entries)}` active)")
    if not ban_entries:
        lines.append("- записей нет")
    else:
        for entry in ban_entries[:3]:
            chat_id = entry.get("chat_id", "?")
            code = entry.get("last_error_code") or entry.get("error_code") or "?"
            lines.append(f"- `{chat_id}` · {code}")
        if len(ban_entries) > 3:
            lines.append(f"- …ещё `{len(ban_entries) - 3}`")
    lines.append("")

    # 3. Chat capability cache ────────────────────────────────────────
    cap_entries = chat_capability_cache.list_entries()
    voice_forbidden = sum(1 for entry in cap_entries if entry.get("voice_allowed") is False)
    slow_mode_active = 0
    for entry in cap_entries:
        slow = entry.get("slow_mode_seconds")
        try:
            if slow is not None and int(slow) > 0:
                slow_mode_active += 1
        except (TypeError, ValueError):
            continue
    lines.append(f"🎛 **Chat capability cache** (`{len(cap_entries)}` cached)")
    lines.append(f"- Voice запрещён явно: `{voice_forbidden}`")
    lines.append(f"- Slow mode > 0: `{slow_mode_active}`")
    lines.append("")

    # 4. Silence mode ─────────────────────────────────────────────────
    silence = silence_manager.status()
    global_muted = bool(silence.get("global_muted"))
    muted_chats_raw = silence.get("muted_chats") or {}
    muted_chats_count = len(muted_chats_raw) if isinstance(muted_chats_raw, dict) else 0
    global_remaining_min = silence.get("global_remaining_min") or 0
    lines.append("🔇 **Silence mode**")
    lines.append(
        f"- Глобально: `{'ВКЛ' if global_muted else 'ВЫКЛ'}`"
        + (f" · осталось `{global_remaining_min} мин`" if global_muted else "")
    )
    lines.append(f"- Заглушённых чатов: `{muted_chats_count}`")
    lines.append("")

    # 5. Voice runtime (bonus) ────────────────────────────────────────
    try:
        profile = bot.get_voice_runtime_profile()
    except Exception:  # pragma: no cover — defensive
        profile = None
    if isinstance(profile, dict):
        blocked = profile.get("blocked_chats") or []
        try:
            blocked_count = len(blocked)
        except TypeError:
            blocked_count = 0
        delivery = str(profile.get("delivery") or "text+voice")
        enabled = bool(profile.get("enabled"))
        lines.append("🎙 **Voice runtime**")
        lines.append(
            f"- Озвучка: `{'ВКЛ' if enabled else 'ВЫКЛ'}` · "
            f"Delivery: `{delivery}` · Blocklist: `{blocked_count}`"
        )

    # 6. FinOps + Translator + Swarm (session 5) ──────────────────────
    lines.append("")
    try:
        from ...core.cost_analytics import cost_analytics as _ca

        report = _ca.build_usage_report_dict()
        cost = report.get("cost_session_usd", 0)
        calls = sum(m.get("calls", 0) for m in (report.get("by_model") or {}).values())
        tool_calls = report.get("total_tool_calls", 0)
        lines.append(f"💰 **FinOps** · ${cost:.4f} · {calls} calls · {tool_calls} tools")
    except Exception:
        pass
    try:
        translator_state = bot.get_translator_session_state()
        t_status = translator_state.get("session_status", "idle")
        t_stats = translator_state.get("stats") or {}
        t_count = t_stats.get("total_translations", 0)
        lines.append(f"🔄 **Translator** · {t_status} · {t_count} переводов")
    except Exception:
        pass
    try:
        from ...core.swarm_task_board import swarm_task_board

        board = swarm_task_board.get_board_summary()
        lines.append(
            f"🐝 **Swarm** · {board.get('total', 0)} tasks · done: {board.get('by_status', {}).get('done', 0)}"
        )
    except Exception:
        pass

    return "\n".join(lines).rstrip()


def _format_ecosystem_report(report: dict[str, Any]) -> str:
    """
    Формирует Telegram-представление ecosystem health отчёта.

    Поддерживает как основные секции `/api/ecosystem/health` (status, checks,
    chain, resources, queue, budget, recommendations), так и опциональный блок
    `session_10` (memory_validator / memory_archive / dedicated_chrome /
    auto_restart) — если он появится в будущих расширениях сервиса.
    """
    lines: list[str] = ["🌍 **Ecosystem Health**"]

    # Top-level ────────────────────────────────────────────────────────
    status = str(report.get("status") or "unknown").lower()
    risk = str(report.get("risk_level") or "unknown").lower()
    degradation = str(report.get("degradation") or "unknown")
    status_icon = "✅" if status == "ok" else ("⚠️" if status == "degraded" else "❌")
    lines.append(f"**Overall:** {status_icon} {status} · risk=`{risk}` · {degradation}")

    # Chain ────────────────────────────────────────────────────────────
    chain = report.get("chain") or {}
    if chain:
        ai_channel = chain.get("active_ai_channel", "?")
        fb_ready = chain.get("fallback_ready")
        voice_ready = chain.get("voice_assist_ready")
        lines.append(
            f"**Chain:** channel=`{ai_channel}` · fallback={'✅' if fb_ready else '❌'}"
            f" · voice={'✅' if voice_ready else '❌'}"
        )

    # Checks (opencla / local_lm / voice_gateway / krab_ear) ───────────
    checks = report.get("checks") or {}
    if isinstance(checks, dict) and checks:
        lines.append("\n**Checks:**")
        for name, info in checks.items():
            if not isinstance(info, dict):
                continue
            ok = bool(info.get("ok"))
            status_txt = str(info.get("status") or "?")
            latency = info.get("latency_ms")
            extra = f" · {latency}ms" if isinstance(latency, int) else ""
            lines.append(f"• {'✅' if ok else '❌'} `{name}` · {status_txt}{extra}")

    # Resources ────────────────────────────────────────────────────────
    resources = report.get("resources") or {}
    if isinstance(resources, dict) and resources and "error" not in resources:
        cpu = resources.get("cpu_percent")
        ram = resources.get("ram_percent")
        ram_avail = resources.get("ram_available_gb")
        parts: list[str] = []
        if cpu is not None:
            parts.append(f"CPU={cpu}%")
        if ram is not None:
            parts.append(f"RAM={ram}%")
        if ram_avail is not None:
            parts.append(f"free={ram_avail}GB")
        if parts:
            lines.append(f"\n**Resources:** {' · '.join(parts)}")

    # Budget ───────────────────────────────────────────────────────────
    budget = report.get("budget") or {}
    if isinstance(budget, dict) and budget:
        usage_pct = budget.get("usage_percent")
        runway = budget.get("runway_days")
        economy = budget.get("is_economy_mode")
        bparts: list[str] = []
        if usage_pct is not None:
            bparts.append(f"usage={usage_pct}%")
        if runway is not None:
            bparts.append(f"runway={runway}d")
        if economy:
            bparts.append("🏷 ECONOMY")
        if bparts:
            lines.append(f"**Budget:** {' · '.join(bparts)}")

    # Session 10 (опциональный расширенный блок) ───────────────────────
    s10 = report.get("session_10") or {}
    if isinstance(s10, dict) and s10:
        lines.append("\n**Session 10:**")
        mv = s10.get("memory_validator") or {}
        if isinstance(mv, dict) and mv.get("available"):
            lines.append(
                f"• 🛡 Memory Validator: safe={mv.get('safe_total', 0)}"
                f" blocked={mv.get('injection_blocked_total', 0)}"
                f" pending={mv.get('pending_count', 0)}"
            )
        ma = s10.get("memory_archive") or {}
        if isinstance(ma, dict) and ma.get("exists"):
            size_bytes = ma.get("size_bytes", 0) or 0
            size_mb = int(size_bytes // 1024 // 1024)
            msgs = ma.get("message_count", 0)
            msgs_str = f"{msgs:,}".replace(",", " ")
            lines.append(f"• 🧠 Archive: {msgs_str} msgs, {size_mb} MB")
        dc = s10.get("dedicated_chrome") or {}
        if isinstance(dc, dict) and dc.get("enabled"):
            lines.append(f"• 🌐 Chrome: running={dc.get('running')} port={dc.get('port')}")
        ar = s10.get("auto_restart") or {}
        if isinstance(ar, dict) and ar:
            lines.append(
                f"• 🔄 Auto-restart: enabled={ar.get('enabled')}"
                f" attempts/hr={ar.get('total_attempts_last_hour', 0)}"
            )

    # Recommendations ──────────────────────────────────────────────────
    recs = report.get("recommendations") or []
    if isinstance(recs, list) and recs:
        lines.append("\n**Recommendations:**")
        for r in recs[:5]:
            lines.append(f"• {r}")

    # Truncate for Telegram limit ─────────────────────────────────────
    text = "\n".join(lines)
    if len(text) > 3800:
        text = text[:3800] + "\n…(truncated)"
    return text


async def _handle_stats_ecosystem(bot: "KraabUserbot", message: Message) -> None:
    """Вывод ecosystem health в Telegram через `/api/ecosystem/health`."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get("http://127.0.0.1:8080/api/ecosystem/health")
            data = r.json()
    except Exception as exc:  # noqa: BLE001
        await message.reply(f"❌ Ecosystem health недоступен: {exc}")
        return

    # API возвращает {"ok": True, "report": {...}}. Если обёртки нет — принимаем
    # data как report (backward-compat с потенциальными вариантами ответа).
    if isinstance(data, dict) and "report" in data:
        report = data.get("report") or {}
    else:
        report = data if isinstance(data, dict) else {}
    if not isinstance(report, dict) or not report:
        await message.reply("❌ Ecosystem health: пустой ответ от API.")
        return

    text = _format_ecosystem_report(report)
    await message.reply(text)


async def handle_stats(bot: "KraabUserbot", message: Message) -> None:
    """Агрегированный runtime-статус Краба.

    Поддерживаемые подкоманды:
    - `!stats` / `!stats basic` — per-session panel (rate limiter, caches, silence, voice).
    - `!stats ecosystem` (`eco`, `health`) — ecosystem health из `/api/ecosystem/health`.
    """
    # Lazy proxy через command_handlers namespace — позволяет
    # ``test_stats_ecosystem_command`` патчить
    # ``command_handlers._handle_stats_ecosystem`` / ``_render_stats_panel``.
    from .. import command_handlers as _ch

    args = bot._get_command_args(message) or ""
    sub = args.strip().lower()

    if sub in ("ecosystem", "eco", "health"):
        await _ch._handle_stats_ecosystem(bot, message)
        return

    panel = _ch._render_stats_panel(bot)
    await message.reply(panel)


# ---------------------------------------------------------------------------
# !health [deep] — health-check + расширенная диагностика
# ---------------------------------------------------------------------------


async def _health_deep_report(bot: "KraabUserbot") -> str:
    """Собирает расширенный диагностический отчёт (!health deep). Owner-only.

    Возвращает markdown-строку до 4000 символов (Telegram-лимит).
    Делегирует сбор данных в collect_health_deep(), форматирует в markdown.
    """
    from ...core.health_deep_collector import collect_health_deep

    session_start = getattr(bot, "_session_start_time", None)
    data = await collect_health_deep(session_start_time=session_start)

    sections: list[str] = ["🏥 **Health Deep**", "══════════════════"]

    # ── 1. Krab process ─────────────────────────────────────────────────────
    krab = data.get("krab", {})
    if "error" in krab:
        sections.append(f"**Krab process** ❌ {krab['error']}")
    else:
        elapsed = krab.get("uptime_sec", 0)
        hrs, rem = divmod(max(elapsed, 0), 3600)
        mins = rem // 60
        uptime_str = f"{hrs}h {mins}m" if hrs else f"{mins}m"
        rss_mb = krab.get("rss_mb", "?")
        cpu = krab.get("cpu_pct", "?")
        sections.append(
            f"**Krab process**\n• Uptime: {uptime_str}\n• RSS: {rss_mb} MB\n• Load avg (1m): {cpu}"
        )

    # ── 2. OpenClaw gateway ──────────────────────────────────────────────────
    oc = data.get("openclaw", {})
    if "error" in oc and not oc.get("healthy"):
        sections.append(f"**OpenClaw gateway** ❌ {oc.get('error', 'unknown error')}")
    else:
        oc_ok = oc.get("healthy", False)
        oc_model = str((oc.get("last_route") or {}).get("model") or "unknown")
        oc_icon = "✅" if oc_ok else "❌"
        oc_status = "up" if oc_ok else "offline"
        sections.append(
            f"**OpenClaw gateway**\n• Status: {oc_icon} {oc_status}\n• Active model: `{oc_model}`"
        )

    # ── 3. LM Studio ─────────────────────────────────────────────────────────
    lm = data.get("lm_studio", {})
    if lm.get("state") == "online":
        loaded = lm.get("loaded_models") or []
        lm_detail = ", ".join(loaded) if loaded else "нет загруженных моделей"
        sections.append(f"**LM Studio**\n• Status: ✅ online\n• Models: {lm_detail}")
    elif lm.get("state") == "error":
        sections.append(f"**LM Studio** ❌ {lm.get('error', '')}")
    else:
        sections.append("**LM Studio**\n• Status: ❌ offline")

    # ── 4. Archive.db ────────────────────────────────────────────────────────
    adb = data.get("archive_db", {})
    integrity = adb.get("integrity", "?")
    if integrity == "missing":
        sections.append("**Archive.db** ⚠️ файл не найден")
    elif integrity == "error" or "error" in adb:
        sections.append(f"**Archive.db** ❌ {adb.get('error', integrity)}")
    else:
        integ_icon = "✅" if integrity == "ok" else "❌"
        size_mb = adb.get("size_mb", 0)
        fts_orph = adb.get("orphan_fts5")
        vec_orph = adb.get("orphan_vec")
        # Формируем строку с orphan-счётчиками; ⚠️ если есть сироты
        if fts_orph is None and vec_orph is None:
            orph_line = "• FTS5 orphans: n/a | vec orphans: n/a"
        else:
            fts_str = str(fts_orph) if fts_orph is not None else "n/a"
            vec_str = str(vec_orph) if vec_orph is not None else "n/a"
            has_orphans = (isinstance(fts_orph, int) and fts_orph > 0) or (
                isinstance(vec_orph, int) and vec_orph > 0
            )
            prefix = "⚠️ " if has_orphans else ""
            orph_line = f"• {prefix}FTS5 orphans: {fts_str} | vec orphans: {vec_str}"
        sections.append(
            f"**Archive.db**\n"
            f"• Integrity: {integ_icon} {integrity}\n"
            f"• Messages: {adb.get('messages', '?'):,} | Chunks: {adb.get('chunks', '?'):,}\n"
            f"• Size: {size_mb:.1f} MB\n"
            f"{orph_line}"
        )

    # ── 5. Reminders ─────────────────────────────────────────────────────────
    rem = data.get("reminders", {})
    if "error" in rem:
        sections.append(f"**Reminders** ❌ {rem['error']}")
    else:
        remind_count = rem.get("pending", 0)
        remind_icon = "✅" if remind_count == 0 else "ℹ️"
        sections.append(f"**Reminders** {remind_icon}\n• Pending: {remind_count}")

    # ── 6. Memory validator ───────────────────────────────────────────────────
    mv = data.get("memory_validator", {})
    if "error" in mv:
        sections.append(f"**Memory validator** ❌ {mv['error']}")
    else:
        pend_count = mv.get("pending_confirm", 0)
        pend_icon = "✅" if pend_count == 0 else "⚠️"
        sections.append(f"**Memory validator** {pend_icon}\n• Pending !confirm: {pend_count}")

    # ── 7. SIGTERM log events ─────────────────────────────────────────────────
    sigterm_count = data.get("sigterm_recent_count", 0)
    if isinstance(sigterm_count, int) and sigterm_count >= 0:
        sig_icon = "✅" if sigterm_count == 0 else "⚠️"
        sections.append(f"**Log (last 500 lines)** {sig_icon}\n• SIGTERM events: {sigterm_count}")
    elif "sigterm_error" in data:
        sections.append(f"**Log** ❌ {data['sigterm_error']}")
    else:
        sections.append("**Log** ⚠️ файл лога не найден")

    # ── 8. System ─────────────────────────────────────────────────────────────
    sys_data = data.get("system", {})
    if "error" in sys_data:
        sections.append(f"**System** ❌ {sys_data['error']}")
    else:
        total_gb = sys_data.get("total_mb", 0) / 1024
        avail_gb = sys_data.get("free_mb", 0) / 1024
        used_pct = sys_data.get("used_pct", 0)
        load_avg = sys_data.get("load_avg", [0, 0, 0])
        sections.append(
            f"**System**\n"
            f"• RAM: {avail_gb:.1f} GB free / {total_gb:.1f} GB ({used_pct:.0f}% used)\n"
            f"• Load avg: {load_avg[0]:.2f} / {load_avg[1]:.2f} / {load_avg[2]:.2f}"
        )

    report = "\n\n".join(sections)
    # Обрезаем по Telegram-лимиту
    if len(report) > 4000:
        report = report[:3990] + "\n…(truncated)"
    return report


async def handle_health(bot: "KraabUserbot", message: Message) -> None:
    """
    Диагностика подсистем Краба (!health [deep]).

    !health      — стандартный health-check (ок / warning / error по строке).
    !health deep — расширенная диагностика: uptime, archive.db integrity,
                   LM Studio, SIGTERM count, memory validator, system load.
    Owner-only команда.
    """
    from ...core.swarm_bus import TEAM_REGISTRY
    from ...core.swarm_scheduler import swarm_scheduler
    from ...core.telegram_rate_limiter import telegram_rate_limiter

    # Проверяем субкоманду deep
    # Lazy proxy через command_handlers namespace — позволяет тестам патчить
    # ``command_handlers.openclaw_client``, ``is_lm_studio_available``,
    # ``get_runtime_primary_model`` через monkeypatch.setattr.
    from .. import command_handlers as _ch

    _oc = _ch.openclaw_client
    _is_lm = _ch.is_lm_studio_available

    raw_args = (
        (bot._get_command_args(message) if hasattr(bot, "_get_command_args") else "")
        .strip()
        .lower()
    )
    if raw_args == "deep":
        access_profile = bot._get_access_profile(message.from_user)
        if access_profile.level != AccessLevel.OWNER:
            raise UserInputError(user_message="🔒 `!health deep` доступен только владельцу.")
        deep_report = await _health_deep_report(bot)
        await message.reply(deep_report)
        return

    lines: list[str] = ["🏥 **Health Check**", "─────────────────"]

    # 1. Telegram: проверяем, что me доступен (userbot подключён)
    try:
        telegram_ok = bot.me is not None
        lines.append("✅ Telegram: connected" if telegram_ok else "❌ Telegram: не инициализирован")
    except Exception as exc:
        lines.append(f"❌ Telegram: ошибка ({exc})")

    # 2. OpenClaw gateway — health check + текущая модель маршрута
    try:
        oc_ok = await _oc.health_check()
        route_meta: dict[str, Any] = {}
        if hasattr(_oc, "get_last_runtime_route"):
            route_meta = _oc.get_last_runtime_route() or {}
        model = str(route_meta.get("model") or "").strip()
        if not model:
            model = str(
                _ch.get_runtime_primary_model() or getattr(config, "MODEL", "") or "unknown"
            )
        if oc_ok:
            lines.append(f"✅ OpenClaw: up ({model})")
        else:
            lines.append(f"❌ OpenClaw: offline ({model})")
    except Exception as exc:
        lines.append(f"❌ OpenClaw: ошибка ({exc})")

    # 3. Swarm scheduler — флаг ENABLED и количество jobs
    try:
        sched_enabled = getattr(config, "SCHEDULER_ENABLED", False)
        jobs = swarm_scheduler.list_jobs()
        job_count = len(jobs)
        if sched_enabled:
            lines.append(f"✅ Scheduler: enabled ({job_count} jobs)")
        else:
            lines.append(f"⚠️ Scheduler: disabled ({job_count} jobs)")
    except Exception as exc:
        lines.append(f"❌ Scheduler: ошибка ({exc})")

    # 4. Proactive Watch — фоновая asyncio-задача жива?
    try:
        pw_task = getattr(bot, "_proactive_watch_task", None)
        pw_running = pw_task is not None and not pw_task.done()
        if pw_running:
            lines.append("✅ Proactive Watch: running")
        else:
            lines.append("⚠️ Proactive Watch: не запущен")
    except Exception as exc:
        lines.append(f"❌ Proactive Watch: ошибка ({exc})")

    # 5. Inbox — attention items (warning/error severity)
    try:
        inbox_summary = _ch.inbox_service.get_summary()
        attention = int(inbox_summary.get("attention_items", 0))
        open_items = int(inbox_summary.get("open_items", 0))
        if attention > 0:
            lines.append(f"⚠️ Inbox: {attention} attention items ({open_items} open)")
        else:
            lines.append(f"✅ Inbox: чисто ({open_items} open)")
    except Exception as exc:
        lines.append(f"❌ Inbox: ошибка ({exc})")

    # 6. Swarm teams — из TEAM_REGISTRY
    try:
        team_count = len(TEAM_REGISTRY)
        if team_count > 0:
            lines.append(f"✅ Swarm: {team_count} teams ready ({', '.join(TEAM_REGISTRY)})")
        else:
            lines.append("❌ Swarm: команды не зарегистрированы")
    except Exception as exc:
        lines.append(f"❌ Swarm: ошибка ({exc})")

    # 7. Voice — проверяем конфигурацию через runtime профиль бота
    try:
        voice_profile: dict[str, Any] = (
            bot.get_voice_runtime_profile() if hasattr(bot, "get_voice_runtime_profile") else {}
        )
        voice_name = str(voice_profile.get("voice") or getattr(config, "VOICE_REPLY_VOICE", ""))
        voice_enabled = bool(voice_profile.get("enabled"))
        if voice_name:
            status_str = "ВКЛ" if voice_enabled else "ВЫКЛ"
            lines.append(f"✅ Voice: configured ({voice_name}, {status_str})")
        else:
            lines.append("⚠️ Voice: не настроен")
    except Exception as exc:
        lines.append(f"❌ Voice: ошибка ({exc})")

    # 8. LM Studio — availability check (короткий таймаут)
    try:
        lm_ok = await _is_lm(config.LM_STUDIO_URL, timeout=2.0)
        if lm_ok:
            lines.append("✅ LM Studio: online")
        else:
            lines.append("❌ LM Studio: offline")
    except Exception as exc:
        lines.append(f"❌ LM Studio: ошибка ({exc})")

    # 9. Rate Limiter — текущая нагрузка sliding window
    try:
        rl_stats = telegram_rate_limiter.stats()
        current = int(rl_stats.get("current_in_window", 0))
        cap = int(rl_stats.get("max_per_sec", 20))
        if current >= cap:
            lines.append(f"⚠️ Rate Limiter: {current}/{cap} rps (перегрузка)")
        else:
            lines.append(f"✅ Rate Limiter: {current}/{cap} rps")
    except Exception as exc:
        lines.append(f"❌ Rate Limiter: ошибка ({exc})")

    report = "\n".join(lines)
    if message.from_user and message.from_user.id == bot.me.id:
        await message.edit(report)
    else:
        await message.reply(report, reply_markup=build_health_recheck_buttons())


# ---------------------------------------------------------------------------
# !ip / !dns / !ping — сетевая диагностика
# ---------------------------------------------------------------------------


def _get_local_ip() -> str:
    """Определить локальный IP через UDP-сокет (без отправки пакетов)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:  # noqa: BLE001
        return "н/д"


async def _get_public_ip() -> str:
    """Получить публичный IP через api.ipify.org."""
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get("https://api.ipify.org?format=json")
        resp.raise_for_status()
        return resp.json()["ip"]


async def handle_ip(bot: "KraabUserbot", message: Message) -> None:
    """
    Команда !ip — показать IP-адреса.

    !ip          — публичный + локальный
    !ip local    — только локальный (без HTTP-запроса)
    """
    args = bot._get_command_args(message).strip().lower()

    # Ленивый lookup — тесты патчат command_handlers._get_local_ip / _get_public_ip
    from .. import command_handlers as _ch

    _local_fn = getattr(_ch, "_get_local_ip", _get_local_ip)
    _public_fn = getattr(_ch, "_get_public_ip", _get_public_ip)
    local_ip = _local_fn()

    if args == "local":
        # Только локальный IP
        text = f"🌐 **IP Info**\n─────\nLocal: `{local_ip}`"
        await message.reply(text)
        return

    # Публичный + локальный
    try:
        public_ip = await _public_fn()
    except Exception as exc:  # noqa: BLE001
        raise UserInputError(user_message=f"❌ Не удалось получить публичный IP: {exc}") from exc

    text = f"🌐 **IP Info**\n─────\nPublic: `{public_ip}`\nLocal: `{local_ip}`"
    await message.reply(text)


async def handle_dns(bot: "KraabUserbot", message: Message) -> None:
    """
    Команда !dns <domain> — DNS lookup.

    Показывает A, AAAA и MX записи для домена.
    """
    domain = bot._get_command_args(message).strip()
    if not domain:
        raise UserInputError(user_message="❌ Укажи домен: `!dns example.com`")

    lines: list[str] = [f"🔍 **DNS: {domain}**", "─────"]

    # A записи (IPv4)
    try:
        a_results = await asyncio.get_event_loop().run_in_executor(
            None, lambda: socket.getaddrinfo(domain, None, socket.AF_INET)
        )
        a_addrs = sorted({r[4][0] for r in a_results})
        for addr in a_addrs:
            lines.append(f"A     `{addr}`")
    except socket.gaierror:
        lines.append("A     н/д")

    # AAAA записи (IPv6)
    try:
        aaaa_results = await asyncio.get_event_loop().run_in_executor(
            None, lambda: socket.getaddrinfo(domain, None, socket.AF_INET6)
        )
        aaaa_addrs = sorted({r[4][0] for r in aaaa_results})
        for addr in aaaa_addrs:
            lines.append(f"AAAA  `{addr}`")
    except socket.gaierror:
        pass  # IPv6 может отсутствовать — не показываем

    # MX записи через host (доступна на macOS/Linux)
    try:
        proc = await asyncio.create_subprocess_exec(
            "host",
            "-t",
            "MX",
            domain,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
        mx_lines = stdout.decode(errors="replace").splitlines()
        for mx_line in mx_lines:
            if "mail is handled" in mx_line or "MX" in mx_line:
                parts = mx_line.split()
                if len(parts) >= 2:
                    mx_record = " ".join(parts[-2:]).rstrip(".")
                    lines.append(f"MX    `{mx_record}`")
    except Exception:  # noqa: BLE001
        pass  # MX необязательны

    await message.reply("\n".join(lines))


async def handle_ping(bot: "KraabUserbot", message: Message) -> None:
    """
    Команда !ping <host> — ping хоста (1 пакет, показать latency).

    Использует системный ping через subprocess.
    """
    host = bot._get_command_args(message).strip()
    if not host:
        raise UserInputError(user_message="❌ Укажи хост: `!ping example.com`")

    try:
        proc = await asyncio.create_subprocess_exec(
            "ping",
            "-c",
            "1",
            "-W",
            "3",
            host,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=os.environ.copy(),
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
        output = stdout.decode(errors="replace")
    except asyncio.TimeoutError:
        raise UserInputError(user_message=f"❌ `{host}` — timeout (10 сек)") from None
    except Exception as exc:  # noqa: BLE001
        raise UserInputError(user_message=f"❌ Ошибка ping: {exc}") from exc

    # Парсим latency из строки вида: time=12.3 ms
    latency: str | None = None
    for line in output.splitlines():
        if "time=" in line:
            for part in line.split():
                if part.startswith("time="):
                    latency = part[len("time=") :]
                    break
            if latency:
                break

    if proc.returncode == 0 and latency:
        text = f"🏓 **Ping: {host}**\n─────\nLatency: `{latency} ms`\nStatus: ✅ доступен"
    elif proc.returncode == 0:
        text = f"🏓 **Ping: {host}**\n─────\nStatus: ✅ доступен"
    else:
        text = f"🏓 **Ping: {host}**\n─────\nStatus: ❌ недоступен"

    await message.reply(text)


# ---------------------------------------------------------------------------
# !log — просмотр логов Краба из Telegram
# ---------------------------------------------------------------------------

# Путь к лог-файлу Краба
_KRAB_LOG_PATH = pathlib.Path.home() / ".openclaw" / "krab_runtime_state" / "krab_main.log"

# Максимальный размер файла для чтения целиком (5 MB)
_LOG_MAX_INLINE_SIZE = 5 * 1024 * 1024

# Лимит строк для вывода в Telegram (без document)
_LOG_TEXT_MAX_LINES = 200


async def handle_log(bot: "KraabUserbot", message: Message) -> None:
    """Просмотр логов Краба из Telegram.

    Форматы:
      !log [N]              — последние N строк лога (default 20)
      !log errors           — только ошибки (строки с ERROR/error/CRITICAL/WARNING)
      !log search <запрос>  — поиск по логам (grep-like)

    Длинный вывод отправляется как документ (.txt файл).
    """
    args = bot._get_command_args(message).strip()

    # Определяем переменную окружения или дефолтный путь к логу
    raw_env = os.environ.get("KRAB_LOG_FILE")
    if raw_env and raw_env.lower() != "none":
        log_path = pathlib.Path(raw_env).expanduser()
    else:
        base_env = os.environ.get("KRAB_RUNTIME_STATE_DIR")
        if base_env:
            log_path = pathlib.Path(base_env).expanduser() / "krab_main.log"
        else:
            log_path = _KRAB_LOG_PATH

    # Определяем цель: в группе — редирект в ЛС
    _log_target = "me" if message.chat.id < 0 else None
    if message.chat.id < 0:
        try:
            await message.reply("📬 Ответ в ЛС (тех-команда).")
        except Exception:  # noqa: BLE001
            pass

    async def _send_log_text(text: str) -> None:
        """Отправляет текст лога в правильный чат."""
        if _log_target:
            try:
                await bot.client.send_message(_log_target, text)
            except Exception as exc:  # noqa: BLE001
                logger.warning("tech_dm_redirect_failed", error=str(exc))
        else:
            await message.reply(text)

    async def _send_log_document(filepath: pathlib.Path, caption: str) -> None:
        """Отправляет документ лога в правильный чат."""
        target = _log_target or message.chat.id
        await bot.client.send_document(target, str(filepath), caption=caption)

    # Лог-файл должен существовать
    if not log_path.exists():
        await _send_log_text(
            f"📋 Лог-файл не найден: `{log_path}`\nУбедись, что Краб запущен и лог активен."
        )
        return

    # --- Разбор подкоманды ---
    mode = "tail"
    n_lines = 20
    query: str | None = None

    if args:
        lower = args.lower()
        if lower == "errors":
            mode = "errors"
        elif lower.startswith("search "):
            mode = "search"
            query = args[7:].strip()
            if not query:
                raise UserInputError(user_message="🔍 Укажи запрос: `!log search <текст>`")
        else:
            # Пробуем распарсить как число
            try:
                n_lines = max(1, min(int(args), 1000))
            except ValueError:
                raise UserInputError(
                    user_message=(
                        "📋 **!log — просмотр логов Краба**\n\n"
                        "`!log [N]`              — последние N строк (default 20)\n"
                        "`!log errors`           — только ошибки\n"
                        "`!log search <запрос>`  — поиск по логам"
                    )
                )

    # --- Чтение лог-файла ---
    try:
        file_size = log_path.stat().st_size
        if file_size > _LOG_MAX_INLINE_SIZE:
            # Большой файл — читаем через tail subprocess
            lines = _read_log_tail_subprocess(log_path, max(n_lines, 500))
        else:
            lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except (OSError, IOError) as e:
        await _send_log_text(f"❌ Ошибка чтения лога: {e}")
        return

    # --- Фильтрация ---
    if mode == "errors":
        _error_keywords = {"error", "critical", "warning"}
        result_lines = [ln for ln in lines if any(kw in ln.lower() for kw in _error_keywords)]
        header = "⚠️ **Ошибки в логах Краба**"
        if not result_lines:
            await _send_log_text("✅ Ошибок в логах нет.")
            return
    elif mode == "search":
        assert query is not None
        result_lines = [ln for ln in lines if query.lower() in ln.lower()]
        header = f"🔍 **Поиск в логах:** `{query}`"
        if not result_lines:
            await _send_log_text(f"🔍 По запросу `{query}` ничего не найдено.")
            return
    else:
        # tail режим
        result_lines = lines[-n_lines:]
        header = f"📋 **Последние {n_lines} строк лога Краба**"

    # --- Формируем текст ---
    body = "\n".join(result_lines)
    full_text = f"{header}\n\n```\n{body}\n```"

    # --- Отправка: короткий текст → reply/DM, длинный → document ---
    # Lazy import _split_text_for_telegram (остаётся в command_handlers — multi-use).
    from .. import command_handlers as _ch

    if len(full_text) <= 3900 and len(result_lines) <= _LOG_TEXT_MAX_LINES:
        parts = _ch._split_text_for_telegram(full_text)
        for part in parts:
            await _send_log_text(part)
    else:
        # Отправляем как документ
        now = datetime.datetime.now()
        filename = now.strftime("krab_log_%Y-%m-%d_%H-%M.txt")
        tmpdir = pathlib.Path(config.BASE_DIR) / ".runtime" / "log_exports"
        tmpdir.mkdir(parents=True, exist_ok=True)
        filepath = tmpdir / filename

        # Считаем строки — без markdown-обёртки
        export_text = f"{header}\n\n{body}"
        try:
            filepath.write_text(export_text, encoding="utf-8")
            await _send_log_document(filepath, caption=f"📋 {header} ({len(result_lines)} строк)")
        except (OSError, IOError) as e:
            await _send_log_text(f"❌ Ошибка создания файла: {e}")
        finally:
            try:
                filepath.unlink(missing_ok=True)
            except OSError:
                pass


def _read_log_tail_subprocess(log_path: pathlib.Path, n: int) -> list[str]:
    """Читает последние N строк большого лог-файла через subprocess tail."""
    try:
        from ...core.subprocess_env import clean_subprocess_env  # type: ignore[import]

        env = clean_subprocess_env()
    except (ImportError, Exception):
        env = None

    try:
        result = _sp.run(
            ["tail", "-n", str(n), str(log_path)],
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
        )
        return result.stdout.splitlines()
    except (_sp.TimeoutExpired, OSError):
        # Fallback: читаем весь файл и берём хвост
        return log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-n:]


# ---------------------------------------------------------------------------
# !diag — one-shot diagnostic summary (parallel-fetch panel data)
# ---------------------------------------------------------------------------


def _diag_panel_base() -> str:
    """Базовый URL owner panel. Используется KRAB_PANEL_URL для тестов/override."""
    return os.environ.get("KRAB_PANEL_URL", "http://127.0.0.1:8080").rstrip("/")


async def _diag_fetch_json(
    client: "httpx.AsyncClient", path: str, *, timeout: float = 2.5
) -> dict | list | None:
    """Безопасно GET json; возвращает None при любой ошибке (graceful degradation)."""
    try:
        resp = await client.get(_diag_panel_base() + path, timeout=timeout)
        if resp.status_code != 200:
            return None
        return resp.json()
    except Exception:  # noqa: BLE001
        return None


def _diag_fmt_section_infra(bot: "KraabUserbot", health: dict | None) -> list[str]:
    """Infrastructure секция: uptime + ключевые порты/процессы."""
    lines: list[str] = ["🖥 Infrastructure"]
    try:
        elapsed = time.time() - bot._session_start_time
        lines.append(f"  • Krab uptime: {_format_uptime_str(elapsed)}")
    except Exception:  # noqa: BLE001
        lines.append("  • Krab uptime: N/A")

    services = [
        ("OpenClaw Gateway", "openclaw_gateway", 18789),
        ("MCP yung-nagato", "mcp_yung_nagato", 8011),
        ("MCP p0lrd", "mcp_p0lrd", 8012),
        ("LM Studio", "lm_studio", 1234),
        ("Cloudflared", "cloudflared", None),
    ]
    data = (health or {}).get("services") if isinstance(health, dict) else None
    for label, key, port in services:
        status = "⚠️ unknown"
        if isinstance(data, dict):
            svc = data.get(key) or {}
            if svc.get("ok") is True:
                status = "✅"
            elif svc.get("ok") is False:
                status = "❌"
            detail = svc.get("detail") or svc.get("status") or ""
            suffix = f" :{port}" if port else ""
            if detail:
                lines.append(f"  • {label}: {status}{suffix} ({detail})")
                continue
        suffix = f" :{port}" if port else ""
        lines.append(f"  • {label}: {status}{suffix}")
    return lines


def _diag_fmt_section_model(model_status: dict | None) -> list[str]:
    """Model routing — активная модель, tier, последний роутинг."""
    lines = ["", "📊 Model routing"]
    if not isinstance(model_status, dict):
        lines.append("  • Данные недоступны")
        return lines
    active = model_status.get("active") or model_status.get("model") or "unknown"
    tier = model_status.get("tier") or model_status.get("cloud_tier") or "n/a"
    last_route = model_status.get("last_route") or model_status.get("last_route_ago") or "n/a"
    lines.append(f"  • Active: {active}")
    lines.append(f"  • Tier: {tier}")
    lines.append(f"  • Last route: {last_route}")
    return lines


def _diag_fmt_section_traffic(stats: dict | None, costs: dict | None) -> list[str]:
    """Трафик + стоимость за час."""
    lines = ["", "💬 Traffic (1h)"]
    if isinstance(stats, dict):
        lines.append(
            f"  • Messages processed: {stats.get('messages_1h', stats.get('messages', 0))}"
        )
        lines.append(f"  • LLM calls: {stats.get('llm_calls_1h', stats.get('llm_calls', 0))}")
        lines.append(f"  • Swarm rounds: {stats.get('swarm_rounds_1h', 0)}")
    else:
        lines.append("  • Статистика недоступна")
    if isinstance(costs, dict):
        spent = costs.get("spent") or costs.get("today") or 0
        budget = costs.get("budget") or costs.get("limit") or 0
        pct = 0
        try:
            pct = int((float(spent) / float(budget)) * 100) if float(budget) else 0
        except Exception:  # noqa: BLE001
            pass
        lines.append(f"  • Cost: ${spent} / ${budget} budget ({pct}%)")
    return lines


def _diag_fmt_section_memory(mem: dict | None) -> list[str]:
    """Memory: archive size, retrieval режим, latency."""
    lines = ["", "🧠 Memory"]
    if not isinstance(mem, dict):
        lines.append("  • Данные недоступны")
        return lines
    archive = mem.get("archive") or {}
    size_mb = archive.get("size_mb") or mem.get("archive_size_mb") or 0
    chunks = archive.get("chunks") or mem.get("chunks") or 0
    vec = archive.get("vec") or mem.get("vec") or chunks
    lines.append(f"  • Archive.db: {size_mb} MB ({chunks} chunks / {vec} vec)")
    mode = mem.get("retrieval_mode") or "hybrid"
    lines.append(f"  • Retrieval mode: {mode}")
    lat = mem.get("latency") or {}
    if lat:
        lines.append(
            f"  • Latency P50 — FTS: {lat.get('fts_p50', '?')}ms / "
            f"Vec: {lat.get('vec_p50', '?')}ms / MMR: {lat.get('mmr_p50', '?')}ms"
        )
    return lines


def _diag_fmt_section_errors(alerts: dict | list | None) -> list[str]:
    """Ошибки / Sentry / digest."""
    lines = ["", "⚠️ Errors (last 24h)"]
    if isinstance(alerts, dict):
        active = alerts.get("active") or alerts.get("alerts") or []
    elif isinstance(alerts, list):
        active = alerts
    else:
        active = None
    if active is None:
        lines.append("  • Данные недоступны")
        return lines
    lines.append(f"  • Active alerts: {len(active)}")
    if active:
        top = active[0] if isinstance(active[0], dict) else {}
        code = top.get("code") or top.get("name") or "alert"
        msg = top.get("message") or top.get("summary") or ""
        lines.append(f"  • Top: {code} — {msg[:80]}")
    return lines


def _diag_fmt_section_inbox(inbox: dict | None) -> list[str]:
    """Inbox items."""
    lines = ["", "📬 Inbox"]
    if not isinstance(inbox, dict):
        lines.append("  • Данные недоступны")
        return lines
    lines.append(f"  • Open items: {inbox.get('open_items', 0)}")
    lines.append(f"  • Stale (>24h): {inbox.get('stale_items', 0)}")
    return lines


def _diag_fmt_section_cron(cron: dict | list | None) -> list[str]:
    """Cron jobs — сегодняшние fires."""
    lines = ["", "✅ Cron (today)"]
    jobs: list[dict] = []
    if isinstance(cron, dict):
        raw = cron.get("jobs") or cron.get("items") or []
        if isinstance(raw, list):
            jobs = [j for j in raw if isinstance(j, dict)]
    elif isinstance(cron, list):
        jobs = [j for j in cron if isinstance(j, dict)]
    if not jobs:
        lines.append("  • Нет активных cron-задач")
        return lines
    for job in jobs[:6]:
        name = job.get("name") or job.get("id") or "job"
        last = job.get("last_fire") or job.get("last_run") or "—"
        fires = job.get("fires_today") or job.get("runs_today") or 0
        fires_str = f" (×{fires})" if fires else ""
        lines.append(f"  • {name}: {last}{fires_str}")
    return lines


def _diag_fmt_section_phase2(phase2: dict | None) -> list[str]:
    """Memory Phase 2 — показываем только если enabled или shadow."""
    if not isinstance(phase2, dict):
        return []
    flag = str(phase2.get("flag") or "disabled").lower()
    if flag not in ("enabled", "shadow"):
        return []
    lines = ["", "🧠 Memory Phase 2"]
    lines.append(f"  • Flag: {flag}")
    model_loaded = phase2.get("model_loaded")
    model_dim = phase2.get("model_dim") or 0
    if model_loaded is True:
        lines.append(f"  • Model2Vec: loaded ({model_dim} dim)")
    elif model_loaded is False:
        lines.append("  • Model2Vec: failed")
    else:
        lines.append("  • Model2Vec: loading")
    vec = phase2.get("vec_chunks_count") or 0
    join_pct = phase2.get("vec_join_pct")
    join_str = f" ({join_pct}% JOIN match)" if join_pct is not None else ""
    lines.append(f"  • vec_chunks: {vec}{join_str}")
    modes = phase2.get("retrieval_mode_hour") or {}
    if modes:
        lines.append(
            "  • Retrieval mode last hour: "
            f"fts={modes.get('fts', 0)} / vec={modes.get('vec', 0)} / "
            f"hybrid={modes.get('hybrid', 0)} / none={modes.get('none', 0)}"
        )
    lat = phase2.get("latency_avg") or {}
    if lat:
        lines.append(
            f"  • Avg latency: FTS {lat.get('fts', '?')}ms / "
            f"Vec {lat.get('vec', '?')}ms / MMR {lat.get('mmr', '?')}ms / "
            f"Total {lat.get('total', '?')}ms"
        )
    if flag == "shadow":
        delta = phase2.get("shadow_delta_pct")
        if delta is not None:
            lines.append(f"  • Shadow delta: {delta}% queries would change top-5")
    return lines


def _diag_fmt_section_sentry(sentry: dict | None) -> list[str]:
    """Sentry 24h breakdown — unresolved + top groups."""
    lines = ["", "🔔 Sentry (24h)"]
    if not isinstance(sentry, dict):
        lines.append("  • Данные недоступны (SENTRY_AUTH_TOKEN?)")
        return lines
    unresolved = sentry.get("unresolved") or 0
    by_project = sentry.get("unresolved_by_project") or {}
    if by_project:
        parts_prj = ", ".join(f"{k}: {v}" for k, v in list(by_project.items())[:4])
        lines.append(f"  • Unresolved: {unresolved} ({parts_prj})")
    else:
        lines.append(f"  • Unresolved: {unresolved}")
    top = sentry.get("top_groups") or []
    if top:
        top_str = ", ".join(
            f"{g.get('title', 'err')}({g.get('count', 0)})" for g in top[:3] if isinstance(g, dict)
        )
        lines.append(f"  • Top groups: {top_str}")
    auto = sentry.get("auto_resolved_today")
    if auto is not None:
        lines.append(f"  • Auto-resolved сегодня: {auto}")
    rate = sentry.get("trace_sample_rate")
    if rate is not None:
        lines.append(f"  • Trace sample rate: {rate}")
    return lines


def _diag_fmt_section_security(sec: dict | None) -> list[str]:
    """Security + Guards активность за 24h."""
    lines = ["", "🛡️ Security"]
    if not isinstance(sec, dict):
        lines.append("  • Данные недоступны")
        return lines
    lines.append(f"  • Phantom guard: {sec.get('phantom_guard_matched', 0)} caught")
    blocklist = sec.get("command_blocklist_skip", 0)
    blocklist_detail = sec.get("blocklist_detail") or ""
    suffix = f" ({blocklist_detail})" if blocklist_detail else ""
    lines.append(f"  • Command blocklist silent skips: {blocklist}{suffix}")
    pii = sec.get("operator_pii_sanitized", 0)
    pii_note = "no leaks detected" if not pii else "redactions applied"
    lines.append(f"  • Operator PII redactions: {pii} ({pii_note})")
    swarm = sec.get("swarm_tool_blocked", 0)
    swarm_note = "no attempts to escape allowlist" if not swarm else "attempts blocked"
    lines.append(f"  • Swarm tool blocked: {swarm} ({swarm_note})")
    return lines


async def _diag_fetch_sentry() -> dict | None:
    """Читает Sentry Issues API (24h). None, если нет токена или ошибка."""
    token = os.environ.get("SENTRY_AUTH_TOKEN")
    if not token:
        return None
    org = os.environ.get("SENTRY_ORG", "krab")
    base = os.environ.get("SENTRY_BASE_URL", "https://sentry.io").rstrip("/")
    url = f"{base}/api/0/organizations/{org}/issues/"
    headers = {"Authorization": f"Bearer {token}"}
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(
                url,
                headers=headers,
                params={"statsPeriod": "24h", "query": "is:unresolved"},
            )
            if resp.status_code != 200:
                return None
            issues = resp.json() or []
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(issues, list):
        return None
    by_project: dict[str, int] = {}
    top_groups: list[dict] = []
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        prj = (issue.get("project") or {}).get("slug") or "unknown"
        by_project[prj] = by_project.get(prj, 0) + 1
        try:
            count = int(issue.get("count", 0))
        except Exception:  # noqa: BLE001
            count = 0
        top_groups.append({"title": issue.get("title") or "err", "count": count})
    top_groups.sort(key=lambda g: g.get("count", 0), reverse=True)
    return {
        "unresolved": len(issues),
        "unresolved_by_project": by_project,
        "top_groups": top_groups[:5],
        "trace_sample_rate": os.environ.get("SENTRY_TRACES_SAMPLE_RATE", "10%"),
    }


async def _diag_collect_security() -> dict | None:
    """Grep последних логов за 24h для подсчёта security-событий."""
    import pathlib
    import re as _re

    log_path = pathlib.Path(
        os.environ.get("KRAB_LOG_PATH", os.path.expanduser("~/.openclaw/logs/krab.log"))
    )
    counts = {
        "phantom_guard_matched": 0,
        "command_blocklist_skip": 0,
        "operator_pii_sanitized": 0,
        "swarm_tool_blocked": 0,
    }
    if not log_path.exists():
        return counts
    patterns = {k: _re.compile(k) for k in counts}
    cutoff = time.time() - 24 * 3600
    try:
        # читаем последние ~20 МБ чтобы не убить IO
        with log_path.open("rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - 20 * 1024 * 1024))
            tail = fh.read().decode("utf-8", errors="ignore")
    except Exception:  # noqa: BLE001
        return counts
    for line in tail.splitlines():
        # быстрое отсечение по cutoff, если строка содержит ISO-дату
        for key, rx in patterns.items():
            if rx.search(line):
                counts[key] += 1
    # best-effort note: без строгой фильтрации по времени — tail ~= 24h на активном боте
    _ = cutoff
    return counts


async def handle_diag(bot: "KraabUserbot", message: Message) -> None:
    """
    !diag — one-shot diagnostic summary для владельца.

    Собирает: infra health, model routing, traffic/cost, memory stats, errors,
    inbox, cron. Запросы к owner panel параллельные (~500ms суммарно).
    Owner-only.
    """
    # Owner-only guard
    try:
        access_profile = bot._get_access_profile(message.from_user)
    except Exception:  # noqa: BLE001
        access_profile = None
    if access_profile is None or access_profile.level != AccessLevel.OWNER:
        await message.reply("🔒 `!diag` — только для владельца.")
        return

    # Lazy proxy через command_handlers namespace — позволяет тестам
    # ``test_handle_diag`` патчить ``command_handlers.httpx``,
    # ``_diag_fetch_sentry``, ``_diag_collect_security``, etc.
    from .. import command_handlers as _ch

    # Параллельные GET к owner panel для скорости (~500ms вместо 5s sequential)
    async with _ch.httpx.AsyncClient(timeout=2.5) as client:
        results = await asyncio.gather(
            _ch._diag_fetch_json(client, "/api/health/lite"),
            _ch._diag_fetch_json(client, "/api/model/status"),
            _ch._diag_fetch_json(client, "/api/stats"),
            _ch._diag_fetch_json(client, "/api/costs/budget"),
            _ch._diag_fetch_json(client, "/api/memory/stats"),
            _ch._diag_fetch_json(client, "/api/ops/alerts"),
            _ch._diag_fetch_json(client, "/api/inbox/status"),
            _ch._diag_fetch_json(client, "/api/openclaw/cron/jobs"),
            _ch._diag_fetch_json(client, "/api/memory/phase2/status"),
            _ch._diag_fetch_sentry(),
            _ch._diag_collect_security(),
            return_exceptions=True,
        )
    (
        health,
        model_status,
        stats,
        costs,
        memory,
        alerts,
        inbox,
        cron,
        phase2,
        sentry,
        security,
    ) = [(r if not isinstance(r, BaseException) else None) for r in results]

    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    parts: list[str] = [f"🦀 Krab Diagnostics @ {now}", ""]
    parts.extend(_diag_fmt_section_infra(bot, health))
    parts.extend(_diag_fmt_section_model(model_status))
    parts.extend(_diag_fmt_section_traffic(stats, costs))
    parts.extend(_diag_fmt_section_memory(memory))
    parts.extend(_diag_fmt_section_errors(alerts))
    parts.extend(_diag_fmt_section_inbox(inbox))
    parts.extend(_diag_fmt_section_cron(cron))
    parts.extend(_diag_fmt_section_phase2(phase2))
    parts.extend(_diag_fmt_section_sentry(sentry))
    parts.extend(_diag_fmt_section_security(security))

    text = "\n".join(parts)
    # Telegram message limit — 4096 chars; обрезаем с пометкой
    if len(text) > 4000:
        text = text[:3990] + "\n…(обрезано)"
    # parse_mode=None — безопаснее для ru-текста со спецсимволами
    await message.reply(text, parse_mode=None)
