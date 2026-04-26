# -*- coding: utf-8 -*-
"""
DB corruption circuit breaker — defence against launchd respawn loop.

Сценарий, который этот модуль предотвращает (Sentry incident 26.04.2026):
- `~/.openclaw/krab_memory/archive.db` или `data/sessions/kraab.session`
  получают повреждение страницы (sqlite "database disk image is malformed",
  "disk I/O error", "file is not a database").
- Krab падает на boot до того, как успевает подняться userbot.
- launchd KeepAlive=true перезапускает процесс → бесконечный loop
  (322 fatal_error events за 24h в incident-окне).

Решение:
1. Pre-flight `PRAGMA integrity_check` на known DB-файлах.
2. При обнаружении corruption — переименовать файл в
   `<path>.corrupt-<unix_ts>` (quarantine), отправить Sentry event с тегом
   `db_corruption=true` и вернуть отчёт.
3. Bootstrap решает, что делать (для archive.db можно регенерировать,
   для kraab.session — корректно exit с явным сообщением, чтобы owner
   re-authorized session, а не launchd респавнил мусор).

Public API:
- `KNOWN_DB_PATHS` — список (path, kind) для preflight.
- `is_corruption_error(exc) -> bool` — детектор по тексту исключения.
- `quarantine_db_file(path) -> str` — переименование в `.corrupt-<ts>`.
- `integrity_check(path) -> tuple[bool, str]` — `PRAGMA integrity_check`.
- `preflight_known_dbs() -> list[dict]` — пробежать all known DBs,
  quarantine corrupt + report Sentry.
- `report_corruption_to_sentry(...)` — best-effort tagged event.
"""

from __future__ import annotations

import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import structlog

logger = structlog.get_logger(__name__)


# Markers, по которым sqlite/SQLite python-binding сообщают о corruption.
# Все сравниваются case-insensitive подстрокой по str(exc).
_CORRUPTION_MARKERS: tuple[str, ...] = (
    "database disk image is malformed",
    "disk i/o error",
    "file is not a database",
    "file is encrypted or is not a database",
    "database is locked",  # включён условно — НЕ trigger-it автоматически
    "no such table: sqlite_master",
    "malformed database schema",
)

# Реальные corruption-маркеры (обязательная quarantine).
# `database is locked` НЕ здесь — он transient, не corruption.
_HARD_CORRUPTION_MARKERS: tuple[str, ...] = (
    "database disk image is malformed",
    "disk i/o error",
    "file is not a database",
    "file is encrypted or is not a database",
    "malformed database schema",
)


@dataclass(frozen=True)
class KnownDb:
    """Описание DB-файла, который проверяется на boot."""

    path: Path
    kind: str  # "session" | "archive" | "swarm-session" | ...
    critical: bool  # True → corruption тут блокирует boot (session)


def _known_db_paths() -> list[KnownDb]:
    """Список known DB файлов. Lazy — чтобы тесты могли monkeypatch HOME."""
    home = Path.home()
    base = Path(__file__).resolve().parents[2]
    sessions_dir = base / "data" / "sessions"
    session_name = os.getenv("TELEGRAM_SESSION_NAME", "kraab")
    return [
        KnownDb(
            path=sessions_dir / f"{session_name}.session",
            kind="session",
            critical=True,
        ),
        KnownDb(
            path=home / ".openclaw" / "krab_memory" / "archive.db",
            kind="archive",
            critical=False,
        ),
    ]


def is_corruption_error(exc: BaseException | str) -> bool:
    """True, если исключение/строка похожи на DB corruption.

    Принимает либо exception, либо уже извлечённый message — удобно для
    проверки PRAGMA-результата (не исключение, а строка).
    """
    text = str(exc).lower()
    return any(marker in text for marker in _HARD_CORRUPTION_MARKERS)


def quarantine_db_file(path: Path) -> str:
    """
    Переименовывает повреждённый DB-файл в `<path>.corrupt-<unix_ts>`.

    Возвращает строку нового пути. Идемпотентен: если path не существует,
    возвращает пустую строку.

    Также пытается переместить sidecar файлы (`-wal`, `-shm`, `-journal`).
    """
    if not path.exists():
        return ""
    ts = int(time.time())
    new_path = path.with_name(f"{path.name}.corrupt-{ts}")
    try:
        path.rename(new_path)
    except OSError as exc:
        logger.error(
            "db_quarantine_rename_failed",
            path=str(path),
            target=str(new_path),
            error=str(exc),
        )
        return ""
    # sidecar файлы SQLite (WAL/SHM/journal) тоже двигаем — иначе SQLite
    # может попытаться "восстановить" базу из старого WAL поверх нового.
    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = path.with_name(path.name + suffix)
        if sidecar.exists():
            try:
                sidecar.rename(sidecar.with_name(f"{sidecar.name}.corrupt-{ts}"))
            except OSError as exc:  # pragma: no cover — best-effort
                logger.warning(
                    "db_quarantine_sidecar_rename_failed",
                    path=str(sidecar),
                    error=str(exc),
                )
    logger.error(
        "db_corruption_quarantined",
        original=str(path),
        quarantine=str(new_path),
        unix_ts=ts,
    )
    return str(new_path)


def integrity_check(path: Path, *, timeout_sec: float = 5.0) -> tuple[bool, str]:
    """
    Запускает `PRAGMA integrity_check` на DB.

    Returns:
        (ok, detail) — ok=True если результат "ok"; detail = строка/сообщение.
        Если файл не существует, возвращает (True, "missing") — это НЕ ошибка
        для optional-DB (archive.db создастся при первом write).
    """
    if not path.exists():
        return True, "missing"
    try:
        # uri=True + mode=ro чтобы НЕ создавать новый файл и НЕ писать
        # в WAL во время проверки.
        uri = f"file:{path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=timeout_sec)
        try:
            cur = conn.execute("PRAGMA integrity_check;")
            row = cur.fetchone()
            result = (row[0] if row else "").strip().lower()
        finally:
            conn.close()
    except sqlite3.DatabaseError as exc:
        # OperationalError тоже наследник DatabaseError — здесь ловим оба.
        msg = str(exc)
        return False, msg
    except Exception as exc:  # noqa: BLE001 — не падаем на нештатном ввод/вывод
        return True, f"check_skipped: {exc}"
    if result == "ok":
        return True, "ok"
    return False, result or "unknown"


def report_corruption_to_sentry(
    *,
    path: str,
    kind: str,
    detail: str,
    quarantine_path: str,
) -> None:
    """Best-effort Sentry event с тегом db_corruption=true.

    Sentry init может ещё не быть выполнен (corruption обнаружена ДО
    init_sentry). В этом случае просто логируем — не падаем.
    """
    try:
        import sentry_sdk

        # sentry-sdk 2.x: new_scope() заменил deprecated push_scope().
        scope_cm = getattr(sentry_sdk, "new_scope", None) or sentry_sdk.push_scope
        with scope_cm() as scope:
            scope.set_tag("db_corruption", "true")
            scope.set_tag("db_kind", kind)
            scope.set_extra("db_path", path)
            scope.set_extra("quarantine_path", quarantine_path)
            scope.set_extra("detail", detail)
            sentry_sdk.capture_message(
                f"db_corruption_detected: {kind}",
                level="error",
            )
    except Exception as exc:  # noqa: BLE001
        logger.debug("sentry_corruption_report_skipped", error=str(exc))


def preflight_known_dbs(
    known_dbs: Iterable[KnownDb] | None = None,
) -> list[dict]:
    """
    Pre-flight проверка known DB-файлов.

    Returns:
        Список report-словарей: {path, kind, critical, ok, detail,
        quarantined, quarantine_path}.

    Стратегия:
    - integrity_check() для каждой DB;
    - если NOT ok И detail матчится corruption — quarantine + Sentry;
    - non-critical → bootstrap продолжает (DB регенерируется);
    - critical (session) → bootstrap должен exit, чтобы НЕ зациклить
      launchd на пустой/битой сессии (owner re-auth needed).
    """
    if known_dbs is None:
        known_dbs = _known_db_paths()
    reports: list[dict] = []
    for entry in known_dbs:
        ok, detail = integrity_check(entry.path)
        report = {
            "path": str(entry.path),
            "kind": entry.kind,
            "critical": entry.critical,
            "ok": ok,
            "detail": detail,
            "quarantined": False,
            "quarantine_path": "",
        }
        if not ok and is_corruption_error(detail):
            quarantine_path = quarantine_db_file(entry.path)
            report["quarantined"] = True
            report["quarantine_path"] = quarantine_path
            report_corruption_to_sentry(
                path=str(entry.path),
                kind=entry.kind,
                detail=detail,
                quarantine_path=quarantine_path,
            )
        elif not ok:
            # integrity_check вернул не-ok, но это не corruption-маркер
            # (например, locked) — просто логируем, не quarantine.
            logger.warning(
                "db_integrity_check_non_ok",
                path=str(entry.path),
                kind=entry.kind,
                detail=detail,
            )
        reports.append(report)
    return reports


__all__ = [
    "KnownDb",
    "is_corruption_error",
    "quarantine_db_file",
    "integrity_check",
    "preflight_known_dbs",
    "report_corruption_to_sentry",
]
