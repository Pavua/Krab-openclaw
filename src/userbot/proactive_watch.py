# -*- coding: utf-8 -*-
"""Wave 31-K: ProactiveWatchMixin — alert + periodic baseline capture.

Зачем:
- bridge до 31-K содержал ~4816 LOC, proactive_watch блок —
  cohesive ~95 LOC (alert delivery + ensure-start + capture loop).
- Mixin использует: ``self.client``, ``self._owner_notify_target``,
  ``self._split_message``, ``self._proactive_watch_task``,
  ``self._error_digest_task``, ``self._weekly_digest_task``,
  ``self._nightly_summary_task``, ``self._openclaw_health_alert_task``.

Контракт:
- ``_send_proactive_watch_alert`` — primary (userbot) → fallback reserve_bot.
  Raises RuntimeError если оба недоступны (caller должен поймать).
- ``_ensure_proactive_watch_started`` — idempotent boot всех related loops:
  proactive_watch, error_digest, weekly_digest, nightly_summary,
  openclaw_health_alert. Гейт по config.PROACTIVE_WATCH_ENABLED.
- ``_run_proactive_watch_loop`` — baseline-aware capture loop. Первый
  pass = baseline без alert, последующие — только при переходах состояния.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import structlog

from ..config import config
from ..core.proactive_watch import proactive_watch
from ..reserve_bot import reserve_bot

if TYPE_CHECKING:
    from pyrogram import Client

logger = structlog.get_logger("Krab.userbot.proactive_watch")


class ProactiveWatchMixin:
    """Mixin: proactive watch alerts + ensure-start orchestration."""

    # Атрибуты, которые ожидаются на host-классе (KraabUserbot):
    client: "Client | None"
    _owner_notify_target: int | str
    _proactive_watch_task: asyncio.Task | None
    _error_digest_task: asyncio.Task | None

    async def _send_proactive_watch_alert(self, text: str) -> None:
        """
        Отправляет watch-alert владельцу через userbot.
        Fallback: если userbot offline — пробует reserve bot (Phase 2.1).
        """
        clean_text = str(text or "").strip()
        if self.client and self.client.is_connected:
            for part in self._split_message(clean_text):
                await self.client.send_message(self._owner_notify_target, part)
            return
        # userbot недоступен — пробуем reserve bot
        if reserve_bot.is_running:
            logger.info("proactive_watch_alert_via_reserve_bot")
            await reserve_bot.send_to_owner(f"[reserve] {clean_text}")
            return
        raise RuntimeError("telegram_client_not_ready")

    def _ensure_proactive_watch_started(self) -> None:
        """Запускает фоновый proactive watch, если он включён конфигом."""
        if not config.PROACTIVE_WATCH_ENABLED:
            return
        if self._proactive_watch_task and not self._proactive_watch_task.done():
            return
        self._proactive_watch_task = asyncio.create_task(self._run_proactive_watch_loop())
        # Запускаем периодическую сводку ошибок (каждые 6 часов)
        if self._error_digest_task is None or self._error_digest_task.done():
            self._error_digest_task = proactive_watch.start_error_digest_loop()
        # WeeklyDigest: подключаем Telegram delivery callback + запускаем loop
        try:
            from ..core.weekly_digest import weekly_digest  # noqa: PLC0415

            weekly_digest.set_telegram_callback(self._send_proactive_watch_alert)
            wdt = getattr(self, "_weekly_digest_task", None)
            if wdt is None or wdt.done():
                self._weekly_digest_task = weekly_digest.start_weekly_digest_loop()
        except Exception as exc:  # noqa: BLE001
            logger.warning("weekly_digest_setup_failed", error=str(exc))

        # NightlySummary: привязываем bot и запускаем daily loop (fire в NIGHTLY_SUMMARY_HOUR)
        try:
            from ..core.nightly_summary import nightly_summary_service  # noqa: PLC0415

            nightly_summary_service.bind_bot(self.client)
            nst = getattr(self, "_nightly_summary_task", None)
            if nst is None or nst.done():
                self._nightly_summary_task = nightly_summary_service.start()
        except Exception as exc:  # noqa: BLE001
            logger.warning("nightly_summary_setup_failed", error=str(exc))

        # OpenClaw gateway health alert: 3 последовательных сбоя → Telegram alert
        _oc_task = getattr(self, "_openclaw_health_alert_task", None)
        if _oc_task is None or _oc_task.done():
            self._openclaw_health_alert_task = proactive_watch.start_openclaw_health_alert_loop(
                notifier=self._send_proactive_watch_alert,
            )

        # Wave 44-V: регистрируем codex quota notifier + recovery probe loop
        try:
            from ..openclaw_client import openclaw_client as _oc_client

            _oc_client._codex_quota_notifier = self._send_proactive_watch_alert  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001
            logger.debug("codex_quota_notifier_wire_failed", error=str(exc))

        _cq_task = getattr(self, "_codex_quota_recovery_task", None)
        if _cq_task is None or _cq_task.done():
            self._codex_quota_recovery_task = asyncio.create_task(
                self._run_codex_quota_recovery_loop()
            )

    async def _run_codex_quota_recovery_loop(self) -> None:
        """Wave 44-V: раз в час проверяет, восстановились ли codex accounts.

        Если ANY account стал available после exhaustion — отправляет debounced
        recovery alert владельцу и снимает codex_disabled флаг.
        """
        interval_sec = 3600  # 1h — соответствует transient cooldown минимуму
        try:
            while True:
                await asyncio.sleep(interval_sec)
                try:
                    from ..integrations.codex_account_rotator import list_accounts
                    from ..integrations.codex_quota_state import (
                        is_codex_disabled,
                        mark_codex_recovered,
                    )

                    if not is_codex_disabled():
                        continue
                    accounts = list_accounts()
                    available = [a for a in accounts if a.get("available") and a.get("logged_in")]
                    if not available:
                        continue
                    # Хотя бы один аккаунт available → recovery transition
                    if mark_codex_recovered():
                        try:
                            await self._send_proactive_watch_alert(
                                "✅ Codex восстановлен — primary вернулся к codex-cli/* "
                                f"(доступно accounts: {len(available)})."
                            )
                        except Exception as notify_exc:  # noqa: BLE001
                            logger.debug("codex_recovery_notify_failed", error=str(notify_exc))
                except Exception as inner_exc:  # noqa: BLE001
                    logger.debug("codex_quota_recovery_iter_failed", error=str(inner_exc))
        except asyncio.CancelledError:
            logger.info("codex_quota_recovery_task_cancelled")
        except Exception as exc:  # noqa: BLE001
            logger.warning("codex_quota_recovery_loop_failed", error=str(exc), non_fatal=True)

    async def _run_proactive_watch_loop(self) -> None:
        """
        Периодически снимает owner-oriented runtime digest.

        Первый проход строит baseline без alert.
        Следующие проходы сообщают только про реальные переходы состояния.
        """
        interval_sec = max(60, config.PROACTIVE_WATCH_INTERVAL_SEC)
        baseline_ready = False
        try:
            while True:
                if self.client and self.client.is_connected:
                    # Даём runtime прогреться: scheduler, wake-up и route warmup должны
                    # успеть стабилизироваться до baseline, иначе первый snapshot
                    # получается правдивым, но слишком "холодным" и мало полезным.
                    if not baseline_ready:
                        await asyncio.sleep(min(20, max(5, interval_sec // 6)))
                    result = await proactive_watch.capture(
                        manual=False,
                        persist_memory=True,
                        notify=baseline_ready,
                        notifier=self._send_proactive_watch_alert,
                    )
                    baseline_ready = True
                    if result.get("reason"):
                        logger.info(
                            "proactive_watch_transition_captured",
                            reason=result.get("reason"),
                            alerted=bool(result.get("alerted")),
                            wrote_memory=bool(result.get("wrote_memory")),
                        )
                await asyncio.sleep(interval_sec)
        except asyncio.CancelledError:
            logger.info("proactive_watch_task_cancelled")
        except Exception as exc:  # noqa: BLE001
            logger.warning("proactive_watch_loop_failed", error=str(exc), non_fatal=True)
