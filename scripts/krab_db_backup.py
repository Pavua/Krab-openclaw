"""Wave 44-D: Daily backup critical sqlite DBs.

Backup-target paths:
- ~/Antigravity_AGENTS/Краб/data/sessions/kraab.session
- ~/.openclaw/krab_memory/archive.db
- ~/.openclaw/tasks/runs.sqlite

Steps per DB:
1. Verify source integrity_check ok ПЕРЕД backup'ом (не backup'аем corrupted!)
2. sqlite3 .backup command (atomic snapshot, не блокирует main process)
3. gzip backup file (typically -90% size)
4. Запись metadata: peers_count, integrity status, source size
5. Retention: cleanup files >14 days old

Output structure:
~/.openclaw/backups/2026-05-06/
  ├── kraab.session.bak.gz           (5 MB compressed)
  ├── kraab.session.meta.json        (peers=447, integrity=ok, source_size=90112)
  ├── archive.db.bak.gz              (compressed)
  ├── archive.db.meta.json
  ├── runs.sqlite.bak.gz
  └── runs.sqlite.meta.json
"""

import gzip
import json
import os
import shutil
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

BACKUP_ROOT = Path.home() / ".openclaw/backups"
RETENTION_DAYS = int(os.environ.get("KRAB_DB_BACKUP_RETENTION_DAYS", "14"))

CRITICAL_DBS = [
    Path.home() / "Antigravity_AGENTS/Краб/data/sessions/kraab.session",
    Path.home() / ".openclaw/krab_memory/archive.db",
    Path.home() / ".openclaw/tasks/runs.sqlite",
]


def verify_source_integrity(db_path: Path) -> tuple[bool, str]:
    """PRAGMA integrity_check на source ПЕРЕД backup'ом."""
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=10)
        cursor = conn.execute("PRAGMA integrity_check")
        result = cursor.fetchone()[0]
        conn.close()
        return result == "ok", result
    except Exception as e:
        return False, str(e)


def get_peers_count(db_path: Path) -> int:
    """Считаем количество peers только для session-файлов."""
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
        cursor = conn.execute("SELECT count(*) FROM peers")
        count = cursor.fetchone()[0]
        conn.close()
        return count
    except Exception:
        return -1


def backup_db(source: Path, target_dir: Path) -> dict:
    """Single DB backup. Возвращает metadata dict."""
    # Шаг 1: проверяем целостность source, не backup'аем corrupted DB
    integrity_ok, integrity_detail = verify_source_integrity(source)
    if not integrity_ok:
        return {
            "ok": False,
            "source": str(source),
            "skipped": True,
            "reason": f"source_integrity_failed: {integrity_detail[:100]}",
        }

    # Peers count только для session-файлов
    peers_count = get_peers_count(source) if "session" in source.name else -1
    target_dir.mkdir(parents=True, exist_ok=True)
    backup_path = target_dir / f"{source.name}.bak"

    # Шаг 2: sqlite3 .backup — atomic snapshot, не блокирует основной процесс
    try:
        src_conn = sqlite3.connect(str(source), timeout=30)
        backup_conn = sqlite3.connect(str(backup_path))
        src_conn.backup(backup_conn)
        src_conn.close()
        backup_conn.close()
    except Exception as e:
        return {"ok": False, "source": str(source), "error": str(e)[:200]}

    # Шаг 3: gzip сжатие (compresslevel=6 — баланс скорость/размер)
    gz_path = backup_path.with_suffix(backup_path.suffix + ".gz")
    with backup_path.open("rb") as f_in:
        with gzip.open(gz_path, "wb", compresslevel=6) as f_out:
            shutil.copyfileobj(f_in, f_out)
    backup_path.unlink()  # удаляем несжатый временный файл

    # Шаг 4: metadata
    return {
        "ok": True,
        "source": str(source),
        "backup": str(gz_path),
        "source_size": source.stat().st_size,
        "compressed_size": gz_path.stat().st_size,
        "peers_count": peers_count if peers_count >= 0 else None,
        "integrity": "ok",
        "ts": time.time(),
    }


def cleanup_old(retention_days: int) -> list[str]:
    """Удаляет backup-папки старше N дней. Возвращает список удалённых."""
    removed = []
    if not BACKUP_ROOT.exists():
        return removed
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    for d in BACKUP_ROOT.iterdir():
        if not d.is_dir():
            continue
        try:
            # Ожидаем директории вида YYYY-MM-DD
            dir_date = datetime.strptime(d.name, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            if dir_date < cutoff:
                shutil.rmtree(d)
                removed.append(d.name)
                print(f"removed_old_backup: {d.name}")
        except ValueError:
            # Пропускаем директории с нестандартными именами
            continue
    return removed


def main() -> int:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    target_dir = BACKUP_ROOT / today

    results = []
    for db in CRITICAL_DBS:
        if not db.exists():
            # Не ошибка — некоторые DBs могут ещё не существовать
            results.append({"source": str(db), "skipped": True, "reason": "not_exists"})
            continue
        result = backup_db(db, target_dir)
        results.append(result)

        # Шаг 4: сохраняем metadata рядом с backup'ом
        meta_path = target_dir / f"{db.name}.meta.json"
        meta_path.write_text(json.dumps(result, indent=2, ensure_ascii=False, default=str))

    # Шаг 5: очистка старых backup'ов
    removed = cleanup_old(RETENTION_DAYS)

    summary = {
        "ts": time.time(),
        "date": today,
        "dbs_backed_up": sum(1 for r in results if r.get("ok")),
        "dbs_skipped": sum(1 for r in results if r.get("skipped")),
        "total": len(results),
        "removed_old": removed,
        "results": results,
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))

    # Выходим с 0 даже если некоторые DBs пропущены (not_exists — норма)
    # Выходим с 1 только если все backup'ы failed
    backed_up = summary["dbs_backed_up"]
    existing = sum(1 for r in results if not r.get("skipped") or r.get("reason") != "not_exists")
    if existing > 0 and backed_up == 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
