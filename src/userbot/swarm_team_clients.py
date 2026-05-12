# -*- coding: utf-8 -*-
"""Wave 31-G: SwarmTeamClientsMixin — выделяет управление per-team Pyrogram clients.

Зачем:
- bridge до 31-G содержал ~5697 LOC, swarm-clients lifecycle (start/stop/init) —
  cohesive 171 LOC, изолированный от main bot logic.
- Mixin использует только: ``self._session_workdir``, ``self._swarm_team_clients``,
  ``self._swarm_clients_warmed``.

Контракт:
- ``_start_swarm_team_clients`` — создаёт Pyrogram Clients для team accounts +
  warmup peer cache (только при первом запуске).
- ``_stop_swarm_team_clients`` — graceful stop с storage guard + WAL checkpoint
  каждой team session независимо.
- ``_init_swarm_team_clients`` — background init: start + bind + register
  message handlers.

Sentry-relevant: при крахе одного team-клиента остальные продолжают работать
(per-team try/except), iteration отдельно от lifecycle main client'a.
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog
from pyrogram import Client

from ..config import config
from ..core.swarm_channels import swarm_channels
from ..openclaw_client import openclaw_client

if TYPE_CHECKING:
    pass

logger = structlog.get_logger("Krab.userbot.swarm_team_clients")


# Wave 63-B: per-client GetState probe для split-brain detection в swarm
# Telegram-сессиях. Если все 4 поля (pts/qts/seq/date) НЕ изменились за два
# интервала AND client connected → split-brain → graceful reconnect.
_SWARM_PROBE_INTERVAL_SEC = max(30, int(os.environ.get("KRAB_SWARM_PROBE_INTERVAL_SEC", "240")))
_SWARM_PROBE_TIMEOUT_SEC = float(os.environ.get("KRAB_SWARM_PROBE_TIMEOUT_SEC", "8.0"))


def _swarm_probe_enabled() -> bool:
    """Wave 63-B: env-gate, чтобы можно было отключить probe без рестарта."""
    return os.environ.get("KRAB_SWARM_PROBE_ENABLED", "1").strip().lower() in {
        "1",
        "true",
        "yes",
    }


class SwarmTeamClientsMixin:
    """Mixin: lifecycle (start/stop/init) per-team Pyrogram clients."""

    # Атрибуты, которые ожидаются на host-классе (KraabUserbot):
    _session_workdir: Path
    _swarm_team_clients: dict[str, Client]
    _swarm_clients_warmed: set[str]  # lazy-initialized в _start_swarm_team_clients
    # Wave 63-B: snapshot {pts, qts, seq, date, ts} последнего успешного
    # GetState на каждый swarm-клиент + handles на probe tasks для cleanup.
    _last_swarm_pts: dict[str, dict[str, int | float]]
    _swarm_probe_tasks: dict[str, asyncio.Task[None]]

    async def _start_swarm_team_clients(self) -> dict[str, Any]:
        """Создаёт и стартует Pyrogram Clients для per-team аккаунтов свёрма."""
        accounts = config.load_swarm_team_accounts()
        if not accounts:
            return {}

        # Wave 24-B: отслеживаем уже прогретые клиенты, чтобы не повторять get_dialogs
        if not hasattr(self, "_swarm_clients_warmed"):
            self._swarm_clients_warmed = set()

        startup_t0 = time.monotonic()
        started: dict[str, Any] = {}
        for team, acct in accounts.items():
            session_name = acct.get("session_name", f"swarm_{team}")
            try:
                # Corruption-aware preflight: WAL/journal удаляем ТОЛЬКО если
                # integrity_check провален или DB не открывается. Безусловная
                # чистка опасна — uncheckpointed peer-cache writes из предыдущего
                # запуска теряются (Session 32 P1 backlog).
                _sess_path = Path(self._session_workdir) / f"{session_name}.session"
                if _sess_path.exists():
                    from ..bootstrap.db_corruption_guard import (  # noqa: PLC0415
                        integrity_check as _swarm_integrity_check,
                    )

                    _ok, _detail = _swarm_integrity_check(_sess_path)
                    _journal = _sess_path.with_suffix(".session-journal")
                    _wal = _sess_path.with_suffix(".session-wal")
                    if _ok:
                        logger.info(
                            "swarm_session_integrity_ok",
                            team=team,
                            file=str(_sess_path),
                            detail=_detail,
                        )
                    else:
                        logger.warning(
                            "swarm_session_integrity_failed",
                            team=team,
                            file=str(_sess_path),
                            detail=_detail,
                        )
                        for _lockf in (_journal, _wal):
                            if _lockf.exists():
                                try:
                                    _lockf.unlink()
                                    logger.info(
                                        "swarm_stale_lock_cleaned",
                                        team=team,
                                        file=str(_lockf),
                                    )
                                except OSError:
                                    pass
                cl = Client(
                    session_name,
                    api_id=config.TELEGRAM_API_ID,
                    api_hash=config.TELEGRAM_API_HASH,
                    workdir=str(self._session_workdir),
                )
                await asyncio.wait_for(cl.start(), timeout=15)
                me = await cl.get_me()
                started[team.lower()] = cl
                logger.info(
                    "swarm_team_client_started",
                    team=team,
                    session=session_name,
                    username=getattr(me, "username", None),
                    user_id=getattr(me, "id", None),
                )
                # Warm-up peer cache: get_dialogs загружает все чаты включая недавно
                # добавленные группы (иначе send_message → CHAT_ID_INVALID).
                # Wave 24-B: прогреваем только при первом запуске клиента, иначе
                # 5 параллельных get_dialogs триггерят DC reconnect flood.
                if team not in self._swarm_clients_warmed:
                    try:
                        async for _ in cl.get_dialogs(limit=50):
                            pass
                        self._swarm_clients_warmed.add(team)
                        logger.info("swarm_team_client_warmed_up", team=team)
                    except Exception as warm_exc:  # noqa: BLE001
                        logger.warning(
                            "swarm_team_client_warmup_failed",
                            team=team,
                            error=str(warm_exc),
                        )
                else:
                    logger.debug("swarm_warmup_skipped_already_warmed", team=team)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "swarm_team_client_start_failed",
                    team=team,
                    session=session_name,
                    error=repr(exc),
                )
            # Wave 24-B: stagger startup чтобы не триггерить DC reconnect flood
            await asyncio.sleep(1.5)

        logger.info(
            "swarm_clients_startup_complete",
            count=len(accounts),
            started=len(started),
            elapsed_ms=round((time.monotonic() - startup_t0) * 1000, 1),
        )
        return started

    async def _stop_swarm_team_clients(self) -> None:
        """Останавливает все per-team swarm clients.

        Безопасно вызывать даже если `_init_swarm_team_clients` не отработал
        (например, в тестовых фикстурах или при раннем сбое старта).
        """
        # Wave 63-B: сначала останавливаем probe-задачи, чтобы они не пытались
        # инвокать на закрывающемся клиенте.
        probe_tasks = getattr(self, "_swarm_probe_tasks", None)
        if probe_tasks:
            for team, task in list(probe_tasks.items()):
                try:
                    task.cancel()
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "swarm_probe_task_cancel_failed",
                        team=team,
                        error=str(exc),
                    )
            for team, task in list(probe_tasks.items()):
                try:
                    await task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
            probe_tasks.clear()

        clients = getattr(self, "_swarm_team_clients", None)
        if not clients:
            return
        # Импорт здесь, чтобы избежать циклической зависимости при загрузке модуля.
        from .session import SessionMixin, checkpoint_session_wal  # noqa: PLC0415

        for team, cl in list(clients.items()):
            session_path: Path | None = None
            try:
                # Перед stop() ставим storage guard на каждый swarm client —
                # иначе фоновые pyrogram-задачи (Session.restart / update_peers)
                # после stop() добегают до закрытой sqlite-базы и спамят Sentry
                # (~10 events/24h × 4 команды = заметная доля PYTHON-FASTAPI-1).
                SessionMixin._arm_storage_shutdown_guard_for_client(cl)
                # Session 33 P1: запоминаем путь к .session ДО stop() — после stop()
                # storage может быть детачена. workdir/name стабильны.
                try:
                    _wd = getattr(cl, "workdir", None) or self._session_workdir
                    _name = getattr(cl, "name", None) or f"swarm_{team}"
                    session_path = Path(_wd) / f"{_name}.session"
                except Exception:  # noqa: BLE001
                    session_path = None
                if cl.is_connected:
                    await cl.stop()
                logger.info("swarm_team_client_stopped", team=team)
            except Exception as exc:  # noqa: BLE001
                logger.warning("swarm_team_client_stop_failed", team=team, error=str(exc))
            # WAL truncate — best-effort, не зависит от успеха stop().
            # Каждая команда checkpoint-ится независимо: ошибка одной не валит остальных.
            if session_path is not None:
                try:
                    checkpoint_session_wal(session_path)
                except Exception as wal_exc:  # noqa: BLE001
                    logger.warning(
                        "swarm_team_wal_checkpoint_unexpected_failure",
                        team=team,
                        error=str(wal_exc),
                        non_fatal=True,
                    )
        clients.clear()

    async def _init_swarm_team_clients(self) -> None:
        """Background init per-team swarm clients (не блокирует основной бот)."""
        try:
            self._swarm_team_clients = await self._start_swarm_team_clients()
            for team, cl in self._swarm_team_clients.items():
                swarm_channels.bind_team_client(team, cl)
            # Регистрируем message handlers для team listener
            if self._swarm_team_clients:
                from ..core.swarm_team_listener import (  # noqa: PLC0415
                    register_team_message_handler,
                )

                for team, cl in self._swarm_team_clients.items():
                    register_team_message_handler(team, cl, openclaw_client)
            # Wave 63-B: запускаем per-client GetState probe loops для
            # split-brain detection на каждой swarm-сессии. Env gate +
            # default ON, чтобы можно было быстро отключить при инциденте.
            if self._swarm_team_clients and _swarm_probe_enabled():
                self._last_swarm_pts = {}
                self._swarm_probe_tasks = {}
                for team, cl in self._swarm_team_clients.items():
                    task = asyncio.create_task(
                        self._per_client_probe_loop(team, cl),
                        name=f"swarm_probe_{team}",
                    )
                    self._swarm_probe_tasks[team] = task
                logger.info(
                    "swarm_probe_loops_started",
                    teams=list(self._swarm_probe_tasks.keys()),
                    interval_sec=_SWARM_PROBE_INTERVAL_SEC,
                )
            if self._swarm_team_clients:
                logger.info(
                    "swarm_team_clients_ready",
                    teams=list(self._swarm_team_clients.keys()),
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("swarm_team_clients_init_failed", error=repr(exc))

    async def _per_client_probe_loop(self, team_name: str, client: Client) -> None:
        """Wave 63-B: periodic GetState probe для swarm-клиента.

        Алгоритм:
          1. Каждые _SWARM_PROBE_INTERVAL_SEC секунд invoke `GetState()`.
          2. Сохраняем snapshot `{pts, qts, seq, date, ts}` в
             `_last_swarm_pts[team]`.
          3. Если за два интервала ни одно из четырёх полей не двинулось AND
             client остаётся connected → log `swarm_split_brain_detected` +
             пробуем graceful reconnect конкретно этого клиента.
          4. Sentry hygiene: транзиентные ошибки invoke логируем warning,
             не валим loop.

        Reconnect: stop(block=True) + start() этого клиента. Главный kraab
        client НЕ трогаем — за ним следит свой watchdog.
        """
        # Lazy-import чтобы не тянуть pyrogram.raw на import-time mixin
        from pyrogram.raw.functions.updates import GetState  # noqa: PLC0415

        # Хелпер lazy-init storage (на случай если loop стартанул раньше
        # _init_swarm_team_clients из-за reordering в host-классе).
        if not hasattr(self, "_last_swarm_pts"):
            self._last_swarm_pts = {}

        stagnant_intervals = 0  # сколько подряд интервалов поля не двигались

        logger.info(
            "swarm_probe_loop_started",
            team=team_name,
            interval_sec=_SWARM_PROBE_INTERVAL_SEC,
        )

        try:
            while True:
                try:
                    await asyncio.sleep(_SWARM_PROBE_INTERVAL_SEC)
                except asyncio.CancelledError:
                    raise

                if not getattr(client, "is_connected", False):
                    # Клиент отключён — не зондируем, сбрасываем stagnant
                    # счётчик чтобы false-detect не сработал при reconnect.
                    stagnant_intervals = 0
                    logger.debug("swarm_probe_skip_disconnected", team=team_name)
                    continue

                try:
                    state = await asyncio.wait_for(
                        client.invoke(GetState()),
                        timeout=_SWARM_PROBE_TIMEOUT_SEC,
                    )
                except asyncio.CancelledError:
                    raise
                except asyncio.TimeoutError:
                    logger.warning(
                        "swarm_probe_get_state_timeout",
                        team=team_name,
                        timeout_sec=_SWARM_PROBE_TIMEOUT_SEC,
                    )
                    continue
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "swarm_probe_get_state_failed",
                        team=team_name,
                        error=str(exc)[:200],
                    )
                    continue

                snapshot: dict[str, int | float] = {
                    "pts": int(getattr(state, "pts", 0) or 0),
                    "qts": int(getattr(state, "qts", 0) or 0),
                    "seq": int(getattr(state, "seq", 0) or 0),
                    "date": int(getattr(state, "date", 0) or 0),
                    "ts": time.time(),
                }
                prev = self._last_swarm_pts.get(team_name)
                self._last_swarm_pts[team_name] = snapshot

                if prev is None:
                    # Первый probe — baseline записан, судить пока не о чем.
                    stagnant_intervals = 0
                    logger.debug(
                        "swarm_probe_baseline_recorded",
                        team=team_name,
                        pts=snapshot["pts"],
                    )
                    continue

                moved = any(
                    snapshot[field] != prev.get(field) for field in ("pts", "qts", "seq", "date")
                )
                if moved:
                    stagnant_intervals = 0
                    logger.debug(
                        "swarm_probe_movement_ok",
                        team=team_name,
                        pts=snapshot["pts"],
                        seq=snapshot["seq"],
                    )
                    continue

                # Ничего не двигалось за этот интервал
                stagnant_intervals += 1
                logger.debug(
                    "swarm_probe_stagnant_interval",
                    team=team_name,
                    stagnant_intervals=stagnant_intervals,
                    pts=snapshot["pts"],
                )

                if stagnant_intervals < 2:
                    continue

                # >= 2 интервалов подряд без движения → split-brain
                logger.warning(
                    "swarm_split_brain_detected",
                    team=team_name,
                    stagnant_intervals=stagnant_intervals,
                    pts=snapshot["pts"],
                    qts=snapshot["qts"],
                    seq=snapshot["seq"],
                    date=snapshot["date"],
                )

                reconnected = await self._try_reconnect_swarm_client(team_name, client)
                if reconnected:
                    logger.info("swarm_split_brain_reconnect_ok", team=team_name)
                else:
                    logger.warning("swarm_split_brain_reconnect_failed", team=team_name)
                # В любом случае сбрасываем счётчик — следующая проверка
                # начнёт новый baseline после reconnect/failure.
                stagnant_intervals = 0
        except asyncio.CancelledError:
            logger.info("swarm_probe_loop_cancelled", team=team_name)
            raise
        except Exception as exc:  # noqa: BLE001
            # Фатальная ошибка в loop — логируем и выходим, чтобы не
            # маскировать проблему. Сама задача завершится; cleanup в
            # _stop_swarm_team_clients просто увидит done task.
            logger.warning(
                "swarm_probe_loop_crashed",
                team=team_name,
                error=str(exc)[:200],
            )

    async def _try_reconnect_swarm_client(self, team_name: str, client: Client) -> bool:
        """Wave 63-B: graceful reconnect одного swarm Pyrogram-клиента.

        Стратегия mirror'ит `_try_reconnect_pyrofork` из main kraab, но
        локально на конкретного team-клиента. Главное — не дёргать main
        client, чтобы избежать cascade restart всего userbot'а.
        """
        try:
            if getattr(client, "is_connected", False):
                await client.stop(block=True)
            await client.start()
            return True
        except (ConnectionError, RuntimeError, OSError) as exc:
            logger.warning(
                "swarm_reconnect_strategy1_failed",
                team=team_name,
                error=str(exc)[:200],
            )
            return False
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "swarm_reconnect_strategy1_unexpected",
                team=team_name,
                error=str(exc)[:200],
            )
            return False
