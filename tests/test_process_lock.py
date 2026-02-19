# -*- coding: utf-8 -*-
"""
Тесты межпроцессной блокировки ядра Krab.

Проверяем два критичных сценария:
1) второй процесс не может захватить lock, пока первый активен;
2) после release lock становится доступным.
"""

import os
import subprocess
import sys
import textwrap
from pathlib import Path

from src.core.process_lock import SingleInstanceProcessLock


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _run_lock_probe(lock_path: Path) -> subprocess.CompletedProcess:
    script = textwrap.dedent(
        f"""
        import sys
        sys.path.insert(0, {str(_project_root())!r})
        from src.core.process_lock import SingleInstanceProcessLock, DuplicateInstanceError

        lock = SingleInstanceProcessLock({str(lock_path)!r})
        try:
            lock.acquire()
        except DuplicateInstanceError as exc:
            print("duplicate", exc.holder_pid)
            sys.exit(2)
        else:
            print("acquired")
            lock.release()
            sys.exit(0)
        """
    )
    return subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


def test_process_lock_blocks_second_process(tmp_path: Path) -> None:
    lock_path = tmp_path / "krab_core.lock"
    pid_path = tmp_path / "krab_core.pid"
    lock = SingleInstanceProcessLock(str(lock_path), str(pid_path))
    lock.acquire()
    try:
        assert pid_path.exists(), "PID-файл должен создаваться при захвате lock"
        assert pid_path.read_text(encoding="utf-8").strip() == str(os.getpid())

        result = _run_lock_probe(lock_path)
        merged = f"{result.stdout}\n{result.stderr}"
        assert result.returncode == 2, merged
        assert "duplicate" in merged.lower()
    finally:
        lock.release()


def test_process_lock_release_allows_next_process(tmp_path: Path) -> None:
    lock_path = tmp_path / "krab_core.lock"
    pid_path = tmp_path / "krab_core.pid"
    lock = SingleInstanceProcessLock(str(lock_path), str(pid_path))
    lock.acquire()
    lock.release()

    result = _run_lock_probe(lock_path)
    merged = f"{result.stdout}\n{result.stderr}"
    assert result.returncode == 0, merged
    assert "acquired" in merged.lower()
