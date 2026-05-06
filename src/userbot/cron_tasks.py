# -*- coding: utf-8 -*-
"""Wave 31-E: CronTaskMixin — выделяет cron/scheduler логику из userbot_bridge.

Зачем:
- bridge до Wave 31-E содержал ~6271 LOC, cron-блок (1638-1865) — 227 LOC cohesive
  логики, не зависящей напрямую от инициализации Telegram client'a.
- mixin использует только: ``self.client``, ``self.me``, ``self._split_message``.

Контракт:
- ``_build_cron_system_prompt`` — staticmethod, ≤500 chars, без tool-mention.
- ``_build_cron_context`` — собирает RUNTIME CONTEXT snapshot (cost/inbox/archive/
  reminders), чтобы cron-LLM отвечал one-shot без tool-chain (timeout 90s).
- ``_run_cron_prompt_and_send`` — full pipeline: context + LLM + Telegram send.
- ``_send_scheduled_message`` — простая отправка scheduled-сообщения.
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from pyrogram import Client

logger = structlog.get_logger("Krab.userbot.cron_tasks")


class CronTaskMixin:
    """Mixin для cron/scheduler send-pipeline."""

    # Атрибуты, которые ожидаются на host-классе (KraabUserbot):
    client: "Client | None"
    me: object | None  # pyrogram.types.User | None

    # ─── helpers ─────────────────────────────────────────────────────────────

    @staticmethod
    def _build_cron_system_prompt() -> str:
        """
        Минимальный system_prompt для cron-path.

        Зачем отдельный: полный owner-prompt (`_build_system_prompt_for_sender`) грузит
        workspace bundle + injection defense + role-инструкции и **регистрирует tools**.
        У CLI-провайдеров (codex-cli/gpt-5.4) это приводило к tool-chain попыткам и
        gateway возвращал плейсхолдер `No response from OpenClaw.` (26 chars) когда
        text payload оставался пустым после tool calls.

        Контракт: ≤500 chars, никаких упоминаний tools, NO_REPLY как escape hatch.
        """
        return (
            "Ты — Krab cron-помощник. На вход — RUNTIME CONTEXT снапшот и задача. "
            "Отвечай в один shot, кратко (≤6 строк), на русском. "
            "Используй только данные из RUNTIME CONTEXT — никаких tool calls, "
            "history_search, cost_lookup, inbox_status и т.п. "
            "Если по контексту нечего сообщить владельцу — ответь ровно `NO_REPLY` "
            "(одним токеном, без пояснений). Не извиняйся, не приветствуй."
        )

    async def _build_cron_context(self) -> str:
        """
        Собирает компактный snapshot runtime-данных для prefix-инъекции в cron prompts.

        Цель: позволить LLM ответить "в один shot" без tool-chain (history_search,
        cost_lookup, inbox_status, archive_size). Tool-chain раньше упирался в 90s
        timeout. Все источники читаются дёшево (in-memory / file stat / sqlite count).

        Формат: ≤500 токенов, многострочный markdown-блок. При ошибке любого
        источника — заменяется на "n/a" чтобы не валить весь cron.
        """
        from datetime import datetime, timezone  # noqa: PLC0415

        from ..core.inbox_service import inbox_service  # noqa: PLC0415
        from ..core.scheduler import krab_scheduler  # noqa: PLC0415

        lines: list[str] = ["=== KRAB RUNTIME CONTEXT (auto, для one-shot ответа) ==="]
        now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        lines.append(f"Время: {now_utc}")

        # Costs (сессия + месяц + бюджет)
        try:
            from ..core.cost_analytics import cost_analytics  # noqa: PLC0415

            session_cost = cost_analytics.get_cost_so_far_usd()
            month_cost = cost_analytics.get_monthly_cost_usd()
            budget = cost_analytics.get_monthly_budget_usd()
            remaining = cost_analytics.get_remaining_budget_usd()
            cost_line = f"Расходы: сессия ${session_cost:.4f}, месяц ${month_cost:.4f}"
            if budget > 0:
                cost_line += f", бюджет ${budget:.2f}"
                if remaining is not None:
                    cost_line += f", осталось ${remaining:.2f}"
            else:
                cost_line += ", бюджет не задан"
            lines.append(cost_line)
        except Exception as exc:  # noqa: BLE001
            lines.append(f"Расходы: n/a ({type(exc).__name__})")

        # Inbox open/processing counts
        try:
            open_items = inbox_service.list_items(status="open", limit=200)
            proc_items = inbox_service.list_items(status="processing", limit=200)
            lines.append(f"Inbox: open={len(open_items)}, processing={len(proc_items)}")
            # Топ-3 самых свежих open для контекста morning brief
            preview = []
            for it in open_items[:3]:
                title = (getattr(it, "summary", None) or getattr(it, "title", "") or "")[:80]
                kind = getattr(it, "kind", "") or "?"
                if title:
                    preview.append(f"  • [{kind}] {title}")
            if preview:
                lines.append("Inbox top-3 open:")
                lines.extend(preview)
        except Exception as exc:  # noqa: BLE001
            lines.append(f"Inbox: n/a ({type(exc).__name__})")

        # Archive.db size + delta за сутки
        try:
            from ..core.archive_growth_monitor import (  # noqa: PLC0415
                load_history,
                take_snapshot,
            )

            snap = take_snapshot()
            if snap:
                archive_line = f"Archive.db: {snap.size_mb:.1f} MB, {snap.message_count} messages"
                hist = load_history()
                day_ago = int(time.time()) - 86400
                older = [s for s in hist if s.ts < day_ago]
                if older:
                    delta_mb = snap.size_mb - older[-1].size_mb
                    delta_msgs = snap.message_count - older[-1].message_count
                    archive_line += f"; за сутки Δ {delta_mb:+.1f} MB / {delta_msgs:+d} msgs"
                lines.append(archive_line)
            else:
                lines.append("Archive.db: n/a (нет файла)")
        except Exception as exc:  # noqa: BLE001
            lines.append(f"Archive.db: n/a ({type(exc).__name__})")

        # Active reminders
        try:
            reminders = krab_scheduler.list_reminders()
            lines.append(f"Reminders pending: {len(reminders)}")
        except Exception as exc:  # noqa: BLE001
            lines.append(f"Reminders: n/a ({type(exc).__name__})")

        lines.append("=== END CONTEXT ===")
        return "\n".join(lines)

    async def _run_cron_prompt_and_send(self, chat_id: str, prompt: str) -> None:
        """
        W32: Cron job callback — пропускает prompt через LLM и отправляет
        результат в Saved Messages владельца.

        Раньше `_send_scheduled_message` был bound как cron sender, но он
        просто слал raw prompt как текст. Для cron jobs нужен LLM-processing:
        morning-brief/evening-recap формируют сводки, archive-growth/cost-budget
        генерируют conditional alerts. LLM-путь obligatory.

        2026-04-25: prefix-инъекция готового контекста (`_build_cron_context`)
        чтобы LLM отвечал в один shot без tool-chain. Раньше LLM пытался
        history_search/cost_lookup/inbox_status и упирался в 90s timeout.
        """
        try:
            if not self.client or not self.client.is_connected:
                logger.warning("cron_job_skip_no_telegram", prompt_preview=prompt[:80])
                return

            # Determine recipient: chat_id может быть "cron_native" — route to owner Saved Messages
            owner_id = self.me.id if self.me else None
            target_chat: int | str | None = None
            if chat_id and chat_id != "cron_native" and re.fullmatch(r"-?\d+", str(chat_id or "")):
                target_chat = int(str(chat_id))
            elif owner_id:
                target_chat = int(owner_id)

            if target_chat is None:
                logger.warning("cron_job_skip_no_target", prompt_preview=prompt[:80])
                return

            # LLM call через существующий router — reuse swarm-style adapter для one-shot
            from ..handlers.command_handlers import _AgentRoomRouterAdapter  # noqa: PLC0415

            # Минимальный cron-prompt (см. _build_cron_system_prompt). Полный owner-prompt
            # с workspace bundle + tool registration ломал CLI-провайдеры → пустой ответ →
            # gateway возвращал "No response from OpenClaw." (26 chars).
            system_prompt = self._build_cron_system_prompt()
            # W32 hotfix v3: chat_id MUST be numeric (target_chat = owner_id),
            # не synthetic string "cron:job:..." — иначе openclaw_client hangs
            # in memory_adapter trying to load history for non-existent chat.
            # Manual run_now showed firing → silent hang (никаких событий после
            # cron_native_job_firing).
            adapter = _AgentRoomRouterAdapter(
                chat_id=str(target_chat),
                system_prompt=system_prompt,
                team_name=None,
            )
            # W32 hotfix v2: _AgentRoomRouterAdapter exposes route_query (returns
            # full string), not .stream(). Add 90s timeout — cron prompts должны
            # отвечать быстро, иначе откатываемся на skip без блокировки scheduler.
            #
            # 2026-04-25: префиксим prompt готовым context-блоком (cost/inbox/archive/
            # reminders) — LLM отвечает в один shot без tool-chain (раньше hit 90s).
            try:
                context_block = await self._build_cron_context()
            except Exception as ctx_exc:  # noqa: BLE001
                logger.warning("cron_context_build_failed", error=str(ctx_exc))
                context_block = ""
            augmented_prompt = f"{context_block}\n\n{prompt}" if context_block else prompt
            full_reply = (
                await asyncio.wait_for(adapter.route_query(augmented_prompt), timeout=90.0)
            ).strip()
            if not full_reply:
                logger.warning("cron_job_empty_llm_reply", prompt_preview=prompt[:80])
                return

            # Gateway placeholder = empty response → silent skip (не спамим Saved Messages)
            if "no response from openclaw" in full_reply.lower():
                logger.warning(
                    "cron_job_gateway_placeholder",
                    prompt_preview=prompt[:80],
                    reply_preview=full_reply[:120],
                )
                return

            # NO_REPLY marker — cron jobs с conditional logic могут решить тихо
            if "NO_REPLY" in full_reply[:50].upper():
                logger.info("cron_job_silent_skip", prompt_preview=prompt[:80])
                return

            for part in self._split_message(full_reply):
                await self.client.send_message(target_chat, part)
            logger.info(
                "cron_job_message_sent",
                target=str(target_chat),
                reply_len=len(full_reply),
                reply_preview=full_reply[:160],
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "cron_job_llm_failed",
                error=str(exc),
                error_type=type(exc).__name__,
                prompt_preview=prompt[:80],
            )

    async def _send_scheduled_message(self, chat_id: str, text: str) -> None:
        """
        Отправляет сообщение из scheduler в Telegram-чат.

        Почему отдельный метод:
        - scheduler должен быть изолирован от деталей Telegram API;
        - здесь централизуем валидацию и безопасную нарезку длинных сообщений.
        """
        if not self.client or not self.client.is_connected:
            raise RuntimeError("telegram_client_not_ready")

        payload = str(text or "").strip()
        if not payload:
            raise ValueError("scheduled_message_empty")

        target_chat: int | str = str(chat_id or "").strip()
        if re.fullmatch(r"-?\d+", str(target_chat)):
            target_chat = int(str(target_chat))

        for part in self._split_message(payload):
            await self.client.send_message(target_chat, part)
