#!/usr/bin/env python3
"""Wave 118: session-specific backup integrity check.

Дополняет Wave 105 (general backup verify) проверкой именно Pyrofork
session backups (`data/sessions/*.session*.bak.*`). Цель — убедиться,
что auth_key и peer cache читаемы, чтобы emergency restore был
возможен.

Pyrofork-схема: auth_key хранится в таблице `sessions` (не `auth_keys`,
как в Pyrogram оригинале), 1 строка с актуальным dc_id/auth_key. Peers
лежат в `peers` (FK к usernames). Чтение PEER count даёт insight о
"свежести" backup'а — старые backups с малым числом peers менее
полезны при восстановлении.

Output JSON:
{
    "timestamp": "...",
    "total_session_backups": N,
    "valid": N,
    "corrupt": K,
    "peer_counts": {"<path>": int|None},
    "files": [{"path", "auth_ok", "peer_count", "reason"}]
}
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import sqlite3
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path

# Pyrofork session db обычно < 5 MB, full SHA256 ok
SHA256_CHUNK = 1024 * 1024
SQLITE_TIMEOUT_SEC = 15.0

# WAL/SHM sidecars не отдельные backup'ы — это shadow для основного .bak.*
# При check пропускаем (не валидные standalone sqlite).
SIDECAR_SUFFIXES = ("-shm", "-wal", "-journal")


@dataclass
class SessionBackupRecord:
    """Запись про один session backup."""

    path: str
    size_bytes: int
    sha256: str
    auth_ok: bool  # True если `sessions` таблица читается и содержит ≥1 row
    peer_count: int | None  # None при ошибке чтения
    reason: str = ""  # пустая строка если valid


@dataclass
class SessionBackupReport:
    """Итоговый отчёт прохода."""

    timestamp: str
    total_session_backups: int
    valid: int
    corrupt: int
    peer_counts: dict[str, int | None] = field(default_factory=dict)
    files: list[SessionBackupRecord] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "total_session_backups": self.total_session_backups,
            "valid": self.valid,
            "corrupt": self.corrupt,
            "peer_counts": dict(self.peer_counts),
            "files": [asdict(f) for f in self.files],
        }


def compute_sha256(path: Path) -> str:
    """Streaming SHA256 — пустая строка при ошибке чтения."""
    try:
        h = hashlib.sha256()
        with path.open("rb") as f:
            while True:
                chunk = f.read(SHA256_CHUNK)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return ""


def _is_sidecar(path: Path) -> bool:
    """Side-car (.shm/.wal/.journal) — не основной backup."""
    name = path.name
    return any(name.endswith(suf) for suf in SIDECAR_SUFFIXES)


def discover_session_backups(sessions_dir: Path) -> list[Path]:
    """Walk `<repo>/data/sessions/*.session*.bak.*`, исключая sidecars."""
    if not sessions_dir.exists():
        return []
    found: list[Path] = []
    try:
        for match in sessions_dir.glob("*.session*.bak.*"):
            if not match.is_file():
                continue
            if _is_sidecar(match):
                continue
            found.append(match.resolve())
    except OSError:
        return []
    return sorted(found)


def _ro_connect(path: Path) -> sqlite3.Connection:
    """Read-only URI connection."""
    uri = f"file:{path}?mode=ro"
    return sqlite3.connect(uri, uri=True, timeout=SQLITE_TIMEOUT_SEC)


def check_auth_key(path: Path) -> tuple[bool, str]:
    """Проверяет, что в backup читается ≥1 row из `sessions` (auth key intact).

    Returns (ok, reason). reason пустой при success.
    """
    if not path.exists():
        return (False, "file_missing")
    try:
        conn = _ro_connect(path)
    except sqlite3.Error as exc:
        return (False, f"connect_failed:{type(exc).__name__}")
    try:
        cur = conn.execute("SELECT COUNT(*) FROM sessions")
        row = cur.fetchone()
        count = int(row[0]) if row else 0
        if count < 1:
            return (False, "sessions_empty")
        return (True, "")
    except sqlite3.Error as exc:
        return (False, f"sessions_read_failed:{type(exc).__name__}")
    finally:
        try:
            conn.close()
        except sqlite3.Error:
            pass


def read_peer_count(path: Path) -> int | None:
    """COUNT(*) FROM peers — recovery insight. None при ошибке."""
    if not path.exists():
        return None
    try:
        conn = _ro_connect(path)
    except sqlite3.Error:
        return None
    try:
        cur = conn.execute("SELECT COUNT(*) FROM peers")
        row = cur.fetchone()
        return int(row[0]) if row else 0
    except sqlite3.Error:
        return None
    finally:
        try:
            conn.close()
        except sqlite3.Error:
            pass


def check_backup(path: Path) -> SessionBackupRecord:
    """Полная проверка одного backup'а."""
    try:
        size = path.stat().st_size
    except OSError:
        return SessionBackupRecord(
            path=str(path),
            size_bytes=0,
            sha256="",
            auth_ok=False,
            peer_count=None,
            reason="stat_failed",
        )

    sha = compute_sha256(path)
    auth_ok, reason = check_auth_key(path)
    peer_count = read_peer_count(path)
    return SessionBackupRecord(
        path=str(path),
        size_bytes=size,
        sha256=sha,
        auth_ok=auth_ok,
        peer_count=peer_count,
        reason=reason if not auth_ok else "",
    )


def run_check(sessions_dir: Path) -> SessionBackupReport:
    """Главный entrypoint — discover + check."""
    paths = discover_session_backups(sessions_dir)
    records: list[SessionBackupRecord] = []
    peer_counts: dict[str, int | None] = {}
    valid = 0
    corrupt = 0

    for p in paths:
        rec = check_backup(p)
        records.append(rec)
        peer_counts[rec.path] = rec.peer_count
        if rec.auth_ok:
            valid += 1
        else:
            corrupt += 1

    return SessionBackupReport(
        timestamp=_dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat(),
        total_session_backups=len(records),
        valid=valid,
        corrupt=corrupt,
        peer_counts=peer_counts,
        files=records,
    )


def publish_metrics(report: SessionBackupReport, panel_url: str) -> bool:
    """Публикует session_backup gauge метрики (best-effort)."""
    payloads = [
        {
            "name": "krab_session_backup_valid_count",
            "type": "gauge",
            "values": {"_": report.valid},
        },
        {
            "name": "krab_session_backup_corrupt_count",
            "type": "gauge",
            "values": {"_": report.corrupt},
        },
    ]
    ok = True
    for payload in payloads:
        try:
            req = urllib.request.Request(
                f"{panel_url.rstrip('/')}/api/metric",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                if not (200 <= resp.status < 300):
                    ok = False
        except (urllib.error.URLError, OSError, TimeoutError):
            ok = False
    return ok


def _default_sessions_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "data" / "sessions"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Krab session backup check (Wave 118)")
    parser.add_argument(
        "--sessions-dir",
        default=os.getenv("KRAB_SESSIONS_DIR", str(_default_sessions_dir())),
    )
    parser.add_argument("--no-publish", action="store_true")
    parser.add_argument("--panel-url", default=os.getenv("KRAB_PANEL_URL", "http://127.0.0.1:8080"))
    args = parser.parse_args(argv)

    sessions_dir = Path(args.sessions_dir).resolve()
    report = run_check(sessions_dir)
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))

    if not args.no_publish:
        publish_metrics(report, args.panel_url)

    # Non-zero exit при corruption → видно в StandardErrorPath
    return 1 if report.corrupt > 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
