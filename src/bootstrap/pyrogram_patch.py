# -*- coding: utf-8 -*-
"""
Pyrofork SQLite session hardening — WAL + busy_timeout.

Фикс Sentry PYTHON-FASTAPI-5A/5B/5C: "OperationalError: database is locked" в
``pyrogram.storage.sqlite_storage.update_usernames``. При 4+ параллельных
pyrofork-клиентах (main yung_nagato + traders/coders/analysts/creative) SQLite
roll-back journal блокирует пишущих соседей, даже если файлы .session разные —
пишущие соседи делят одинаковую схему конкурентных транзакций.

Root cause: ``FileStorage.open()`` в pyrofork открывает sqlite3.connect с
``timeout=1`` и без WAL. Под нагрузкой (peer-update + update_usernames)
OperationalError прилетает в фоновые таски.

Почему monkey-patch, а не форк: pyrofork не даёт публичного API задать PRAGMA
при открытии storage, а держать форк ради 4 строк PRAGMA слишком дорого.
Патч идемпотентен (двойной импорт не повредит) и применяется один раз при
bootstrap — до создания первого Client().
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

_PATCH_APPLIED = False


def _execute_pragmas(conn) -> None:
    """Применяет PRAGMA к уже открытому sqlite3-соединению."""
    # journal_mode=WAL — writer не блокирует readers, и наоборот; multi-process
    # safe, persists в заголовке файла.
    # busy_timeout=5000 ms — sqlite сам ждёт лок вместо мгновенного
    # ``database is locked`` (покрывает короткие конкурентные всплески).
    # synchronous=NORMAL — безопасно для WAL, даёт ~2x throughput на writes.
    for pragma in (
        "PRAGMA journal_mode=WAL",
        "PRAGMA busy_timeout=5000",
        "PRAGMA synchronous=NORMAL",
    ):
        try:
            conn.execute(pragma)
        except Exception as exc:  # noqa: BLE001
            # Фейл одной PRAGMA не должен ломать старт: логируем и едем дальше.
            log.warning("pyrogram_pragma_failed", extra={"pragma": pragma, "error": str(exc)})


def apply_pyrogram_sqlite_hardening() -> bool:
    """
    Monkey-patch ``FileStorage.open`` и ``SQLiteStorage.update_usernames``.

    Возвращает True если патч применён (или уже был применён ранее).
    Идемпотентно: повторный вызов no-op.
    """
    global _PATCH_APPLIED
    if _PATCH_APPLIED:
        return True

    try:
        from pyrogram.storage import file_storage as _fs
        from pyrogram.storage import sqlite_storage as _ss
    except Exception as exc:  # noqa: BLE001
        log.warning("pyrogram_patch_import_failed", extra={"error": str(exc)})
        return False

    _orig_open = _fs.FileStorage.open

    async def _patched_open(self, *args, **kwargs):
        await _orig_open(self, *args, **kwargs)
        if getattr(self, "conn", None) is not None:
            _execute_pragmas(self.conn)

    _fs.FileStorage.open = _patched_open

    # Graceful retry-layer поверх update_usernames: под редкой конкуренцией
    # даже с WAL возможен OperationalError (например, schema upgrade на
    # старте). Логируем warning и глотаем — имена подтянутся в следующий раз.
    _orig_update_usernames = _ss.SQLiteStorage.update_usernames

    async def _safe_update_usernames(self, usernames):
        try:
            await _orig_update_usernames(self, usernames)
        except Exception as exc:  # noqa: BLE001 — sqlite3.OperationalError и др.
            msg = str(exc).lower()
            if "database is locked" in msg or "database table is locked" in msg:
                log.warning(
                    "pyrogram_sqlite_locked",
                    extra={"op": "update_usernames", "count": len(usernames or [])},
                )
                return None
            raise

    _ss.SQLiteStorage.update_usernames = _safe_update_usernames

    _PATCH_APPLIED = True
    log.info("pyrogram_sqlite_hardening_applied")
    return True


def is_patch_applied() -> bool:
    """Тестовый хук: проверить, применён ли патч."""
    return _PATCH_APPLIED


def _reset_for_tests() -> None:
    """Только для тестов: сбрасывает флаг, чтобы можно было re-apply в фикстуре."""
    global _PATCH_APPLIED
    _PATCH_APPLIED = False
