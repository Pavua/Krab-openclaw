# -*- coding: utf-8 -*-
"""Wave 31-I: BackgroundLoopsMixin — фоновые периодические тики userbot'а.

Зачем:
- bridge до 31-I содержал ~5190 LOC, idea-features tick + command_usage_save —
  cohesive 178 LOC периодических задач, изолированных от main message flow.
- Mixin использует: ``self.client.send_message``, ``self.me``,
  ``self._owner_notify_target``, ``self._idea_tick_state``.

Контракт:
- ``_idea_features_tick_loop`` — единый 30-секундный тик с 5 фичами:
  reply_scheduler / daily_brief (08:00) / channel_digest (09:00) /
  pattern_detector (6h) / skill_curator A/B (04:00 UTC).
  Каждая фича обёрнута в try/except → fail-open, не валит петлю.
- ``_command_usage_save_loop`` — 5-минутный flush command counters на диск.
- ``_evaluate_and_apply_skill_curator_proposals`` (module-level) — Wave 38-B
  cron callback, вызывается из tick'а.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from pyrogram import Client

logger = structlog.get_logger("Krab.userbot.background_loops")


# ─── Module-level helpers ────────────────────────────────────────────────────


async def _evaluate_and_apply_skill_curator_proposals() -> None:
    """Wave 38-B: cron-trigger для evaluation + apply A/B test results.

    Запускается раз в сутки в 04:00 UTC через _idea_features_tick_loop.
    Гейт: KRAB_SKILL_CURATOR_CRON_ENABLED=1.
    Для каждой команды свёрма ищет активный A/B тест и запускает evaluate_ab_test_and_apply.
    При отсутствии activate теста — тихий пропуск.
    """
    _swarm_teams = ("traders", "coders", "analysts", "creative")

    try:
        from ..core.skill_curator import skill_curator  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        _sc_logger = logging.getLogger(__name__)
        _sc_logger.warning("skill_curator_cron_import_failed: %s", exc)
        return

    _log = logging.getLogger(__name__)

    for team in _swarm_teams:
        try:
            # Проверяем наличие активного теста (без side-effects)
            active_test = skill_curator.get_active_ab_test(team)
            if not active_test:
                _log.debug("skill_curator_cron_no_active_test", team=team)
                continue

            ab_id = active_test.get("ab_id", "")
            if not ab_id:
                continue

            result, applied = await skill_curator.evaluate_ab_test_and_apply(ab_id)
            _log.info(
                "skill_curator_eval_done",
                team=team,
                ab_id=ab_id,
                applied=applied,
                status=result.get("status"),
                winner=result.get("winner"),
            )
        except Exception as exc:  # noqa: BLE001
            _log.warning(
                "skill_curator_eval_failed",
                team=team,
                error=str(exc)[:200],
            )


# ─── Mixin ───────────────────────────────────────────────────────────────────


class BackgroundLoopsMixin:
    """Mixin: периодические фоновые тики userbot'а."""

    # Атрибуты, которые ожидаются на host-классе (KraabUserbot):
    client: "Client | None"
    me: object | None  # pyrogram.types.User | None
    _idea_tick_state: dict[str, float]
    _owner_notify_target: int | str

    async def _idea_features_tick_loop(self) -> None:
        """Единый периодический tick для idea-features (Idea 5/18/+).

        Периодичность:
        - reply_scheduler.pop_due() — каждые 30 секунд (отправка отложенных ответов)
        - daily_brief — раз в день в 08:00 local (если KRAB_DAILY_BRIEF_ENABLED=1)
        - channel_digest — раз в день в 09:00 в KRAB_CHANNEL_DIGEST_CHAT_ID
        - pattern_detector.detect_patterns() — каждые 6 часов
        - skill_curator A/B eval — 04:00 UTC если KRAB_SKILL_CURATOR_CRON_ENABLED=1

        State (last_run timestamps) — в self._idea_tick_state.
        Каждая фича обёрнута в try/except: fail-open, не валит петлю.
        """
        # Шаг тика: 30 секунд (минимально для reply_scheduler)
        tick_interval = 30.0
        # Периоды (секунды)
        pattern_period = 6 * 3600  # 6 часов
        daily_reentry_guard = 23 * 3600  # 23 часа — защита от двойного запуска

        while True:
            try:
                await asyncio.sleep(tick_interval)
            except asyncio.CancelledError:
                raise

            now_ts = time.time()
            now_local = datetime.now().astimezone()

            # ── 1. reply_scheduler.pop_due ─────────────────────────────
            try:
                from ..core.reply_scheduler import reply_scheduler  # noqa: PLC0415

                due = reply_scheduler.pop_due()
                for job in due:
                    try:
                        kwargs: dict[str, Any] = {}
                        meta = dict(getattr(job, "metadata", {}) or {})
                        rt = meta.get("reply_to_message_id")
                        if rt is not None:
                            try:
                                kwargs["reply_to_message_id"] = int(rt)
                            except (TypeError, ValueError):
                                pass
                        await self.client.send_message(job.chat_id, job.text, **kwargs)
                        logger.info(
                            "idea_tick_reply_sent",
                            job_id=job.job_id,
                            chat_id=job.chat_id,
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "idea_tick_reply_send_failed",
                            job_id=getattr(job, "job_id", "?"),
                            error=str(exc),
                            error_type=type(exc).__name__,
                        )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "idea_tick_reply_scheduler_failed",
                    error=str(exc),
                    error_type=type(exc).__name__,
                )

            # ── 2. daily_brief — 08:00 local в self-DM ─────────────────
            try:
                if os.getenv("KRAB_DAILY_BRIEF_ENABLED", "0").strip().lower() in (
                    "1",
                    "true",
                    "yes",
                ):
                    last_run = float(self._idea_tick_state.get("daily_brief", 0.0))
                    if (
                        now_local.hour == 8
                        and (now_ts - last_run) > daily_reentry_guard
                        and self.me is not None
                    ):
                        from ..core.daily_brief import DailyBriefBuilder  # noqa: PLC0415

                        builder = DailyBriefBuilder()
                        text = await builder.build_brief()
                        if text:
                            await self.client.send_message(self._owner_notify_target, text)
                            logger.info("idea_tick_daily_brief_sent", chars=len(text))
                        else:
                            logger.info("idea_tick_daily_brief_empty")
                        self._idea_tick_state["daily_brief"] = now_ts
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "idea_tick_daily_brief_failed",
                    error=str(exc),
                    error_type=type(exc).__name__,
                )

            # ── 3. channel_digest — 09:00 local в configured chat ──────
            try:
                digest_chat = os.getenv("KRAB_CHANNEL_DIGEST_CHAT_ID", "").strip()
                if digest_chat:
                    last_run = float(self._idea_tick_state.get("channel_digest", 0.0))
                    if now_local.hour == 9 and (now_ts - last_run) > daily_reentry_guard:
                        from ..core.channel_digest import (  # noqa: PLC0415
                            channel_digest_builder,
                        )

                        text = channel_digest_builder.build_digest()
                        if text:
                            try:
                                target: int | str = int(digest_chat)
                            except ValueError:
                                target = digest_chat
                            await self.client.send_message(target, text)
                            logger.info(
                                "idea_tick_channel_digest_sent",
                                chat=digest_chat,
                                chars=len(text),
                            )
                        else:
                            logger.info("idea_tick_channel_digest_empty")
                        self._idea_tick_state["channel_digest"] = now_ts
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "idea_tick_channel_digest_failed",
                    error=str(exc),
                    error_type=type(exc).__name__,
                )

            # ── 4. pattern_detector — каждые 6 часов ───────────────────
            try:
                last_run = float(self._idea_tick_state.get("pattern_detector", 0.0))
                if (now_ts - last_run) >= pattern_period:
                    from ..core.proactive_suggestions import (  # noqa: PLC0415
                        pattern_detector,
                    )

                    suggestions = pattern_detector.detect_patterns()
                    logger.info(
                        "proactive_patterns_detected",
                        count=len(suggestions),
                    )
                    self._idea_tick_state["pattern_detector"] = now_ts
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "idea_tick_pattern_detector_failed",
                    error=str(exc),
                    error_type=type(exc).__name__,
                )

            # ── 5. Wave 38-B: skill_curator A/B evaluation — 04:00 UTC ─
            # SkillCurator Step 4: auto-apply если кандидат победил в A/B тесте.
            # Запускается раз в сутки в 04:00 UTC. Гейт: KRAB_SKILL_CURATOR_CRON_ENABLED=1.
            try:
                if os.getenv("KRAB_SKILL_CURATOR_CRON_ENABLED", "0").strip().lower() in (
                    "1",
                    "true",
                    "yes",
                ):
                    now_utc = datetime.now(timezone.utc)
                    last_run = float(self._idea_tick_state.get("skill_curator_eval", 0.0))
                    if now_utc.hour == 4 and (now_ts - last_run) > daily_reentry_guard:
                        await _evaluate_and_apply_skill_curator_proposals()
                        self._idea_tick_state["skill_curator_eval"] = now_ts
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "idea_tick_skill_curator_eval_failed",
                    error=str(exc),
                    error_type=type(exc).__name__,
                )

    async def _command_usage_save_loop(self) -> None:
        """Периодически (каждые 5 минут) сохраняет счётчики команд на диск."""
        while True:
            await asyncio.sleep(300)  # 5 минут
            try:
                from ..core.command_registry import save_usage as _save_usage  # noqa: PLC0415

                _save_usage()
            except Exception as exc:  # noqa: BLE001
                logger.warning("command_usage_periodic_save_failed", error=str(exc))
