# -*- coding: utf-8 -*-
"""Wave 24-D: Pre-exit WAL checkpoint для kraab.session.

Вызывается ДО закрытия pyrofork client'а в shutdown sequence.
Форсирует PRAGMA wal_checkpoint(TRUNCATE) — гарантирует что WAL flushed
на диск и -wal файл удалён, чтобы next start не получил stale WAL.

Без этого hook'а pyrofork оставляет -wal файл в неопределённом состоянии,
что приводит к 'disk I/O error' / 'malformed' на следующем open.

Отличие от checkpoint_session_wal в session.py:
  - Используется timeout=10.0 (а не 2.0), т.к. вызывается до stop() —
    pyrofork ещё может держать lock несколько секунд;
  - Структурированный возврат dict с полем ok;
  - Предназначен для pre-exit интеграции (SIGTERM handler / atexit).
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from ..core.logger import get_logger

logger = get_logger(__name__)

# Wave 24-D: env gate — по умолчанию включён (pre-exit hook всегда безопасен).
# Отключение через KRAB_WAL_PRE_EXIT_ENABLED=0 для совместимости в тестах.
_WAL_PRE_EXIT_ENABLED = os.environ.get("KRAB_WAL_PRE_EXIT_ENABLED", "1").strip() not in {
    "0",
    "false",
    "no",
}


def force_wal_checkpoint(session_path: Path, *, timeout_sec: float = 10.0) -> dict:
    """Force WAL checkpoint TRUNCATE на session_path.

    Контракт:
    - Вызывается ДО или ПОСЛЕ client.stop() — работает в обоих случаях.
    - Идемпотентен: если файла нет — silent skip.
    - Никогда не бросает наружу: ошибки → warning лог, возвращает ok=False.
    - PRAGMA wal_checkpoint(TRUNCATE): переписывает WAL в основной файл
      и удаляет *.session-wal sidecar. Следующий open не читает stale WAL.

    Args:
        session_path: абсолютный путь к *.session sqlite-файлу.
        timeout_sec: sqlite3.connect timeout (default 10.0s — достаточно
            для случая, когда pyrofork ещё держит lock после stop()).

    Returns:
        dict с полями:
          ok (bool): True если checkpoint завершён без busy readers;
          busy_count (int|None): 0 = успех, >0 = был активный writer;
          log_count (int|None): фреймов в WAL до checkpoint;
          error (str|None): текст ошибки при неуспехе.
    """
    if not _WAL_PRE_EXIT_ENABLED:
        return {"ok": False, "error": "disabled_by_env", "busy_count": None, "log_count": None}

    # Нормализуем path
    session_path = Path(session_path)

    if not session_path.exists():
        return {"ok": False, "error": "session_not_exists", "busy_count": None, "log_count": None}

    try:
        # Открываем с большим timeout — pyrofork ещё может держать lock
        conn = sqlite3.connect(str(session_path), timeout=timeout_sec)
        try:
            # TRUNCATE = перенести WAL в основной файл + удалить -wal sidecar
            cur = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            row = cur.fetchone()
            # row = (busy, log_count, checkpointed) согласно SQLite docs
            busy = row[0] if row else None
            log_count = row[1] if row else None
            checkpointed = row[2] if row else None
            logger.info(
                "wal_checkpoint_pre_exit_done",
                session=str(session_path),
                busy=busy,
                log=log_count,
                checkpointed=checkpointed,
            )
            return {
                "ok": busy == 0,
                "busy_count": busy,
                "log_count": log_count,
                "error": None,
            }
        finally:
            conn.close()
    except sqlite3.OperationalError as e:
        logger.warning(
            "wal_checkpoint_pre_exit_failed",
            session=str(session_path),
            error=str(e),
        )
        return {"ok": False, "error": str(e), "busy_count": None, "log_count": None}
    except Exception as e:  # noqa: BLE001 — никогда не бросаем наружу
        logger.warning(
            "wal_checkpoint_pre_exit_unexpected",
            session=str(session_path),
            error=str(e),
        )
        return {"ok": False, "error": str(e), "busy_count": None, "log_count": None}
