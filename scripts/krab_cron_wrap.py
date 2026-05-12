#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Wave 115: cron wrapper для записи истории запусков LaunchAgent'ов.

Запускает заданную команду, замеряет duration + exit code, и пишет строку в
``~/.openclaw/krab_runtime_state/cron_history.db``. SQLite-операции изолированы
в try/except — wrapper НИКОГДА не должен ломать сам cron (любая ошибка БД
логируется на stderr, но exit code пробрасывается из child-процесса).

Usage:
    krab_cron_wrap.py --label foo -- venv/bin/python scripts/foo.py arg1

Schema:
    id, label, start_ts, end_ts, exit_code, duration_sec,
    stdout_size_bytes, stderr_size_bytes

Замечание: stdout/stderr НЕ перехватываются (`subprocess` наследует fd
напрямую) — LaunchAgent уже перенаправляет в StandardOutPath/StandardErrorPath.
Размеры считаются через `os.fstat` на этих файлах ПОСЛЕ запуска, если
переменные KRAB_CRON_STDOUT/KRAB_CRON_STDERR указывают на файлы.
Если их нет — поля = 0.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_DB_PATH = Path(
    os.environ.get(
        "KRAB_CRON_HISTORY_DB",
        str(Path.home() / ".openclaw" / "krab_runtime_state" / "cron_history.db"),
    )
)


def _ensure_schema(db_path: Path) -> sqlite3.Connection | None:
    """Открыть БД и убедиться что schema есть. None при ошибке."""
    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path), timeout=5.0)
    except (sqlite3.Error, OSError) as exc:
        sys.stderr.write(f"krab_cron_wrap: db_open_failed: {type(exc).__name__}: {exc}\n")
        return None
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cron_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                label TEXT NOT NULL,
                start_ts TEXT NOT NULL,
                end_ts TEXT NOT NULL,
                exit_code INTEGER NOT NULL,
                duration_sec REAL NOT NULL,
                stdout_size_bytes INTEGER NOT NULL DEFAULT 0,
                stderr_size_bytes INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_cron_label_ts ON cron_history(label, start_ts DESC)"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_cron_ts ON cron_history(start_ts DESC)")
        conn.commit()
    except sqlite3.Error as exc:
        sys.stderr.write(f"krab_cron_wrap: schema_failed: {type(exc).__name__}: {exc}\n")
        conn.close()
        return None
    return conn


def _file_size(path: str | None) -> int:
    """Размер файла в байтах, 0 если нет/ошибка."""
    if not path:
        return 0
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def record_run(
    label: str,
    start_ts: str,
    end_ts: str,
    exit_code: int,
    duration_sec: float,
    stdout_size: int,
    stderr_size: int,
    db_path: Path = DEFAULT_DB_PATH,
) -> bool:
    """Записать одну строку в cron_history. True если успешно."""
    conn = _ensure_schema(db_path)
    if conn is None:
        return False
    try:
        conn.execute(
            """
            INSERT INTO cron_history
                (label, start_ts, end_ts, exit_code, duration_sec,
                 stdout_size_bytes, stderr_size_bytes)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                label,
                start_ts,
                end_ts,
                int(exit_code),
                float(duration_sec),
                int(stdout_size),
                int(stderr_size),
            ),
        )
        conn.commit()
        return True
    except sqlite3.Error as exc:
        sys.stderr.write(f"krab_cron_wrap: insert_failed: {type(exc).__name__}: {exc}\n")
        return False
    finally:
        try:
            conn.close()
        except sqlite3.Error:
            pass


def _parse_args(argv: list[str]) -> tuple[argparse.Namespace, list[str]]:
    """Парсит --label и возвращает остаток после `--` как команду."""
    parser = argparse.ArgumentParser(
        description="Wave 115 cron wrapper: log every cron invocation to sqlite",
        add_help=True,
    )
    parser.add_argument("--label", required=True, help="logical job label")
    parser.add_argument(
        "--db",
        default=str(DEFAULT_DB_PATH),
        help="override path to cron_history.db",
    )
    # Всё после `--` это команда.
    if "--" not in argv:
        parser.error("missing `--` separator before command")
    sep = argv.index("--")
    wrapper_args = argv[:sep]
    cmd = argv[sep + 1 :]
    if not cmd:
        parser.error("empty command after `--`")
    ns = parser.parse_args(wrapper_args)
    return ns, cmd


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    ns, cmd = _parse_args(argv)

    label = str(ns.label).strip() or "unknown"
    db_path = Path(ns.db)

    start_dt = datetime.now(timezone.utc)
    start_mono = time.monotonic()
    start_ts_iso = start_dt.isoformat()

    # Сам subprocess: НЕ перехватываем stdout/stderr — наследуем fd.
    try:
        completed = subprocess.run(cmd, check=False)
        exit_code = int(completed.returncode)
    except FileNotFoundError as exc:
        sys.stderr.write(f"krab_cron_wrap: cmd_not_found: {exc}\n")
        exit_code = 127
    except OSError as exc:
        sys.stderr.write(f"krab_cron_wrap: cmd_failed: {type(exc).__name__}: {exc}\n")
        exit_code = 126

    end_dt = datetime.now(timezone.utc)
    duration_sec = max(0.0, time.monotonic() - start_mono)
    end_ts_iso = end_dt.isoformat()

    stdout_size = _file_size(os.environ.get("KRAB_CRON_STDOUT"))
    stderr_size = _file_size(os.environ.get("KRAB_CRON_STDERR"))

    record_run(
        label=label,
        start_ts=start_ts_iso,
        end_ts=end_ts_iso,
        exit_code=exit_code,
        duration_sec=duration_sec,
        stdout_size=stdout_size,
        stderr_size=stderr_size,
        db_path=db_path,
    )

    # Prometheus update — best-effort, lazy import чтобы wrapper не зависел
    # от prometheus_client.
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from src.core.metrics.cron_history import record_run as _prom_record

        _prom_record(label=label, exit_code=exit_code, duration_sec=duration_sec)
    except Exception:  # noqa: BLE001
        pass

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
