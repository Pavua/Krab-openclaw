# -*- coding: utf-8 -*-
"""
Swarm Self-Reflection — Proactivity Level 3.

После каждой big task (swarm research, multi-step operation) запускает
reflection-агрегатор, который анализирует результат и предлагает follow-up
задачи.

Output: список {task_description, priority, suggested_team} — добавляются
в swarm_task_board или reminders_queue (scheduler.add_reminder).

Использование::

    from .swarm_self_reflection import reflect_on_task, enqueue_followups

    reflection = await reflect_on_task(
        task_id=task.id,
        task_title=task.topic,
        task_description=task.description,
        task_result=task.final_report,
        llm_caller=my_async_llm_caller,
    )
    enqueue_followups(reflection, task_board=swarm_task_board)
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional

from .logger import get_logger

logger = get_logger(__name__)


REFLECTION_PROMPT_TEMPLATE = """Ты reviewer только что завершённой агентной задачи в Krab.

Задача: {task_title}
Описание: {task_description}
Результат (первые 2000 chars): {task_result_preview}
Статус: {task_status}

Твой отчёт должен содержать:
1. **Ключевые инсайты** из результата — что узнали нового, какие гипотезы подтвердились/опровергнуты.
2. **Unresolved вопросы** — что осталось неясно, требует дальнейшей проверки.
3. **Follow-up задачи** — конкретные actionable items на основе результата. Для каждой укажи:
   - title (короткое)
   - description (что сделать)
   - priority (low|medium|high|critical)
   - suggested_team (traders|coders|analysts|creative|self)
   - trigger (time_based "через N часов" или event_based "когда X")

Верни строгий JSON:
{{
  "insights": ["..."],
  "unresolved": ["..."],
  "followups": [
    {{"title": "", "description": "", "priority": "medium", "suggested_team": "self", "trigger": "manual"}}
  ]
}}
"""


# Тип LLM-caller: принимает prompt, возвращает текст ответа.
# Делаем инъекцию через callable, чтобы не тащить зависимость от openclaw_client
# и упростить тестирование.
LLMCaller = Callable[[str], Awaitable[str]]


@dataclass
class ReflectionResult:
    """Результат саморефлексии после завершённой задачи."""

    task_id: str
    completed_at: int
    insights: list[str] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)
    followups: list[dict] = field(default_factory=list)
    raw_response: str = ""


async def _default_openclaw_caller(openclaw_client, prompt: str) -> str:
    """Обёртка над openclaw_client.send_message_stream() — собирает полный ответ."""
    chunks: list[str] = []
    try:
        async for piece in openclaw_client.send_message_stream(
            message=prompt,
            chat_id="__reflection__",
            force_cloud=True,
            disable_tools=True,
        ):
            if isinstance(piece, str):
                chunks.append(piece)
    except Exception as exc:  # noqa: BLE001
        logger.warning("self_reflection_stream_failed", error=str(exc))
    return "".join(chunks)


async def reflect_on_task(
    task_id: str,
    task_title: str,
    task_description: str,
    task_result: str,
    task_status: str = "completed",
    *,
    llm_caller: Optional[LLMCaller] = None,
    openclaw_client=None,
) -> ReflectionResult:
    """
    Запускает LLM reflection на завершённую задачу.

    Args:
        task_id: ID задачи.
        task_title: Короткий заголовок.
        task_description: Что делали.
        task_result: Результат (truncated к 2000 chars в промпте).
        task_status: completed | failed | partial.
        llm_caller: Async callable (prompt) -> response. Если задан — используется.
        openclaw_client: Альтернатива: объект с send_message_stream().
            Если llm_caller не задан, используется wrapper над клиентом.

    Returns:
        ReflectionResult. Если нет клиента — пустой результат.
    """
    result = ReflectionResult(
        task_id=task_id,
        completed_at=int(datetime.now(timezone.utc).timestamp()),
    )

    if llm_caller is None and openclaw_client is None:
        logger.warning("self_reflection_no_client", task_id=task_id)
        return result

    prompt = REFLECTION_PROMPT_TEMPLATE.format(
        task_title=task_title,
        task_description=task_description,
        task_result_preview=(task_result or "")[:2000],
        task_status=task_status,
    )

    try:
        if llm_caller is not None:
            raw = await llm_caller(prompt)
        else:
            raw = await _default_openclaw_caller(openclaw_client, prompt)

        raw_str = raw if isinstance(raw, str) else str(raw)
        result.raw_response = raw_str[:5000]

        # Парсим JSON — ищем первый {...} блок, даже если обёрнут в ```json```
        json_match = re.search(r"\{.*\}", raw_str, re.DOTALL)
        if json_match:
            try:
                parsed = json.loads(json_match.group())
                result.insights = list(parsed.get("insights", []))
                result.unresolved = list(parsed.get("unresolved", []))
                result.followups = list(parsed.get("followups", []))
            except json.JSONDecodeError as exc:
                logger.warning(
                    "self_reflection_json_parse_failed",
                    task_id=task_id,
                    error=str(exc),
                )

        logger.info(
            "self_reflection_completed",
            task_id=task_id,
            insights_count=len(result.insights),
            unresolved_count=len(result.unresolved),
            followups_count=len(result.followups),
        )
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "self_reflection_failed",
            task_id=task_id,
            error=str(exc),
            error_type=type(exc).__name__,
        )

    return result


def _parse_hours_from_trigger(trigger: str) -> int:
    """Парсит '(через) N час(ов)' или '(in) N hour(s)' → int N, default 2."""
    if not trigger:
        return 2
    match = re.search(r"(\d+)\s*(?:час|hour)", trigger, re.IGNORECASE)
    if match:
        try:
            return max(1, int(match.group(1)))
        except ValueError:
            return 2
    return 2


def _is_time_based(trigger: str) -> bool:
    """Проверяет, является ли триггер time-based (через N часов / in N hours)."""
    if not trigger:
        return False
    t = trigger.lower()
    return "час" in t or "hour" in t or t.startswith("через") or t.startswith("in ")


def enqueue_followups(
    reflection: ReflectionResult,
    task_board=None,
    reminders_queue=None,
) -> dict[str, int]:
    """
    Добавляет follow-up tasks в swarm_task_board и/или reminders_queue.

    Args:
        reflection: Результат reflect_on_task().
        task_board: Объект с .create_task(team, title, description, priority, ...).
        reminders_queue: Объект с .add_time_reminder(owner_id, fire_at, action, action_type).

    Returns:
        dict со счётчиками: {"board": N, "reminders": N, "skipped": N}.
    """
    stats = {"board": 0, "reminders": 0, "skipped": 0}
    if not reflection.followups:
        return stats

    for fup in reflection.followups:
        if not isinstance(fup, dict):
            stats["skipped"] += 1
            continue

        title = str(fup.get("title", "")).strip()
        description = str(fup.get("description", "")).strip()
        if not title and not description:
            stats["skipped"] += 1
            continue

        team = str(fup.get("suggested_team", "self")).strip() or "self"
        priority = str(fup.get("priority", "medium")).strip() or "medium"
        trigger = str(fup.get("trigger", "manual")).strip()

        if _is_time_based(trigger) and reminders_queue is not None:
            hours = _parse_hours_from_trigger(trigger)
            fire_at = int(time.time()) + hours * 3600
            try:
                reminders_queue.add_time_reminder(
                    owner_id="self",
                    fire_at=fire_at,
                    action=f"[{team}] {title}: {description}",
                    action_type="notify",
                )
                stats["reminders"] += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "self_reflection_reminder_enqueue_failed",
                    error=str(exc),
                    title=title,
                )
                stats["skipped"] += 1
        elif task_board is not None:
            try:
                task_board.create_task(
                    team=team,
                    title=title or description[:80],
                    description=description or title,
                    priority=priority,
                    created_by="self_reflection",
                )
                stats["board"] += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "self_reflection_task_enqueue_failed",
                    error=str(exc),
                    title=title,
                )
                stats["skipped"] += 1
        else:
            stats["skipped"] += 1

    logger.info(
        "self_reflection_followups_enqueued",
        task_id=reflection.task_id,
        **stats,
    )
    return stats


# ---------------------------------------------------------------------------
# Structured reflection (schema-validated, Haiku-style light model)
# ---------------------------------------------------------------------------

STRUCTURED_REFLECTION_PROMPT = """Ты reviewer завершённой агентной задачи.

Задача: {task_title}
Описание: {task_description}
Результат (первые 2000 chars): {task_result_preview}

Верни ТОЛЬКО валидный JSON без дополнительного текста:
{{
  "insights": ["список ключевых инсайтов (строки)"],
  "follow_ups": [
    {{
      "title": "короткое название",
      "description": "что сделать",
      "priority": "low|medium|high|critical",
      "suggested_team": "traders|coders|analysts|creative|self",
      "trigger": "manual|время через N часов"
    }}
  ]
}}
"""

# Очередь follow-up задач, которые будут persist-ированы как напоминания
_reminders_queue: list[dict] = []


@dataclass
class ReflectionOutput:
    """Результат structured reflection (schema-validated)."""

    insights: list[str] = field(default_factory=list)
    follow_ups: list[dict] = field(default_factory=list)
    raw_response: str = ""


async def structured_reflect(
    task_id: str,
    task_title: str,
    task_description: str,
    task_result: str,
    llm_caller: LLMCaller,
) -> ReflectionOutput:
    """
    Запускает schema-validated structured reflection (Haiku-style).

    Args:
        task_id: ID задачи для логирования.
        task_title: Заголовок задачи.
        task_description: Описание задачи.
        task_result: Результат (truncated к 2000 chars в промпте).
        llm_caller: Async callable (prompt: str) -> str — лёгкая модель.

    Returns:
        ReflectionOutput с insights и follow_ups.
    """
    output = ReflectionOutput()

    prompt = STRUCTURED_REFLECTION_PROMPT.format(
        task_title=task_title,
        task_description=task_description,
        task_result_preview=(task_result or "")[:2000],
    )

    try:
        raw = await llm_caller(prompt)
        raw_str = raw if isinstance(raw, str) else str(raw)
        output.raw_response = raw_str[:5000]

        # Извлекаем первый JSON-блок из ответа
        json_match = re.search(r"\{.*\}", raw_str, re.DOTALL)
        if json_match:
            parsed = json.loads(json_match.group())
            insights = parsed.get("insights", [])
            follow_ups = parsed.get("follow_ups", [])
            # Валидация типов
            output.insights = [str(i) for i in insights if i]
            output.follow_ups = [f for f in follow_ups if isinstance(f, dict)]

        logger.info(
            "structured_reflect_parsed",
            task_id=task_id,
            insights=len(output.insights),
            follow_ups=len(output.follow_ups),
        )
    except json.JSONDecodeError as exc:
        logger.warning("structured_reflect_json_failed", task_id=task_id, error=str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.warning("structured_reflect_failed", task_id=task_id, error=str(exc))

    return output


def flush_followups_to_reminders(
    reflection: ReflectionOutput,
    owner_id: str = "self",
) -> int:
    """
    Persist follow_ups из ReflectionOutput → глобальный _reminders_queue.

    Каждый follow_up добавляется как запись с fire_at (time-based)
    или как manual-элемент. Возвращает кол-во добавленных записей.

    Args:
        reflection: Результат structured_reflect().
        owner_id: Идентификатор владельца для очереди.

    Returns:
        Количество добавленных в очередь записей.
    """
    flushed = 0
    for fup in reflection.follow_ups:
        if not isinstance(fup, dict):
            continue
        title = str(fup.get("title", "")).strip()
        description = str(fup.get("description", "")).strip()
        if not title and not description:
            continue

        trigger = str(fup.get("trigger", "manual")).strip()
        team = str(fup.get("suggested_team", "self")).strip() or "self"
        priority = str(fup.get("priority", "medium")).strip() or "medium"

        fire_at: int
        if _is_time_based(trigger):
            hours = _parse_hours_from_trigger(trigger)
            fire_at = int(time.time()) + hours * 3600
        else:
            # manual → fire ASAP (сейчас +60 сек для дедубликации)
            fire_at = int(time.time()) + 60

        entry = {
            "owner_id": owner_id,
            "fire_at": fire_at,
            "action": f"[{team}] {title}: {description}".strip(": "),
            "action_type": "notify",
            "priority": priority,
            "trigger": trigger,
        }
        _reminders_queue.append(entry)
        flushed += 1

    if flushed:
        logger.info("flush_followups_to_reminders", count=flushed, owner_id=owner_id)

    return flushed


def get_pending_reminders() -> list[dict]:
    """Возвращает копию текущей очереди напоминаний (для тестов и инспекции)."""
    return list(_reminders_queue)


def clear_reminders_queue() -> None:
    """Сбрасывает очередь (для тестов)."""
    _reminders_queue.clear()
