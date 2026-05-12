#!/usr/bin/env python3
"""Wave 105: backup integrity verification — SHA256 + sqlite PRAGMA integrity_check.

Запускается weekly через LaunchAgent ai.krab.backup-verify (Sun 05:00, между
nightly maintenance и secrets audit 09:00).

Цели:
1. Walk известных backup путей:
   - data/sessions/*.session*.bak.* (Pyrofork session backups, Wave 18-A)
   - ~/.openclaw/krab_runtime_state/backups/* (state snapshots, Wave 49-F)
   - ~/.openclaw/krab_runtime_state/*.bak* (in-place backups)
   - data/memory/archive.db.bak* (memory archive backups)
2. Для каждого файла: stat (size, mtime) + SHA256.
3. Для *.db файлов: sqlite3 PRAGMA integrity_check — должно вернуть "ok".
4. Output JSON report (stdout + persisted rolling log).
5. Метрика krab_backup_corrupt_count (Gauge) → panel.

Output JSON shape:
{
    "timestamp": "...",
    "total_backups": N,
    "total_size_mb": float,
    "corrupt_count": N,
    "corrupt_files": [{"path": str, "reason": str}, ...],
    "files": [{"path": str, "size_bytes": N, "sha256": "...",
               "integrity": "ok"|"failed"|"skipped"}, ...]
}

Rolling persistence: ~/.openclaw/krab_runtime_state/backup_verify_log.json
хранит последние 10 запусков (FIFO).
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

# --- Backup пути относительно repo root + home ---
DEFAULT_BACKUP_PATTERNS: tuple[tuple[str, str], ...] = (
    # (root_kind, glob) — root_kind ∈ {"repo", "home_state"}
    ("repo", "data/sessions/*.session*.bak*"),
    ("repo", "data/sessions/*.session.bak*"),
    ("repo", "data/memory/archive.db.bak*"),
    ("home_state", "backups/**/*"),
    ("home_state", "*.bak*"),
    ("home_state", "*.bak_*"),
)

# Файлы крупнее лимита НЕ хэшируются полностью — берём первый/последний chunk
# (для backup verify нужно детектить corruption, full hash для huge db
# слишком тяжёл). Default 500MB → читаем целиком; иначе streaming.
SHA256_STREAM_CHUNK = 1024 * 1024  # 1 MB

# Лимит integrity_check: read-only mode + timeout
SQLITE_INTEGRITY_TIMEOUT_SEC = 30.0


@dataclass
class BackupFile:
    """Запись об одном backup файле."""

    path: str
    size_bytes: int
    mtime: float
    sha256: str
    integrity: str  # "ok" | "failed" | "skipped" | "error"
    integrity_detail: str = ""


@dataclass
class VerifyReport:
    """Итоговый отчёт прохода."""

    timestamp: str
    total_backups: int
    total_size_mb: float
    corrupt_count: int
    corrupt_files: list[dict[str, str]] = field(default_factory=list)
    files: list[BackupFile] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "total_backups": self.total_backups,
            "total_size_mb": round(self.total_size_mb, 3),
            "corrupt_count": self.corrupt_count,
            "corrupt_files": self.corrupt_files,
            "files": [asdict(f) for f in self.files],
        }


def compute_sha256(path: Path) -> str:
    """Streaming SHA256; пустая строка при ошибке чтения."""
    try:
        h = hashlib.sha256()
        with path.open("rb") as f:
            while True:
                chunk = f.read(SHA256_STREAM_CHUNK)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return ""


def sqlite_integrity_check(path: Path) -> tuple[str, str]:
    """sqlite3 PRAGMA integrity_check на read-only URI.

    Returns (status, detail) где status ∈ {"ok", "failed", "error"}.
    """
    if not path.exists():
        return ("error", "file_missing")
    # Read-only URI чтобы не модифицировать backup
    uri = f"file:{path}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=SQLITE_INTEGRITY_TIMEOUT_SEC)
    except sqlite3.Error as exc:
        return ("error", f"connect_failed:{type(exc).__name__}:{exc}")
    try:
        cur = conn.execute("PRAGMA integrity_check;")
        rows = cur.fetchall()
        # integrity_check возвращает [("ok",)] при успехе, иначе список проблем
        if rows == [("ok",)]:
            return ("ok", "")
        msgs = ";".join(str(r[0]) for r in rows[:5])
        return ("failed", msgs[:500])
    except sqlite3.Error as exc:
        return ("error", f"{type(exc).__name__}:{exc}")
    finally:
        try:
            conn.close()
        except sqlite3.Error:
            pass


def is_sqlite_file(path: Path) -> bool:
    """Эвристика: расширение .db / .session / SQLite magic header."""
    name = path.name.lower()
    if any(part in name for part in (".db", ".session", ".sqlite")):
        return True
    try:
        with path.open("rb") as f:
            header = f.read(16)
        return header.startswith(b"SQLite format 3")
    except OSError:
        return False


def discover_backups(
    repo_dir: Path,
    home_state_dir: Path,
    patterns: tuple[tuple[str, str], ...] = DEFAULT_BACKUP_PATTERNS,
) -> list[Path]:
    """Walk all backup dirs/globs, dedupe absolute paths."""
    found: set[Path] = set()
    for root_kind, pattern in patterns:
        base = repo_dir if root_kind == "repo" else home_state_dir
        if not base.exists():
            continue
        try:
            for match in base.glob(pattern):
                if match.is_file():
                    found.add(match.resolve())
        except OSError:
            continue
    return sorted(found)


def verify_file(path: Path, *, run_integrity: bool = True) -> BackupFile:
    """Stat + SHA256 + (optional) sqlite integrity."""
    try:
        st = path.stat()
        size = st.st_size
        mtime = st.st_mtime
    except OSError:
        return BackupFile(
            path=str(path),
            size_bytes=0,
            mtime=0.0,
            sha256="",
            integrity="error",
            integrity_detail="stat_failed",
        )

    sha = compute_sha256(path)
    if not sha:
        return BackupFile(
            path=str(path),
            size_bytes=size,
            mtime=mtime,
            sha256="",
            integrity="error",
            integrity_detail="sha256_failed",
        )

    if run_integrity and is_sqlite_file(path):
        status, detail = sqlite_integrity_check(path)
    else:
        status, detail = ("skipped", "non_sqlite")

    return BackupFile(
        path=str(path),
        size_bytes=size,
        mtime=mtime,
        sha256=sha,
        integrity=status,
        integrity_detail=detail,
    )


def run_verify(
    repo_dir: Path,
    home_state_dir: Path,
    *,
    run_integrity: bool = True,
    patterns: tuple[tuple[str, str], ...] = DEFAULT_BACKUP_PATTERNS,
) -> VerifyReport:
    """Главный entrypoint — discover + verify all backups."""
    paths = discover_backups(repo_dir, home_state_dir, patterns=patterns)
    files: list[BackupFile] = []
    total_size = 0
    corrupt: list[dict[str, str]] = []

    for p in paths:
        record = verify_file(p, run_integrity=run_integrity)
        files.append(record)
        total_size += record.size_bytes
        if record.integrity == "failed" or record.integrity == "error":
            corrupt.append(
                {
                    "path": record.path,
                    "reason": f"{record.integrity}:{record.integrity_detail or 'unknown'}",
                }
            )

    return VerifyReport(
        timestamp=_dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat(),
        total_backups=len(files),
        total_size_mb=total_size / (1024 * 1024),
        corrupt_count=len(corrupt),
        corrupt_files=corrupt,
        files=files,
    )


def append_rolling_log(state_dir: Path, report: VerifyReport, *, keep: int = 10) -> Path:
    """Persistent rolling JSON log — последние `keep` запусков (FIFO)."""
    state_dir.mkdir(parents=True, exist_ok=True)
    log_path = state_dir / "backup_verify_log.json"
    history: list[dict] = []
    if log_path.exists():
        try:
            raw = json.loads(log_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and isinstance(raw.get("runs"), list):
                history = list(raw["runs"])
        except (json.JSONDecodeError, OSError):
            history = []
    history.append(report.to_dict())
    history = history[-keep:]
    log_path.write_text(
        json.dumps({"runs": history}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return log_path


def publish_metric(report: VerifyReport, panel_url: str) -> bool:
    """Публикует krab_backup_corrupt_count (Gauge) в owner panel (best-effort)."""
    payload = {
        "name": "krab_backup_corrupt_count",
        "type": "gauge",
        "values": {"_": report.corrupt_count},
    }
    try:
        req = urllib.request.Request(
            f"{panel_url.rstrip('/')}/api/metric",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            return 200 <= resp.status < 300
    except (urllib.error.URLError, OSError, TimeoutError):
        return False


def _default_repo_dir() -> Path:
    return Path(__file__).resolve().parent.parent


def _default_home_state_dir() -> Path:
    return Path(
        os.getenv(
            "KRAB_RUNTIME_STATE_DIR",
            str(Path.home() / ".openclaw" / "krab_runtime_state"),
        )
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Krab backup verify (Wave 105)")
    parser.add_argument("--repo", default=str(_default_repo_dir()))
    parser.add_argument("--home-state", default=str(_default_home_state_dir()))
    parser.add_argument(
        "--no-integrity",
        action="store_true",
        help="Skip sqlite3 PRAGMA integrity_check (только SHA256+stat)",
    )
    parser.add_argument("--no-publish", action="store_true")
    parser.add_argument("--no-log", action="store_true", help="Не писать rolling log")
    parser.add_argument("--panel-url", default=os.getenv("KRAB_PANEL_URL", "http://127.0.0.1:8080"))
    args = parser.parse_args(argv)

    repo = Path(args.repo).resolve()
    home_state = Path(args.home_state).resolve()

    report = run_verify(repo, home_state, run_integrity=not args.no_integrity)
    output = report.to_dict()
    print(json.dumps(output, ensure_ascii=False, indent=2))

    if not args.no_log:
        try:
            append_rolling_log(home_state, report)
        except OSError as exc:
            print(f"warn: rolling log persist failed: {exc}", flush=True)

    if not args.no_publish:
        publish_metric(report, args.panel_url)

    # Non-zero exit при corruption — LaunchAgent логирует stderr
    return 1 if report.corrupt_count > 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
