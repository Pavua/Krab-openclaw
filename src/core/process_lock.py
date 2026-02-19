# -*- coding: utf-8 -*-
"""
Модуль межпроцессной блокировки ядра Krab.

Зачем нужен:
- предотвращает одновременный запуск двух экземпляров `src.main`;
- исключает гонки при старте и дубли ответов в Telegram;
- обеспечивает единый источник правды по активному PID.

Связь с системой:
- используется в `src/main.py` как ранний guard перед `app.run(...)`.
"""

from __future__ import annotations

import fcntl
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional


@dataclass
class LockHolderInfo:
    """Информация о процессе, который уже держит блокировку."""

    pid: Optional[int]
    started_at: Optional[str]
    lock_path: str
    raw_payload: str


class DuplicateInstanceError(RuntimeError):
    """Исключение: второй экземпляр ядра пытается стартовать при активном первом."""

    def __init__(self, info: LockHolderInfo) -> None:
        self.info = info
        self.holder_pid = info.pid
        self.holder_started_at = info.started_at
        message = (
            "Обнаружен уже запущенный экземпляр Krab "
            f"(pid={info.pid}, started_at={info.started_at}, lock={info.lock_path})"
        )
        super().__init__(message)


class SingleInstanceProcessLock:
    """Атомарная блокировка процесса через flock + lock-файл."""

    def __init__(self, lock_path: str, pid_path: Optional[str] = None) -> None:
        self.lock_path = os.path.abspath(lock_path)
        self.pid_path = os.path.abspath(pid_path) if pid_path else None
        self._fd: Optional[int] = None

    def acquire(self) -> None:
        """Захватывает lock; при конфликте поднимает DuplicateInstanceError."""
        if self._fd is not None:
            return

        os.makedirs(os.path.dirname(self.lock_path), exist_ok=True)
        fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            info = self._read_holder_info(fd)
            os.close(fd)
            raise DuplicateInstanceError(info) from None

        self._fd = fd
        self._write_holder_info()
        self._write_pid_file()

    def release(self) -> None:
        """Освобождает lock и удаляет PID-файл текущего процесса."""
        if self._fd is None:
            return

        try:
            self._remove_own_pid_file()
            os.ftruncate(self._fd, 0)
            os.lseek(self._fd, 0, os.SEEK_SET)
        finally:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
            os.close(self._fd)
            self._fd = None

    def __enter__(self) -> "SingleInstanceProcessLock":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()

    def _write_holder_info(self) -> None:
        if self._fd is None:
            return
        payload = {
            "pid": os.getpid(),
            "started_at": datetime.now(timezone.utc).isoformat(),
        }
        encoded = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
        os.ftruncate(self._fd, 0)
        os.lseek(self._fd, 0, os.SEEK_SET)
        os.write(self._fd, encoded)
        os.fsync(self._fd)

    def _write_pid_file(self) -> None:
        if not self.pid_path:
            return
        os.makedirs(os.path.dirname(self.pid_path), exist_ok=True)
        with open(self.pid_path, "w", encoding="utf-8") as handle:
            handle.write(str(os.getpid()))

    def _remove_own_pid_file(self) -> None:
        if not self.pid_path or not os.path.exists(self.pid_path):
            return
        try:
            with open(self.pid_path, "r", encoding="utf-8") as handle:
                current = handle.read().strip()
            if current == str(os.getpid()):
                os.remove(self.pid_path)
        except Exception:
            # Безопасный режим: если не удалось удалить PID-файл, не ломаем shutdown.
            pass

    def _read_holder_info(self, fd: int) -> LockHolderInfo:
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            raw_payload = os.read(fd, 8192).decode("utf-8", errors="replace").strip()
        except Exception:
            raw_payload = ""

        pid: Optional[int] = None
        started_at: Optional[str] = None
        if raw_payload:
            try:
                parsed = json.loads(raw_payload)
                if isinstance(parsed, dict):
                    raw_pid = parsed.get("pid")
                    if isinstance(raw_pid, int):
                        pid = raw_pid
                    elif isinstance(raw_pid, str) and raw_pid.strip().isdigit():
                        pid = int(raw_pid.strip())
                    raw_started_at = parsed.get("started_at")
                    if isinstance(raw_started_at, str) and raw_started_at.strip():
                        started_at = raw_started_at.strip()
            except Exception:
                # Оставляем raw_payload в диагностике, даже если JSON повреждён.
                pass

        return LockHolderInfo(
            pid=pid,
            started_at=started_at,
            lock_path=self.lock_path,
            raw_payload=raw_payload,
        )
