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


def checkpoint_session_wal(session_path: str | Path) -> dict | None:
    """
    Best-effort `PRAGMA wal_checkpoint(TRUNCATE)` для Pyrogram .session файла.

    Зачем: при graceful shutdown Pyrogram закрывает sqlite без явного truncate,
    оставляя многомегабайтный *.session-wal sidecar. На многих рестартах WAL
    растёт неограниченно (см. session 32: 4MB → ручной truncate).

    Контракт:
    - Вызывается ПОСЛЕ `client.stop()` (storage уже закрыта, файл существует).
    - Идемпотентен: если файла нет (первый запуск) — silent skip, возвращает None.
    - Никогда не бросает наружу: все ошибки логируются как warning.
    - Возвращает dict с `frames_checkpointed` / `pages_in_wal` при успехе.

    PRAGMA wal_checkpoint(TRUNCATE) семантика:
        result = (busy, log_frames, checkpointed_frames)
        — busy: 0 если успешно, 1 если был writer
        — log_frames: всего фреймов в WAL до checkpoint
        — checkpointed_frames: сколько перенесено в основной файл
    """
    path = Path(session_path)
    if not path.exists():
        return None
    try:
        with sqlite3.connect(str(path), timeout=2.0) as conn:
            cur = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            row = cur.fetchone() or (None, None, None)
            busy, log_frames, checkpointed = row
            payload = {
                "session_path": str(path),
                "frames_checkpointed": checkpointed,
                "pages_in_wal": log_frames,
                "busy": busy,
            }
            logger.info("pyrogram_wal_checkpoint_truncated", **payload)
            return payload
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "pyrogram_wal_checkpoint_failed",
            session_path=str(path),
            error=str(exc),
            non_fatal=True,
        )
        return None


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

    def _main_session_integrity_preflight(self) -> bool:
        """
        Pre-flight integrity check + auto-recovery для main kraab.session.

        Симметричен swarm preflight (`_start_swarm_team_clients`, ~line 3300
        в `userbot_bridge.py`). Закрывает асимметрию: swarm clients защищены,
        а main session раньше шёл прямо в `Client(...)` — corruption всплывала
        только при первом read и могла вешать процесс.

        Поток:
        1. Если файл отсутствует → return True (фрэш auth flow handled выше).
        2. Если backup `.bak-corrupt-*` моложе 1h существует → ТОЛЬКО integrity
           check (skip recovery loop, чтобы не зацикливаться).
        3. integrity_check на read-only connection.
        4. На corruption → backup + WAL/SHM cleanup + `.recover` → integrity
           recheck → atomic replace.
        5. На irrecoverable → DBCorruptionError (runtime.py выходит exit 78).

        Returns:
            True если session готова к открытию Pyrogram client'ом.

        Raises:
            DBCorruptionError — recovery провалилась, либо повторная попытка
            (idempotency guard).
        """
        from ..bootstrap.db_corruption_guard import (
            DBCorruptionError,
            attempt_session_recovery,
            has_recent_recovery_backup,
            integrity_check,
            is_corruption_error,
            report_corruption_to_sentry,
        )

        sess_path = self._primary_session_file()
        if not sess_path.exists():
            # Fresh session — nothing to check. Pyrogram сам создаст файл
            # при первом start (либо запросит phone-code в interactive flow).
            return True

        ok, detail = integrity_check(sess_path)
        if ok:
            logger.info(
                "main_session_integrity_ok",
                file=str(sess_path),
                detail=detail,
            )
            return True

        # Non-ok. Различаем: реальная corruption vs transient locked / disk
        # I/O. Только corruption-маркеры триггерят recovery.
        if not is_corruption_error(detail):
            # Transient (locked / disk i/o). Не recover — пускай Pyrogram
            # сам отретраит на реальной open. Это согласовано с поведением
            # bootstrap'а runtime.py.
            logger.warning(
                "main_session_integrity_non_corruption",
                file=str(sess_path),
                detail=detail,
            )
            return True

        logger.error(
            "main_session_integrity_failed",
            file=str(sess_path),
            detail=detail,
        )

        # Idempotency guard: если recovery уже была в последний час и мы опять
        # видим corruption — повторный .recover вряд ли поможет. Fail loudly.
        if has_recent_recovery_backup(sess_path, within_seconds=3600):
            logger.error(
                "main_session_recovery_skipped_recent_backup",
                file=str(sess_path),
                detail=detail,
            )
            report_corruption_to_sentry(
                path=str(sess_path),
                kind="session",
                detail=detail,
                quarantine_path="",
            )
            raise DBCorruptionError(
                f"Main session {sess_path} corrupt and recent recovery did not help: {detail}"
            )

        # Auto-recovery (sqlite3 .recover).
        recovery = attempt_session_recovery(sess_path, timeout_sec=30.0)
        if recovery.get("recovered"):
            logger.info(
                "main_session_recovered_auto",
                file=str(sess_path),
                backup_path=recovery.get("backup_path", ""),
                peer_count=recovery.get("peer_count"),
                username_count=recovery.get("username_count"),
                sessions_count=recovery.get("sessions_count"),
            )
            return True

        logger.error(
            "main_session_recovery_failed",
            file=str(sess_path),
            detail=recovery.get("detail", ""),
            backup_path=recovery.get("backup_path", ""),
        )
        report_corruption_to_sentry(
            path=str(sess_path),
            kind="session",
            detail=recovery.get("detail", "") or detail,
            quarantine_path=recovery.get("backup_path", ""),
        )
        raise DBCorruptionError(
            f"Main session {sess_path} corrupt; .recover failed: {recovery.get('detail', '')}"
        )

    def _recreate_client(self) -> None:
        """
        Полностью пересоздает экземпляр Pyrogram Client и регистрирует хендлеры заново.
        Нужен для recovery после протухшей/битой сессии.

        Перед созданием Pyrogram Client проводит integrity preflight + auto-recovery
        — симметрично swarm clients (см. `_start_swarm_team_clients`).
        """
        # Гарантируем, что session workdir существует ДО любого open()
        # (integrity_check открывает в read-only mode, но parent dir нужен
        # для будущих recover-сайдкаров).
        self._session_workdir.mkdir(parents=True, exist_ok=True)
        # Wave 5: integrity-gate. Поднимет DBCorruptionError если auto-recovery
        # не помог — runtime.py поймает sqlite3.DatabaseError-родственника
        # и выйдет с DB_CORRUPTION_EXIT_CODE.
        try:
            self._main_session_integrity_preflight()
        except Exception as exc:  # noqa: BLE001
            # Re-raise после явного логирования. Класс — наследник RuntimeError,
            # bootstrap его не залапает как DatabaseError, поэтому добавляем
            # стабильный лог здесь.
            logger.error(
                "main_session_preflight_aborted_boot",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            raise
        self.client = Client(
            config.TELEGRAM_SESSION_NAME,
            api_id=config.TELEGRAM_API_ID,
            api_hash=config.TELEGRAM_API_HASH,
            workdir=str(self._session_workdir),
        )
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
                self._arm_storage_shutdown_guard()
                await self._cancel_client_restart_tasks()
                await self.client.stop()
                # Session 33 P1: TRUNCATE WAL чтобы sidecar не рос неограниченно.
                # Best-effort, не блокирует shutdown.
                try:
                    checkpoint_session_wal(self._primary_session_file())
                except Exception as wal_exc:  # noqa: BLE001
                    logger.warning(
                        "pyrogram_wal_checkpoint_unexpected_failure",
                        error=str(wal_exc),
                        non_fatal=True,
                    )
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

    def _arm_storage_shutdown_guard(self) -> None:
        """
        Ставит guard на pyrogram storage основного клиента (`self.client`).

        Тонкая обёртка над статическим helper'ом — он же используется для
        per-team swarm clients (см. `_arm_storage_shutdown_guard_for_client`).
        """
        if not self.client:
            return
        SessionMixin._arm_storage_shutdown_guard_for_client(self.client)

    @staticmethod
    def _arm_storage_shutdown_guard_for_client(client) -> None:
        """
        Ставит guard на pyrogram storage произвольного клиента.

        Почему это нужно:
        - Pyrogram-задачи Session.restart()/dispatcher могут долететь до
          storage уже после `await client.stop()` и упасть на
          `sqlite3.ProgrammingError: Cannot operate on a closed database`;
        - такие падения шумят в Sentry (PYTHON-FASTAPI-1, ~130 events/сутки)
          и не имеют actionable runtime-следствий — race на shutdown;
        - guard помечает storage как closed и подменяет `_get` / `update_peers`
          на безопасные no-op'ы, которые возвращают пустые результаты вместо
          обращения к закрытому sqlite connection.

        Применяется как к основному userbot-клиенту, так и к per-team swarm
        clients (4× rate ⇒ ~10 events/24h без guard'а).
        Идемпотентно: повторный вызов только переставляет `_krab_storage_closed`.
        """
        if client is None:
            return
        storage = getattr(client, "storage", None)
        if storage is None:
            return
        if getattr(storage, "_krab_storage_guard_installed", False):
            setattr(storage, "_krab_storage_closed", True)
            return

        setattr(storage, "_krab_storage_closed", True)

        original_get = getattr(storage, "_get", None)
        original_update_peers = getattr(storage, "update_peers", None)

        if callable(original_get):

            def _guarded_get(*args, **kwargs):
                if getattr(storage, "_krab_storage_closed", False):
                    logger.debug("telegram_storage_get_suppressed_after_close")
                    return None
                try:
                    return original_get(*args, **kwargs)
                except sqlite3.ProgrammingError as exc:
                    if "closed database" in str(exc).lower():
                        logger.debug(
                            "telegram_storage_get_swallowed_closed_db",
                            error=str(exc),
                        )
                        return None
                    raise

            setattr(storage, "_krab_original_get", original_get)
            setattr(storage, "_get", _guarded_get)

        if callable(original_update_peers):

            async def _guarded_update_peers(peers, *args, **kwargs):
                if getattr(storage, "_krab_storage_closed", False):
                    logger.debug("telegram_storage_update_peers_suppressed_after_close")
                    return None
                try:
                    return await original_update_peers(peers, *args, **kwargs)
                except sqlite3.ProgrammingError as exc:
                    if "closed database" in str(exc).lower():
                        logger.debug(
                            "telegram_storage_update_peers_swallowed_closed_db",
                            error=str(exc),
                        )
                        return None
                    raise

            setattr(storage, "_krab_original_update_peers", original_update_peers)
            setattr(storage, "update_peers", _guarded_update_peers)

        setattr(storage, "_krab_storage_guard_installed", True)

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
