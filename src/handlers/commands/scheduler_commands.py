# -*- coding: utf-8 -*-
"""
scheduler_commands — Phase 2 Wave 3 extraction (Session 27).

Команды управления временем и расписанием:
  !timer, !stopwatch, !remind, !reminders, !rm_remind,
  !schedule, !autodel, !todo, !cron, !cronstatus.

Включает helpers _parse_duration / _fmt_duration и module-level state
(_active_timers, _stopwatches, _timer_counter, _AUTODEL_STATE_KEY).
Re-exported из command_handlers.py для обратной совместимости.

См. ``docs/CODE_SPLITS_PLAN.md`` § Phase 2 — domain extractions.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import pathlib
import re
import time
from typing import TYPE_CHECKING, Any

from pyrogram.types import Message

from ...config import config
from ...core.access_control import AccessLevel
from ...core.exceptions import UserInputError
from ...core.logger import get_logger
from ...core.scheduler import krab_scheduler, parse_due_time, split_reminder_input

if TYPE_CHECKING:
    from ...userbot_bridge import KraabUserbot

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# In-memory state (timers / stopwatches / autodel)
# ---------------------------------------------------------------------------

# Структура: {timer_id: {"task": asyncio.Task, "label": str, "ends_at": float, "chat_id": int}}
_active_timers: dict[int, dict] = {}
_timer_counter: int = 0  # счётчик для ID

# Структура: {chat_id: {"started_at": float, "laps": list[float]}}
_stopwatches: dict[int, dict] = {}

# Ключ в _runtime_state бота для хранения настроек autodel по чатам
_AUTODEL_STATE_KEY = "autodel_settings"


# ---------------------------------------------------------------------------
# Helpers: parse / format duration
# ---------------------------------------------------------------------------


def _parse_duration(spec: str) -> int | None:
    """Парсит строку вида 5m, 1h30m, 90s в секунды. Возвращает None при ошибке."""
    spec = spec.strip().lower()
    pattern = re.compile(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?$")
    m = pattern.fullmatch(spec)
    if m and any(m.groups()):
        h = int(m.group(1) or 0)
        mins = int(m.group(2) or 0)
        s = int(m.group(3) or 0)
        total = h * 3600 + mins * 60 + s
        return total if total > 0 else None
    if spec.isdigit():
        return int(spec) or None
    return None


def _fmt_duration(seconds: float) -> str:
    """Форматирует секунды в читаемую строку (1ч 5м 3с)."""
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    parts = []
    if h:
        parts.append(f"{h}ч")
    if m:
        parts.append(f"{m}м")
    if s or not parts:
        parts.append(f"{s}с")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Autodel helpers
# ---------------------------------------------------------------------------


async def _delete_after(client: Any, chat_id: int, message_id: int, delay: float) -> None:
    """Удаляет сообщение после задержки (внутренняя утилита для autodel)."""
    await asyncio.sleep(delay)
    try:
        await client.delete_messages(chat_id, message_ids=[message_id])
    except Exception as exc:  # noqa: BLE001
        logger.debug("autodel_failed", chat_id=chat_id, msg_id=message_id, error=str(exc))


def schedule_autodel(client: Any, chat_id: int, message_id: int, delay: float) -> None:
    """Планирует отложенное удаление сообщения Краба."""
    asyncio.create_task(
        _delete_after(client, chat_id, message_id, delay),
        name=f"autodel_{chat_id}_{message_id}",
    )


def get_autodel_delay(bot: "KraabUserbot", chat_id: int) -> float | None:
    """Возвращает задержку autodel для чата (в секундах) или None."""
    state: dict = getattr(bot, "_runtime_state", {}) or {}
    settings: dict = state.get(_AUTODEL_STATE_KEY, {})
    delay = settings.get(str(chat_id))
    if delay is None or delay <= 0:
        return None
    return float(delay)


def _set_autodel_delay(bot: "KraabUserbot", chat_id: int, delay: float) -> None:
    """Сохраняет настройку autodel в _runtime_state бота."""
    if not hasattr(bot, "_runtime_state") or bot._runtime_state is None:
        bot._runtime_state = {}
    settings: dict = bot._runtime_state.setdefault(_AUTODEL_STATE_KEY, {})
    if delay <= 0:
        settings.pop(str(chat_id), None)
    else:
        settings[str(chat_id)] = delay


# ---------------------------------------------------------------------------
# Reply helpers
# ---------------------------------------------------------------------------


async def _reply_tech(message: Message, bot: "KraabUserbot", text: str, **kwargs: Any) -> None:
    """Тех-ответ: в группе — в ЛС, в ЛС — обычный reply."""
    chat = getattr(message, "chat", None)
    chat_id = getattr(chat, "id", 0) if chat is not None else 0
    if chat_id < 0:
        try:
            await message.reply("📬 Ответ в ЛС (тех-команда).")
        except Exception:  # noqa: BLE001
            pass
        try:
            await bot.client.send_message("me", text, **kwargs)
        except Exception as exc:  # noqa: BLE001
            logger.warning("tech_dm_redirect_failed", error=str(exc))
    else:
        await message.reply(text, **kwargs)


def _split_text_for_telegram(text: str, limit: int = 3900) -> list[str]:
    """Делит длинный текст на части с сохранением границ строк."""
    lines = text.splitlines()
    chunks: list[str] = []
    current = ""
    for line in lines:
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
        if len(line) <= limit:
            current = line
        else:
            for i in range(0, len(line), limit):
                part = line[i : i + limit]
                if len(part) == limit:
                    chunks.append(part)
                else:
                    current = part
    if current:
        chunks.append(current)
    return chunks or [text[:limit]]


# ---------------------------------------------------------------------------
# !remind — напоминания
# ---------------------------------------------------------------------------

_REMIND_HELP = (
    "⏰ **Напоминания — Формат:**\n\n"
    "**Создать (по времени):**\n"
    "- `!remind me in 30m купить молоко`\n"
    "- `!remind in 2 hours позвонить`\n"
    "- `!remind at 15:00 встреча`\n"
    "- `!remind tomorrow 9:00 зарядка`\n"
    "- `!remind через 20 минут проверить почту`\n"
    "- `!remind в 18:30 созвон`\n"
    "- `!remind 10m | выпить воды`\n\n"
    "**Создать (по событию):**\n"
    "- `!remind when upload photos then notify me`\n"
    "- `!remind когда upload photos сделай напомнить`\n\n"
    "**Управление:**\n"
    "- `!remind list` — список активных\n"
    "- `!remind cancel <id>` — отменить\n"
)


async def handle_remind(bot: "KraabUserbot", message: Message) -> None:
    """Управляет напоминаниями с natural language парсингом."""
    if not bool(getattr(config, "SCHEDULER_ENABLED", False)):
        raise UserInputError(
            user_message=(
                "⏰ Scheduler сейчас выключен (`SCHEDULER_ENABLED=0`).\n"
                "Включи его (`!set SCHEDULER_ENABLED 1`) и перезапусти Krab."
            )
        )

    raw_args = bot._get_command_args(message).strip()

    if raw_args.lower() in ("list", "список", "ls"):
        rows = krab_scheduler.list_reminders(chat_id=str(message.chat.id))
        try:
            from ...core.reminders_queue import reminders_queue as _rq

            owner_id = str(getattr(message.from_user, "id", "") or "")
            event_rows = _rq.list_pending(owner_id=owner_id) if owner_id else []
        except Exception:  # noqa: BLE001
            event_rows = []

        if not rows and not event_rows:
            await message.reply("⏰ Активных напоминаний нет.")
            return
        lines = ["⏰ **Активные напоминания:**"]
        for item in rows:
            due_raw = str(item.get("due_at_iso") or "")
            try:
                from datetime import datetime as _dt

                due_label = _dt.fromisoformat(due_raw).strftime("%d.%m %H:%M")
            except Exception:  # noqa: BLE001
                due_label = due_raw
            text = str(item.get("text") or "")
            rid = str(item.get("reminder_id") or "")
            lines.append(f"- `{rid}` · `{due_label}` · {text}")
        for r in event_rows:
            if r.trigger_type.value == "time" and r.fire_at:
                when = datetime.datetime.fromtimestamp(r.fire_at).strftime("%d.%m %H:%M")
                lines.append(f"- `{r.id}` · `{when}` · {r.action_payload[:120]}")
            elif r.trigger_type.value == "event":
                lines.append(f"- `{r.id}` · when `{r.match_pattern}` · {r.action_payload[:120]}")
        payload = "\n".join(lines)
        chunks = _split_text_for_telegram(payload, limit=3600)
        await message.reply(chunks[0])
        for part in chunks[1:]:
            await message.reply(part)
        return

    cancel_match = re.match(r"^(?:cancel|отмена|rm|del)\s+(\S+)$", raw_args, re.IGNORECASE)
    if cancel_match:
        rid = cancel_match.group(1)
        ok = krab_scheduler.remove_reminder(rid)
        if not ok:
            try:
                from ...core.reminders_queue import reminders_queue as _rq

                ok = _rq.cancel(rid)
            except Exception:  # noqa: BLE001
                ok = False
        if ok:
            await message.reply(f"🗑️ Напоминание `{rid}` отменено.")
        else:
            await message.reply(f"⚠️ Напоминание `{rid}` не найдено.")
        return

    if not raw_args:
        raise UserInputError(user_message=_REMIND_HELP)

    from ...core.remind_parser import parse_remind_args
    from ...core.reminders_queue import reminders_queue

    _spec = parse_remind_args(raw_args)
    if _spec is not None and _spec.get("type") == "event":
        owner_id = str(getattr(message.from_user, "id", "") or "")
        chat_id = str(message.chat.id)
        rid = reminders_queue.add_event_reminder(
            owner_id=owner_id,
            chat_id=chat_id,
            pattern=str(_spec["pattern"]),
            action=str(_spec["action"]),
        )
        await message.reply(
            "👁️ Event-напоминание создано в этом чате.\n"
            f"- ID: `{rid}`\n"
            f"- Pattern: `{_spec['pattern']}`\n"
            f"- Action: {_spec['action']}\n\n"
            f"Отменить: `!remind cancel {rid}`"
        )
        return

    time_spec, reminder_text = split_reminder_input(raw_args)
    if not time_spec or not reminder_text:
        raise UserInputError(
            user_message=("⏰ Не удалось разобрать время/текст.\n\n" + _REMIND_HELP)
        )

    try:
        due_at = parse_due_time(time_spec)
    except ValueError:
        raise UserInputError(user_message=("❌ Не удалось распознать время.\n\n" + _REMIND_HELP))

    if hasattr(bot, "_sync_scheduler_runtime"):
        try:
            bot._sync_scheduler_runtime()
        except Exception as exc:  # noqa: BLE001
            logger.warning("scheduler_runtime_sync_failed_in_remind", error=str(exc))

    if not krab_scheduler.is_started:
        try:
            krab_scheduler.start()
        except RuntimeError:
            raise UserInputError(user_message="❌ Scheduler не запущен в runtime loop.")

    reminder_id = krab_scheduler.add_reminder(
        chat_id=str(message.chat.id),
        text=reminder_text,
        due_at=due_at,
    )
    due_label = due_at.astimezone().strftime("%d.%m.%Y %H:%M")
    await message.reply(
        "✅ Напоминание создано.\n"
        f"- ID: `{reminder_id}`\n"
        f"- Когда: `{due_label}`\n"
        f"- Текст: {reminder_text}\n\n"
        f"Отменить: `!remind cancel {reminder_id}`"
    )


async def handle_reminders(bot: "KraabUserbot", message: Message) -> None:
    """Показывает pending reminders текущего чата."""
    rows = krab_scheduler.list_reminders(chat_id=str(message.chat.id))
    if not rows:
        await message.reply("⏰ Активных напоминаний нет.")
        return
    lines = ["⏰ **Активные напоминания:**"]
    for item in rows:
        due = str(item.get("due_at_iso") or "")
        text = str(item.get("text") or "")
        rid = str(item.get("reminder_id") or "")
        lines.append(f"- `{rid}` · `{due}` · {text}")
    payload = "\n".join(lines)
    chunks = _split_text_for_telegram(payload, limit=3600)
    await message.reply(chunks[0])
    for part in chunks[1:]:
        await message.reply(part)


async def handle_rm_remind(bot: "KraabUserbot", message: Message) -> None:
    """Удаляет reminder по ID."""
    raw_args = bot._get_command_args(message).strip()
    if not raw_args:
        raise UserInputError(user_message="🗑️ Формат: `!rm_remind <id>`")
    ok = krab_scheduler.remove_reminder(raw_args)
    if ok:
        await message.reply(f"🗑️ Напоминание `{raw_args}` удалено.")
    else:
        await message.reply(f"⚠️ Напоминание `{raw_args}` не найдено.")


async def handle_cronstatus(bot: "KraabUserbot", message: Message) -> None:
    """Отдает runtime-статус scheduler."""
    status = krab_scheduler.get_status()
    await _reply_tech(
        message,
        bot,
        "🧭 **Scheduler status**\n"
        f"- enabled (config): `{status.get('scheduler_enabled')}`\n"
        f"- started: `{status.get('started')}`\n"
        f"- pending: `{status.get('pending_count')}`\n"
        f"- next_due_at: `{status.get('next_due_at') or '-'}`\n"
        f"- storage: `{status.get('storage_path')}`",
    )


# ---------------------------------------------------------------------------
# !cron — управление OpenClaw cron jobs
# ---------------------------------------------------------------------------


def _cron_read_jobs() -> list[dict]:
    """Читает jobs.json напрямую (без gateway), возвращает список dict."""
    jobs_path = pathlib.Path.home() / ".openclaw" / "cron" / "jobs.json"
    try:
        data = json.loads(jobs_path.read_text(encoding="utf-8", errors="replace"))
        return data.get("jobs", []) if isinstance(data, dict) else []
    except (OSError, json.JSONDecodeError):
        return []


def _cron_write_jobs(jobs: list[dict]) -> None:
    """Записывает обновлённый список jobs обратно в jobs.json."""
    jobs_path = pathlib.Path.home() / ".openclaw" / "cron" / "jobs.json"
    payload = {"version": 1, "jobs": jobs}
    jobs_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _cron_format_schedule(job: dict) -> str:
    """Возвращает читаемую строку расписания для job."""
    schedule = job.get("schedule") or {}
    kind = str(schedule.get("kind") or "unknown").lower()
    if kind == "every":
        every_ms = int(schedule.get("everyMs") or 0)
        if every_ms <= 0:
            return "каждые ?"
        if every_ms % 3600000 == 0:
            return f"каждые {every_ms // 3600000}ч"
        if every_ms % 60000 == 0:
            return f"каждые {every_ms // 60000}м"
        return f"каждые {every_ms // 1000}с"
    if kind == "cron":
        expr = str(schedule.get("expr") or "?")
        tz = str(schedule.get("tz") or "").strip()
        return f"cron `{expr}`" + (f" ({tz})" if tz else "")
    return kind


def _cron_format_last_status(job: dict) -> str:
    """Форматирует последний статус job."""
    state = job.get("state") or {}
    status = str(state.get("lastStatus") or state.get("lastRunStatus") or "—")
    errors = int(state.get("consecutiveErrors") or 0)
    if errors > 0:
        return f"{status} ⚠️ ({errors} ош.)"
    return status


async def _cron_run_openclaw(*args: str, timeout: float = 30.0) -> tuple[bool, str]:
    """Запускает `openclaw` CLI с переданными аргументами через asyncio."""
    from ...core.openclaw_cli_budget import get_global_semaphore, terminate_and_reap
    from ...core.subprocess_env import clean_subprocess_env

    try:
        async with get_global_semaphore():
            proc = await asyncio.create_subprocess_exec(
                "openclaw",
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=clean_subprocess_env(),
            )
            try:
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            except asyncio.TimeoutError:
                await terminate_and_reap(proc)
                return False, "timeout"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)

    raw = stdout.decode("utf-8", errors="replace").strip()
    return proc.returncode == 0, raw


async def handle_cron(bot: "KraabUserbot", message: Message) -> None:
    """Управление OpenClaw cron jobs из Telegram."""
    access_profile = bot._get_access_profile(message.from_user)
    if access_profile.level != AccessLevel.OWNER:
        raise UserInputError(user_message="🔒 Команда доступна только владельцу.")

    raw_args = bot._get_command_args(message).strip()
    parts = raw_args.split(maxsplit=1)
    sub = parts[0].lower() if parts else "list"
    arg = parts[1].strip() if len(parts) > 1 else ""

    if sub in {"status", "stat", "статус"}:
        jobs = _cron_read_jobs()
        total = len(jobs)
        enabled = sum(1 for j in jobs if j.get("enabled"))
        disabled = total - enabled
        errors = sum(
            1 for j in jobs if int((j.get("state") or {}).get("consecutiveErrors") or 0) > 0
        )
        lines = [
            "🗓 **OpenClaw Cron — статус**",
            f"Всего jobs: **{total}**",
            f"• включено: {enabled}",
            f"• выключено: {disabled}",
            f"• с ошибками: {errors}",
        ]
        await _reply_tech(message, bot, "\n".join(lines))
        return

    if sub in {"list", "ls", "список", ""}:
        jobs = _cron_read_jobs()
        if not jobs:
            await message.reply("🗓 Cron jobs не найдены.")
            return
        jobs_sorted = sorted(jobs, key=lambda j: (not j.get("enabled"), str(j.get("name") or "")))
        lines = ["🗓 **OpenClaw Cron Jobs**\n"]
        for job in jobs_sorted:
            flag = "✅" if job.get("enabled") else "⏸"
            name = str(job.get("name") or job.get("id") or "?")
            schedule = _cron_format_schedule(job)
            last_status = _cron_format_last_status(job)
            lines.append(f"{flag} **{name}**")
            lines.append(f"   расписание: {schedule} | статус: `{last_status}`")
        await _reply_tech(message, bot, "\n".join(lines))
        return

    if sub in {"enable", "вкл", "включить", "disable", "выкл", "выключить"}:
        if not arg:
            action_word = "enable" if sub in {"enable", "вкл", "включить"} else "disable"
            raise UserInputError(
                user_message=f"❌ Укажи имя или id job: `!cron {action_word} <name>`"
            )
        enable = sub in {"enable", "вкл", "включить"}
        action = "enable" if enable else "disable"

        jobs = _cron_read_jobs()
        matched = [
            j
            for j in jobs
            if str(j.get("name") or "").lower() == arg.lower()
            or str(j.get("id") or "").lower() == arg.lower()
        ]
        if not matched:
            raise UserInputError(user_message=f"❌ Job `{arg}` не найден.")

        job_id = str(matched[0].get("id") or "")
        job_name = str(matched[0].get("name") or job_id)

        ok, raw = await _cron_run_openclaw("cron", action, job_id)

        if ok:
            emoji = "✅" if enable else "⏸"
            verb = "включён" if enable else "выключен"
            await message.reply(f"{emoji} Job **{job_name}** {verb}.")
        else:
            logger.warning("cron_openclaw_cli_failed_patching_directly", action=action, raw=raw)
            for j in jobs:
                if str(j.get("id") or "") == job_id:
                    j["enabled"] = enable
            _cron_write_jobs(jobs)
            emoji = "✅" if enable else "⏸"
            verb = "включён" if enable else "выключен"
            await message.reply(
                f"{emoji} Job **{job_name}** {verb} (direct patch, gateway offline)."
            )
        return

    if sub in {"run", "запустить", "запуск"}:
        if not arg:
            raise UserInputError(user_message="❌ Укажи имя или id job: `!cron run <name>`")

        jobs = _cron_read_jobs()
        matched = [
            j
            for j in jobs
            if str(j.get("name") or "").lower() == arg.lower()
            or str(j.get("id") or "").lower() == arg.lower()
        ]
        if not matched:
            raise UserInputError(user_message=f"❌ Job `{arg}` не найден.")

        job_id = str(matched[0].get("id") or "")
        job_name = str(matched[0].get("name") or job_id)

        msg = await message.reply(f"⏳ Запускаю job **{job_name}**…")
        ok, raw = await _cron_run_openclaw("cron", "run", job_id, timeout=60.0)

        short_raw = raw[:200] if raw else ""
        if ok:
            text = (
                f"✅ Job **{job_name}** запущен.\n`{short_raw}`"
                if short_raw
                else f"✅ Job **{job_name}** запущен."
            )
            await msg.edit(text)
        else:
            text = (
                f"❌ Ошибка запуска **{job_name}**:\n`{short_raw}`"
                if short_raw
                else f"❌ Ошибка запуска **{job_name}**."
            )
            await msg.edit(text)
        return

    if sub in {"quick", "быстро", "add"}:
        await _handle_cron_quick(bot, message, arg)
        return

    if sub == "native":
        await _handle_cron_native(message, arg)
        return

    await message.reply(
        "🗓 **!cron** — управление OpenClaw cron jobs\n\n"
        "`!cron list` — список всех jobs\n"
        "`!cron enable <name>` — включить job\n"
        "`!cron disable <name>` — выключить job\n"
        "`!cron run <name>` — запустить job немедленно\n"
        "`!cron status` — общая статистика\n"
        '`!cron quick "<время>" "<промпт>"` — создать job в текущем чате\n'
        '   Пример: `!cron quick "каждый день в 10:00" "AI-ресёрч и саммари"`\n'
        "`!cron native list` — native fallback jobs\n"
        '`!cron native add "0 10 * * *" "промпт"` — добавить native job'
    )


async def _handle_cron_quick(bot: "KraabUserbot", message: Message, args: str) -> None:
    """!cron quick "<время>" "<промпт>" — создаёт per-chat recurring cron job."""
    import shlex

    from ...core.cron_spec_parser import parse_cron_expression

    raw = (args or "").strip()
    if not raw:
        await message.reply(
            '❌ Формат: `!cron quick "<время>" "<промпт>"`\n'
            'Пример: `!cron quick "каждый день в 10:00" "AI+crypto ресёрч, саммари"`',
        )
        return

    try:
        parts = shlex.split(raw)
    except ValueError:
        await message.reply(
            '❌ Не распарсил аргументы. Используй кавычки: `!cron quick "время" "промпт"`',
        )
        return

    if len(parts) < 2:
        await message.reply(
            "❌ Нужно 2 аргумента: время + промпт. "
            'Пример: `!cron quick "каждые 2 часа" "проверь BTC/ETH"`',
        )
        return

    time_expr = parts[0]
    prompt = " ".join(parts[1:]).strip()
    if not prompt:
        await message.reply("❌ Промпт не может быть пустым.")
        return

    cron_spec = parse_cron_expression(time_expr)
    if not cron_spec:
        await message.reply(
            f"❌ Не распарсил время: `{time_expr}`.\nПримеры:\n"
            "• `каждый день в 10:00`\n"
            "• `каждые 2 часа`\n"
            "• `каждый понедельник в 09:00`\n"
            "• `0 10 * * *` (прямой cron)",
        )
        return

    chat_id = message.chat.id if getattr(message, "chat", None) else 0
    job_name = f"tg-quick-{chat_id}-{int(time.time())}"
    description = f"quick per-chat job (chat_id={chat_id})"

    ok, raw_out = await _cron_run_openclaw(
        "cron",
        "add",
        "--json",
        "--name",
        job_name,
        "--every",
        cron_spec,
        "--session",
        "main",
        "--wake",
        "now",
        "--description",
        description,
        "--system-event",
        prompt,
        timeout=45.0,
    )
    if not ok:
        short = raw_out[:200] if raw_out else "no output"
        logger.warning(
            "cron_quick_create_failed_fallback_native", name=job_name, spec=cron_spec, raw=short
        )
        from ...core.cron_native_store import add_job as _native_add_job

        native_id = _native_add_job(cron_spec=cron_spec, prompt=prompt, job_id=job_name)
        prompt_preview = prompt[:80] + ("…" if len(prompt) > 80 else "")
        await message.reply(
            "✅ **Cron создан (native fallback)**\n"
            f"• ID: `{native_id}`\n"
            f"• Расписание: `{cron_spec}` (из `{time_expr}`)\n"
            f"• Промпт: `{prompt_preview}`\n"
            f"• Chat: `{chat_id}`\n"
            f"Выключить: `!cron native disable {native_id}`\n"
            "_OpenClaw CLI недоступен — job сохранён в native scheduler._",
        )
        return

    prompt_preview = prompt[:80] + ("…" if len(prompt) > 80 else "")
    await message.reply(
        "✅ **Cron создан**\n"
        f"• Имя: `{job_name}`\n"
        f"• Расписание: `{cron_spec}` (из `{time_expr}`)\n"
        f"• Промпт: `{prompt_preview}`\n"
        f"• Chat: `{chat_id}`\n"
        f"Выключить: `!cron disable {job_name}`",
    )


async def _handle_cron_native(message: Message, args: str) -> None:
    """!cron native <subcmd> [args] — управление native fallback cron jobs."""
    import shlex

    from ...core.cron_native_store import (
        add_job,
        list_jobs,
        remove_job,
        toggle_job,
    )

    parts = args.split(maxsplit=1) if args else []
    nsub = parts[0].lower() if parts else "list"
    narg = parts[1].strip() if len(parts) > 1 else ""

    if nsub in {"list", "ls", "список", ""}:
        jobs = list_jobs()
        if not jobs:
            await message.reply("🗓 Native cron jobs: пусто.")
            return
        lines = ["🗓 **Native Cron Jobs** _(fallback)_\n"]
        for j in jobs:
            flag = "✅" if j.get("enabled") else "⏸"
            jid = str(j.get("id") or "?")
            spec = str(j.get("cron_spec") or "?")
            runs = int(j.get("run_count") or 0)
            prompt_prev = str(j.get("prompt") or "")[:50]
            lines.append(f"{flag} `{jid}` — `{spec}` (runs: {runs})")
            lines.append(f"   {prompt_prev}")
        await message.reply("\n".join(lines))
        return

    if nsub == "add":
        if not narg:
            await message.reply(
                '❌ Формат: `!cron native add "0 10 * * *" "промпт"`',
            )
            return
        try:
            sp = shlex.split(narg)
        except ValueError:
            await message.reply("❌ Не распарсил аргументы. Используй кавычки.")
            return
        if len(sp) < 2:
            await message.reply('❌ Нужно: `"cron_spec" "промпт"`')
            return
        spec = sp[0]
        prompt = " ".join(sp[1:])
        new_id = add_job(cron_spec=spec, prompt=prompt)
        await message.reply(
            f"✅ Native job добавлен: `{new_id}`\n"
            f"• spec: `{spec}`\n"
            f"• промпт: `{prompt[:60]}`\n"
            f"Удалить: `!cron native remove {new_id}`"
        )
        return

    if nsub in {"remove", "rm", "del", "удалить"}:
        if not narg:
            await message.reply("❌ Укажи id job: `!cron native remove <id>`")
            return
        ok = remove_job(narg)
        if ok:
            await message.reply(f"✅ Native job `{narg}` удалён.")
        else:
            await message.reply(f"❌ Native job `{narg}` не найден.")
        return

    if nsub in {"enable", "вкл"}:
        if not narg:
            await message.reply("❌ Укажи id: `!cron native enable <id>`")
            return
        ok = toggle_job(narg, enabled=True)
        await message.reply(
            f"✅ Native job `{narg}` включён." if ok else f"❌ Job `{narg}` не найден."
        )
        return

    if nsub in {"disable", "выкл"}:
        if not narg:
            await message.reply("❌ Укажи id: `!cron native disable <id>`")
            return
        ok = toggle_job(narg, enabled=False)
        await message.reply(
            f"⏸ Native job `{narg}` выключен." if ok else f"❌ Job `{narg}` не найден."
        )
        return

    await message.reply(
        "🗓 **!cron native** — native fallback cron\n\n"
        "`!cron native list` — список jobs\n"
        '`!cron native add "0 10 * * *" "промпт"` — добавить\n'
        "`!cron native remove <id>` — удалить\n"
        "`!cron native enable/disable <id>` — вкл/выкл"
    )


# ---------------------------------------------------------------------------
# !schedule — отложенные сообщения
# ---------------------------------------------------------------------------


async def handle_schedule(bot: "KraabUserbot", message: Message) -> None:
    """Отложенные сообщения через MTProto schedule_date."""
    from ...core.message_scheduler import (
        _MIN_SCHEDULE_SECONDS,
        _now_local,
        format_scheduled_list,
        msg_scheduler_store,
        parse_schedule_spec,
        split_schedule_input,
    )

    raw_args = bot._get_command_args(message).strip()
    if not raw_args:
        raise UserInputError(
            user_message=(
                "📅 **Отложенные сообщения**\n\n"
                "Форматы:\n"
                "`!schedule HH:MM <текст>` — в указанное время\n"
                "`!schedule +Nm <текст>` — через N минут\n"
                "`!schedule +Nh <текст>` — через N часов\n"
                "`!schedule list` — список запланированных\n"
                "`!schedule cancel <id>` — отменить\n\n"
                "Пример: `!schedule +30m Позвонить Маше`"
            )
        )

    spec, rest = split_schedule_input(raw_args)
    chat_id = str(message.chat.id)

    if spec in {"list", "список"}:
        records = msg_scheduler_store.list_pending(chat_id=chat_id)
        await message.reply(format_scheduled_list(records))
        return

    if spec in {"cancel", "отмена"}:
        record_id = rest.strip()
        if not record_id:
            raise UserInputError(user_message="❌ Укажи ID: `!schedule cancel <id>`")

        rec = msg_scheduler_store.get(record_id)
        if rec is None or rec.chat_id != chat_id:
            await message.reply(f"⚠️ Запись `{record_id}` не найдена в этом чате.")
            return

        try:
            await bot.client.delete_messages(
                chat_id=int(chat_id),
                message_ids=rec.tg_message_id,
                revoke=True,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "schedule_cancel_delete_failed",
                record_id=record_id,
                tg_msg_id=rec.tg_message_id,
                error=str(exc),
            )

        msg_scheduler_store.mark_cancelled(record_id)
        await message.reply(f"🗑️ Запланированное сообщение `{record_id}` отменено.")
        return

    if not rest:
        raise UserInputError(
            user_message=("❌ Укажи текст сообщения.\nПример: `!schedule +30m Позвонить Маше`")
        )

    try:
        schedule_time = parse_schedule_spec(spec)
    except ValueError as exc:
        raise UserInputError(
            user_message=(
                f"❌ Не удалось распознать время: `{spec}`\n\n"
                "Поддерживаемые форматы:\n"
                "• `HH:MM` — конкретное время (напр. `14:30`)\n"
                "• `+Nm` — через N минут (напр. `+30m`)\n"
                "• `+Nh` — через N часов (напр. `+2h`)"
            )
        ) from exc

    delay_sec = (schedule_time - _now_local()).total_seconds()
    if delay_sec < _MIN_SCHEDULE_SECONDS:
        raise UserInputError(
            user_message=(
                f"⚠️ Минимальное время отсрочки — {_MIN_SCHEDULE_SECONDS} секунд.\n"
                "Telegram не принимает scheduled messages ближе к текущему моменту."
            )
        )

    try:
        sent = await bot.client.send_message(
            chat_id=int(chat_id),
            text=rest,
            schedule_date=schedule_time,
        )
    except Exception as exc:
        logger.error("schedule_send_failed", chat_id=chat_id, error=str(exc))
        raise UserInputError(
            user_message=f"❌ Ошибка при создании отложенного сообщения: {exc}"
        ) from exc

    record_id = msg_scheduler_store.add(
        chat_id=chat_id,
        text=rest,
        schedule_time=schedule_time,
        tg_message_id=sent.id,
    )

    when_str = schedule_time.strftime("%d.%m.%Y в %H:%M")
    await message.reply(
        f"📅 Сообщение запланировано на **{when_str}**\n"
        f"ID: `{record_id}` · Telegram msg: `{sent.id}`\n"
        f"Отмена: `!schedule cancel {record_id}`"
    )


# ---------------------------------------------------------------------------
# !autodel — автоудаление ответов Краба
# ---------------------------------------------------------------------------


async def handle_autodel(bot: "KraabUserbot", message: Message) -> None:
    """!autodel <секунды> — автоудаление ответов Краба через N секунд."""
    access_profile = bot._get_access_profile(message.from_user)
    if access_profile.level != AccessLevel.OWNER:
        raise UserInputError(user_message="🚫 Только owner может управлять автоудалением.")

    chat_id = message.chat.id
    raw = bot._get_command_args(message).strip()

    if not raw or raw.lower() == "status":
        delay = get_autodel_delay(bot, chat_id)
        if delay:
            await message.reply(
                f"⏱ Автоудаление: **включено** — через `{int(delay)}` сек.\n"
                f"`!autodel 0` — выключить."
            )
        else:
            await message.reply("⏱ Автоудаление: **выключено**.\n`!autodel <секунды>` — включить.")
        return

    try:
        delay = float(raw)
    except ValueError:
        raise UserInputError(
            user_message="❌ Использование: `!autodel <секунды>` или `!autodel status`."
        )

    if delay < 0:
        raise UserInputError(user_message="❌ Секунды не могут быть отрицательными.")

    _set_autodel_delay(bot, chat_id, delay)

    if delay == 0:
        await message.reply("⏱ Автоудаление **выключено**.")
    else:
        await message.reply(
            f"⏱ Автоудаление **включено**: ответы Краба будут удалены через `{int(delay)}` сек."
        )


# ---------------------------------------------------------------------------
# !timer — таймер
# ---------------------------------------------------------------------------


async def handle_timer(bot: "KraabUserbot", message: Message) -> None:
    """Управление таймерами: !timer <время>, !timer list, !timer cancel [id]."""
    global _timer_counter  # noqa: PLW0603

    args = bot._get_command_args(message).strip()

    if args in ("list", "список", "ls"):
        if not _active_timers:
            await message.reply("⏱ Нет активных таймеров.")
            return
        lines = ["⏱ **Активные таймеры:**"]
        now = time.monotonic()
        for tid, info in sorted(_active_timers.items()):
            remaining = max(0.0, info["ends_at"] - now)
            label = info.get("label") or ""
            label_part = f" — {label}" if label else ""
            lines.append(f"• `#{tid}` {_fmt_duration(remaining)} осталось{label_part}")
        await message.reply("\n".join(lines))
        return

    if args.startswith(("cancel", "отмена", "stop")):
        parts = args.split(maxsplit=1)
        if len(parts) < 2:
            if not _active_timers:
                await message.reply("⏱ Нет активных таймеров.")
                return
            for info in _active_timers.values():
                info["task"].cancel()
            count = len(_active_timers)
            _active_timers.clear()
            await message.reply(f"✅ Отменено таймеров: {count}.")
            return
        id_str = parts[1].lstrip("#")
        if not id_str.isdigit():
            await message.reply("❌ Укажи ID таймера: `!timer cancel 3`")
            return
        tid = int(id_str)
        if tid not in _active_timers:
            await message.reply(f"❌ Таймер `#{tid}` не найден.")
            return
        _active_timers[tid]["task"].cancel()
        del _active_timers[tid]
        await message.reply(f"✅ Таймер `#{tid}` отменён.")
        return

    parts = args.split(maxsplit=1)
    if not parts or not args:
        await message.reply(
            "⏱ Использование:\n"
            "`!timer 5m` — таймер на 5 минут\n"
            "`!timer 1h30m Обед` — таймер с меткой\n"
            "`!timer list` — список\n"
            "`!timer cancel [id]` — отмена"
        )
        return

    seconds = _parse_duration(parts[0])
    if seconds is None:
        await message.reply(
            f"❌ Не могу распарсить время: `{parts[0]}`\nПримеры: `5m`, `1h30m`, `90s`, `3600`"
        )
        return

    label = parts[1] if len(parts) > 1 else ""
    chat_id = message.chat.id

    _timer_counter += 1
    tid = _timer_counter

    async def _timer_callback(t_id: int, secs: int, c_id: int, lbl: str) -> None:
        try:
            await asyncio.sleep(secs)
        except asyncio.CancelledError:
            return
        label_part = f" — {lbl}" if lbl else ""
        if c_id < 0:
            target = "me"
            text = f"⏰ **Таймер истёк!**{label_part} (#{t_id})\n_(из группы `{c_id}`)_"
        else:
            target = c_id
            text = f"⏰ **Таймер истёк!**{label_part} (#{t_id})"
        try:
            await bot.client.send_message(target, text)
        except Exception as exc:  # noqa: BLE001
            logger.warning("timer_notify_failed", timer_id=t_id, error=str(exc))
        finally:
            _active_timers.pop(t_id, None)

    task = asyncio.create_task(_timer_callback(tid, seconds, chat_id, label))
    _active_timers[tid] = {
        "task": task,
        "label": label,
        "ends_at": time.monotonic() + seconds,
        "chat_id": chat_id,
    }

    label_part = f" — {label}" if label else ""
    await message.reply(f"⏱ Таймер `#{tid}` запущен на **{_fmt_duration(seconds)}**{label_part}.")


# ---------------------------------------------------------------------------
# !stopwatch — секундомер
# ---------------------------------------------------------------------------


async def handle_stopwatch(bot: "KraabUserbot", message: Message) -> None:
    """Управление секундомером: !stopwatch start|stop|lap."""
    args = bot._get_command_args(message).strip().lower()
    chat_id = message.chat.id

    if args in ("start", "старт", "go"):
        if chat_id in _stopwatches:
            elapsed = time.monotonic() - _stopwatches[chat_id]["started_at"]
            await message.reply(
                f"⚡ Секундомер уже запущен ({_fmt_duration(elapsed)} прошло). "
                "Используй `!stopwatch stop` для остановки."
            )
            return
        _stopwatches[chat_id] = {"started_at": time.monotonic(), "laps": []}
        await message.reply("▶️ Секундомер запущен.")
        return

    if args in ("stop", "стоп", "end"):
        if chat_id not in _stopwatches:
            await message.reply("⏱ Секундомер не запущен. Используй `!stopwatch start`.")
            return
        sw = _stopwatches.pop(chat_id)
        elapsed = time.monotonic() - sw["started_at"]
        laps = sw["laps"]
        lines = [f"⏹ Секундомер остановлен. **Итого: {_fmt_duration(elapsed)}**"]
        if laps:
            lines.append("")
            for i, lap_ts in enumerate(laps, 1):
                lap_elapsed = lap_ts - sw["started_at"]
                lines.append(f"  Круг {i}: {_fmt_duration(lap_elapsed)}")
        await message.reply("\n".join(lines))
        return

    if args in ("lap", "круг", "split"):
        if chat_id not in _stopwatches:
            await message.reply("⏱ Секундомер не запущен. Используй `!stopwatch start`.")
            return
        sw = _stopwatches[chat_id]
        now = time.monotonic()
        sw["laps"].append(now)
        elapsed = now - sw["started_at"]
        lap_num = len(sw["laps"])
        if lap_num > 1:
            prev = sw["laps"][-2]
            split = now - prev
            await message.reply(
                f"🔵 Круг {lap_num}: **{_fmt_duration(elapsed)}** "
                f"(+{_fmt_duration(split)} с прошлого)"
            )
        else:
            await message.reply(f"🔵 Круг {lap_num}: **{_fmt_duration(elapsed)}**")
        return

    if args in ("", "status", "статус", "time"):
        if chat_id not in _stopwatches:
            await message.reply("⏱ Секундомер не запущен. Используй `!stopwatch start`.")
            return
        sw = _stopwatches[chat_id]
        elapsed = time.monotonic() - sw["started_at"]
        laps = sw["laps"]
        lines = [f"⏱ Текущее время: **{_fmt_duration(elapsed)}**"]
        if laps:
            lines.append(f"Кругов: {len(laps)}")
        await message.reply("\n".join(lines))
        return

    await message.reply(
        "⏱ Использование секундомера:\n"
        "`!stopwatch start` — запустить\n"
        "`!stopwatch stop` — остановить\n"
        "`!stopwatch lap` — отметить круг\n"
        "`!stopwatch` — текущее время"
    )


# ---------------------------------------------------------------------------
# !todo — персональный TODO
# ---------------------------------------------------------------------------


async def handle_todo(bot: "KraabUserbot", message: Message) -> None:
    """Персональный менеджер задач в Telegram."""
    from ...core.personal_todo import personal_todo_service

    args = bot._get_command_args(message).strip()

    if not args or args.lower() in ("list",):
        await message.reply(personal_todo_service.render())
        return

    parts = args.split(maxsplit=1)
    sub = parts[0].lower()
    rest = parts[1].strip() if len(parts) > 1 else ""

    if sub == "add":
        if not rest:
            raise UserInputError(user_message="📋 Формат: `!todo add <текст задачи>`")
        item = personal_todo_service.add(rest)
        await message.reply(f"✅ Задача добавлена: **{item['id']}. {item['text']}**")
        return

    if sub == "done":
        if not rest.isdigit():
            raise UserInputError(user_message="📋 Формат: `!todo done <id>`")
        todo_id = int(rest)
        item = personal_todo_service.mark_done(todo_id)
        if item is None:
            raise UserInputError(user_message=f"📋 Задача #{todo_id} не найдена.")
        await message.reply(f"✅ Отмечено выполненным: ~{item['text']}~")
        return

    if sub in ("del", "delete", "rm"):
        if not rest.isdigit():
            raise UserInputError(user_message="📋 Формат: `!todo del <id>`")
        todo_id = int(rest)
        deleted = personal_todo_service.delete(todo_id)
        if not deleted:
            raise UserInputError(user_message=f"📋 Задача #{todo_id} не найдена.")
        await message.reply(f"🗑 Задача #{todo_id} удалена.")
        return

    if sub == "clear" and rest.lower() == "done":
        count = personal_todo_service.clear_done()
        if count == 0:
            await message.reply("📋 Нет выполненных задач для очистки.")
        else:
            await message.reply(f"🗑 Очищено {count} выполненных задач.")
        return

    raise UserInputError(
        user_message=(
            "📋 **Todo — менеджер задач**\n\n"
            "`!todo` / `!todo list` — показать все задачи\n"
            "`!todo add <текст>` — добавить задачу\n"
            "`!todo done <id>` — отметить выполненной\n"
            "`!todo del <id>` — удалить задачу\n"
            "`!todo clear done` — очистить выполненные"
        )
    )
