# -*- coding: utf-8 -*-
"""
Mixin для управления Pyrogram session lifecycle: создание/уничтожение клиента,
watchdog, recovery, purge session-файлов, диагностика sqlite-сессий.

Часть декомпозиции `src/userbot_bridge.py` (session 4+, 2026-04-09).
См. `docs/USERBOT_BRIDGE_SPLIT_PROPOSAL.md` для полной стратегии.
"""

from __future__ import annotations

import asyncio
import shutil
import sqlite3
import types
from pathlib import Path

from pyrogram import Client

from ..config import config
from ..core.logger import get_logger

logger = get_logger(__name__)


class SessionMixin:
    """
    Pyrogram session lifecycle: client create/stop, watchdog, recovery, purge.

    Mixin для `KraabUserbot`: управляет жизненным циклом Telegram-сессии,
    включая пересоздание клиента, сериализованный start/stop, watchdog-пробы,
    recovery при протухшей auth key и очистку session-файлов.
    """

    # ------------------------------------------------------------------
    # Session file helpers
    # ------------------------------------------------------------------

    def _get_session_dirs(self) -> list[Path]:
        """
        Возвращает список каталогов, где могли лежать session-файлы.
        Порядок важен: сначала новый канонический путь, затем legacy.
        """
        dirs = [
            self._session_workdir,
            config.BASE_DIR,
            config.BASE_DIR / "src",
            Path.cwd(),
        ]
        unique: list[Path] = []
        seen: set[str] = set()
        for item in dirs:
            key = str(item.resolve()) if item.exists() else str(item)
            if key in seen:
                continue
            seen.add(key)
            unique.append(item)
        return unique

    def _session_name(self) -> str:
        """Нормализованное имя Telegram session-файла."""
        return str(config.TELEGRAM_SESSION_NAME or "kraab").strip() or "kraab"

    def _primary_session_file(self) -> Path:
        """Канонический session-файл, который использует текущий Pyrogram client."""
        return self._session_workdir / f"{self._session_name()}.session"

    def _inspect_session_file(self, session_file: Path) -> dict:
        """
        Легковесная диагностика sqlite session-файла:
        - есть ли auth key;
        - есть ли user binding (user_id > 0), т.е. завершенный логин.
        """
        snapshot = {
            "path": str(session_file),
            "exists": session_file.exists(),
            "has_auth_key": False,
            "has_user_binding": False,
            "user_id": 0,
            "is_bot": None,
            "error": "",
        }
        if not session_file.exists():
            return snapshot
        try:
            with sqlite3.connect(str(session_file), timeout=0.7) as conn:
                row = conn.execute(
                    "SELECT length(auth_key), coalesce(user_id,0), is_bot FROM sessions LIMIT 1"
                ).fetchone()
            if row:
                auth_len = int(row[0] or 0)
                user_id = int(row[1] or 0)
                is_bot = row[2]
                snapshot["has_auth_key"] = auth_len > 0
                snapshot["has_user_binding"] = user_id > 0
                snapshot["user_id"] = user_id
                snapshot["is_bot"] = None if is_bot is None else int(is_bot)
        except Exception as exc:  # noqa: BLE001
            snapshot["error"] = str(exc)
        return snapshot

    def _primary_session_snapshot(self) -> dict:
        """Snapshot канонического session-файла (из рабочего каталога клиента)."""
        return self._inspect_session_file(self._primary_session_file())

    def _restore_primary_session_from_legacy(self) -> bool:
        """
        Восстанавливает канонический session-файл из legacy-пути, если:
        - в рабочем пути сессия отсутствует или неавторизована;
        - в одном из legacy-путей найдена валидная авторизованная сессия.

        Это устраняет ложные relogin после миграций путей session-файла.
        """
        primary_file = self._primary_session_file()
        primary_snapshot = self._inspect_session_file(primary_file)
        if primary_snapshot["has_user_binding"]:
            return False

        session_name = self._session_name()
        for base_dir in self._get_session_dirs():
            if base_dir == self._session_workdir:
                continue
            candidate = base_dir / f"{session_name}.session"
            candidate_snapshot = self._inspect_session_file(candidate)
            if not candidate_snapshot["has_user_binding"]:
                continue
            try:
                self._session_workdir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(candidate, primary_file)
                for suffix in (".session-shm", ".session-wal", ".session-journal"):
                    sidecar = base_dir / f"{session_name}{suffix}"
                    if sidecar.exists():
                        shutil.copy2(sidecar, self._session_workdir / sidecar.name)
                logger.info(
                    "telegram_session_restored_from_legacy",
                    source=str(candidate),
                    target=str(primary_file),
                    user_id=candidate_snapshot["user_id"],
                )
                return True
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "telegram_session_restore_from_legacy_failed",
                    source=str(candidate),
                    target=str(primary_file),
                    error=str(exc),
                )
        return False

    def _session_file_exists(self) -> bool:
        """Проверяет наличие основного session-файла (`*.session`)."""
        session_name = str(config.TELEGRAM_SESSION_NAME or "kraab").strip() or "kraab"
        for base_dir in self._get_session_dirs():
            target = base_dir / f"{session_name}.session"
            if target.exists():
                return True
        return False

    # ------------------------------------------------------------------
    # Client create / start / stop
    # ------------------------------------------------------------------

    def _recreate_client(self) -> None:
        """
        Полностью пересоздает экземпляр Pyrogram Client и регистрирует хендлеры заново.
        Нужен для recovery после протухшей/битой сессии.
        """
        self.client = Client(
            config.TELEGRAM_SESSION_NAME,
            api_id=config.TELEGRAM_API_ID,
            api_hash=config.TELEGRAM_API_HASH,
            workdir=str(self._session_workdir),
        )
        self._session_workdir.mkdir(parents=True, exist_ok=True)
        logger.info(
            "telegram_client_created",
            session_name=config.TELEGRAM_SESSION_NAME,
            workdir=str(self._session_workdir),
        )
        self._setup_handlers()

    async def _start_client_serialized(self) -> None:
        """
        Сериализованный client.start(), чтобы избежать гонки start/stop над одним sqlite session-файлом.
        """
        async with self._client_lifecycle_lock:
            assert self.client is not None
            await self.client.start()

    async def _safe_stop_client(self, *, reason: str) -> None:
        """
        Безопасный stop Telegram-клиента.

        Почему:
        - во время shutdown pyrogram может падать на сохранении sqlite-сессии;
        - такие ошибки должны считаться non-fatal и не валить весь runtime.
        """
        async with self._client_lifecycle_lock:
            if not self.client:
                return
            if not self.client.is_connected:
                return
            try:
                self._arm_client_session_shutdown_guard()
                await self._cancel_client_restart_tasks()
                await self.client.stop()
            except Exception as exc:  # noqa: BLE001
                if self._is_sqlite_io_error(exc):
                    logger.warning(
                        "telegram_session_save_failed",
                        reason=reason,
                        error=str(exc),
                        non_fatal=True,
                    )
                    return
                logger.warning(
                    "telegram_client_stop_failed",
                    reason=reason,
                    error=str(exc),
                    non_fatal=False,
                )
                raise

    # ------------------------------------------------------------------
    # Shutdown guard & restart task cancellation
    # ------------------------------------------------------------------

    def _arm_client_session_shutdown_guard(self) -> None:
        """
        Ставит guard на `session.restart()` у текущего Pyrogram session.

        Почему это нужно:
        - Pyrogram создаёт внутренние restart-task'и сам и не отдаёт нам их ссылки;
        - во время controlled shutdown такие task'и не должны заново открывать transport;
        - без guard поздний restart иногда лезет в уже закрытую sqlite storage и шумит traceback'ом.
        """
        session = getattr(self.client, "session", None) if self.client else None
        if session is None:
            return

        setattr(session, "_krab_shutdown_requested", True)
        if getattr(session, "_krab_restart_guard_installed", False):
            return

        original_restart = session.restart

        async def _guarded_restart(session_self, *args, **kwargs):
            if getattr(session_self, "_krab_shutdown_requested", False):
                logger.info("telegram_session_restart_suppressed", reason="controlled_shutdown")
                return None
            try:
                return await original_restart(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001
                if getattr(
                    session_self, "_krab_shutdown_requested", False
                ) and self._is_sqlite_io_error(exc):
                    logger.warning(
                        "telegram_session_restart_ignored_after_shutdown",
                        error=str(exc),
                        non_fatal=True,
                    )
                    return None
                raise

        setattr(session, "_krab_original_restart", original_restart)
        setattr(session, "restart", types.MethodType(_guarded_restart, session))
        setattr(session, "_krab_restart_guard_installed", True)

    async def _cancel_client_restart_tasks(self) -> None:
        """
        Гасит висячие `Session.restart()` задачи Pyrogram для текущего session-объекта.

        Почему это нужно:
        - Pyrogram создаёт restart-task'и через `loop.create_task(...)` и не хранит на них ссылку;
        - при нашем `client.stop()` такой task может добежать до закрытой sqlite storage;
        - это даёт шумные `Task exception was never retrieved` и оставляет ложный след инцидента.
        """
        session = getattr(self.client, "session", None) if self.client else None
        if session is None:
            return

        current_task = asyncio.current_task()
        restart_tasks: list[asyncio.Task] = []
        for task in asyncio.all_tasks():
            if task is current_task or task.done():
                continue
            coro = task.get_coro()
            frame = getattr(coro, "cr_frame", None)
            if frame is None:
                continue
            if frame.f_locals.get("self") is not session:
                continue
            code = getattr(coro, "cr_code", None)
            if code is None or getattr(code, "co_name", "") != "restart":
                continue
            task.cancel()
            restart_tasks.append(task)

        if restart_tasks:
            await asyncio.gather(*restart_tasks, return_exceptions=True)

    # ------------------------------------------------------------------
    # Error classification helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _is_sqlite_io_error(exc: Exception) -> bool:
        """Определяет non-fatal ошибки sqlite при сохранении сессии Telegram."""
        if isinstance(exc, sqlite3.OperationalError):
            low = str(exc).lower()
            return "disk i/o error" in low or "database is locked" in low
        if isinstance(exc, sqlite3.ProgrammingError):
            low = str(exc).lower()
            return "closed database" in low
        low = str(exc).lower()
        return "disk i/o error" in low or "database is locked" in low or "closed database" in low

    @staticmethod
    def _is_auth_key_invalid(exc: Exception) -> bool:
        """True, если исключение связано с протухшей Telegram auth key."""
        text = str(exc).lower()
        return "auth key not found" in text or "auth_key_unregistered" in text

    @staticmethod
    def _is_db_locked_error(exc: Exception) -> bool:
        """True, если ошибка связана с блокировкой sqlite session-файла."""
        return "database is locked" in str(exc).lower()

    # ------------------------------------------------------------------
    # Session file cleanup
    # ------------------------------------------------------------------

    def _purge_telegram_session_files(self) -> list[str]:
        """
        Удаляет локальные файлы сессии Pyrogram.

        Почему:
        - После ошибки `auth key not found` сессия в SQLite обычно уже невалидна.
        - Очистка позволяет получить чистый интерактивный relogin без ручного поиска файлов.
        """
        session_name = str(config.TELEGRAM_SESSION_NAME or "kraab").strip() or "kraab"
        removed: list[str] = []
        for base_dir in self._get_session_dirs():
            for suffix in (".session", ".session-journal", ".session-shm", ".session-wal"):
                target = base_dir / f"{session_name}{suffix}"
                if target.exists():
                    try:
                        target.unlink()
                        removed.append(str(target))
                    except OSError as exc:
                        logger.warning(
                            "telegram_session_purge_failed", file=str(target), error=str(exc)
                        )
        return removed

    def _cleanup_telegram_session_locks(self) -> list[str]:
        """
        Удаляет только lock/journal файлы sqlite-сессии.
        Основной `.session` файл не трогаем.
        """
        session_name = str(config.TELEGRAM_SESSION_NAME or "kraab").strip() or "kraab"
        removed: list[str] = []
        for base_dir in self._get_session_dirs():
            for suffix in (".session-journal", ".session-shm", ".session-wal"):
                target = base_dir / f"{session_name}{suffix}"
                if target.exists():
                    try:
                        target.unlink()
                        removed.append(str(target))
                    except OSError as exc:
                        logger.warning(
                            "telegram_session_lock_cleanup_failed", file=str(target), error=str(exc)
                        )
        return removed

    # ------------------------------------------------------------------
    # Recovery & watchdog
    # ------------------------------------------------------------------

    async def _recover_telegram_session(self, reason: str) -> None:
        """
        Контролируемая деградация при невалидной Telegram-сессии:
        - останавливаем текущий клиент;
        - НЕ удаляем session-файл автоматически;
        - переводим runtime в `login_required`.
        """
        if self._session_recovery_lock.locked():
            return
        async with self._session_recovery_lock:
            logger.warning("telegram_session_recovery_started", reason=reason)
            try:
                await self._safe_stop_client(reason="session_recovery")
            except Exception as exc:  # noqa: BLE001
                logger.warning("telegram_session_recovery_stop_failed", error=str(exc))
            self._mark_manual_relogin_required(
                reason="session_recovery_manual_relogin",
                error=str(reason),
            )
            self._ensure_maintenance_started()
            logger.warning(
                "telegram_session_recovery_requires_manual_relogin",
                reason=reason,
            )

    async def _telegram_session_watchdog(self) -> None:
        """
        Периодически проверяет валидность Telegram-сессии.
        Если auth key протухла — auto-recovery. Если transport disconnected —
        auto-reconnect через restart() (P1 fix session 5).
        """
        interval_sec = int(getattr(config, "TELEGRAM_SESSION_HEARTBEAT_SEC", 45))
        probe_timeout_sec = float(
            getattr(config, "TELEGRAM_SESSION_PROBE_TIMEOUT_SEC", 15.0) or 15.0
        )
        probe_timeout_sec = max(5.0, probe_timeout_sec)
        failure_limit = int(getattr(config, "TELEGRAM_SESSION_PROBE_FAILURE_LIMIT", 3) or 3)
        failure_limit = max(1, failure_limit)
        _reconnect_cooldown_sec = 60.0  # минимум между reconnect попытками
        _last_reconnect_ts = 0.0
        while True:
            try:
                await asyncio.sleep(max(15, interval_sec))
                if not self.client or not self.client.is_connected:
                    self._telegram_probe_failures += 1
                    if self._telegram_probe_failures >= failure_limit:
                        self._mark_transport_degraded(
                            reason="client_not_connected",
                            error="Pyrogram client помечен как disconnected",
                        )
                        # P1: auto-reconnect вместо пассивного ожидания
                        import time as _time  # noqa: PLC0415

                        now = _time.monotonic()
                        if now - _last_reconnect_ts >= _reconnect_cooldown_sec:
                            _last_reconnect_ts = now
                            logger.warning(
                                "telegram_watchdog_auto_reconnect",
                                failures=self._telegram_probe_failures,
                            )
                            try:
                                await self.restart(reason="watchdog_auto_reconnect")
                                self._telegram_probe_failures = 0
                            except Exception as restart_exc:  # noqa: BLE001
                                logger.error(
                                    "telegram_watchdog_reconnect_failed",
                                    error=str(restart_exc),
                                    exc_info=True,
                                )
                    continue
                if self._client_lifecycle_lock.locked() or self._telegram_restart_lock.locked():
                    continue
                await asyncio.wait_for(self.client.get_me(), timeout=probe_timeout_sec)
                self._telegram_probe_failures = 0
                self._restore_running_state_after_probe()
            except asyncio.CancelledError:
                break
            except Exception as exc:  # noqa: BLE001
                if self._is_auth_key_invalid(exc):
                    await self._recover_telegram_session(reason=str(exc))
                else:
                    self._telegram_probe_failures += 1
                    if self._telegram_probe_failures >= failure_limit:
                        self._mark_transport_degraded(
                            reason="watchdog_probe_failed", error=str(exc)
                        )
                        # P1: auto-reconnect при probe failure
                        import time as _time  # noqa: PLC0415

                        now = _time.monotonic()
                        if now - _last_reconnect_ts >= _reconnect_cooldown_sec:
                            _last_reconnect_ts = now
                            logger.warning(
                                "telegram_watchdog_auto_reconnect_probe_failed",
                                failures=self._telegram_probe_failures,
                                error=repr(exc),
                            )
                            try:
                                await self.restart(reason="watchdog_probe_auto_reconnect")
                                self._telegram_probe_failures = 0
                            except Exception as restart_exc:  # noqa: BLE001
                                logger.error(
                                    "telegram_watchdog_reconnect_failed",
                                    error=str(restart_exc),
                                    exc_info=True,
                                )
                    else:
                        logger.warning(
                            "telegram_watchdog_probe_failed",
                            error=repr(exc),
                            consecutive_failures=self._telegram_probe_failures,
                        )
