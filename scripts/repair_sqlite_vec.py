#!/usr/bin/env python3
"""
Безопасное восстановление FTS5 + vec_chunks в archive.db.

Диагностика (Wave 29-N) выявила два вида десинхронизации:
  1. messages_fts (FTS5) — rowid'ы индекса не совпадают с chunks.id.
     Симптом: "database disk image is malformed" при JOIN-запросах.
  2. vec_chunks — rowid'ы векторов тоже не совпадают с chunks.id.

Причина: chunks были удалены и пересозданы с новыми AUTOINCREMENT id,
а FTS5 и vec_chunks не были пересинхронизированы.

Скрипт:
  1. Делает backup archive.db → archive.db.pre-repair-YYYYMMDD_HHMMSS
  2. Пересобирает messages_fts через FTS5 rebuild
  3. DROP + CREATE vec_chunks, заново кодирует все chunks
  4. Идемпотентен (можно повторить без двойного backup'а если backup свежий)

Usage:
    venv/bin/python scripts/repair_sqlite_vec.py [--dry-run] [--skip-vec] [--skip-fts]

Flags:
    --dry-run   Только диагностика, без изменений и backup
    --skip-vec  Пропустить пересборку vec_chunks (только FTS5 rebuild)
    --skip-fts  Пропустить FTS5 rebuild (только vec_chunks rebuild)
    --no-backup Не создавать backup (осторожно!)
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

DB_PATH = Path("~/.openclaw/krab_memory/archive.db").expanduser()
BACKUP_SUFFIX_FMT = "pre-repair-%Y%m%d_%H%M%S"


# ---------------------------------------------------------------------------
# Диагностика.
# ---------------------------------------------------------------------------

def _load_vec_ext(conn: sqlite3.Connection) -> bool:
    """Загрузить sqlite-vec extension. True если успешно."""
    try:
        import sqlite_vec  # type: ignore[import-not-found]

        conn.enable_load_extension(True)
        try:
            sqlite_vec.load(conn)
        finally:
            conn.enable_load_extension(False)
        return True
    except Exception as exc:
        print(f"  [WARN] sqlite-vec unavailable: {exc}")
        return False


def diagnose(conn: sqlite3.Connection) -> dict:
    """Собрать диагностику FTS5 и vec_chunks. Возвращает dict с метриками."""
    result: dict = {}

    # --- chunks ---
    result["chunks_count"] = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    result["chunks_id_range"] = conn.execute("SELECT MIN(id), MAX(id) FROM chunks").fetchone()

    # --- FTS5 ---
    result["fts_doc_count"] = conn.execute("SELECT COUNT(*) FROM messages_fts").fetchone()[0]
    result["fts_docsize_count"] = conn.execute("SELECT COUNT(*) FROM messages_fts_docsize").fetchone()[0]

    # Проверяем десинхронизацию через JOIN: FTS MATCH должен видеть только
    # строки из chunks. Любая строка в FTS5 без соответствующего chunks.id
    # вызывает "disk image is malformed" при JOIN-запросах.
    # Используем ALL FTS rowids через docsize shadow table vs chunks.
    result["fts_docsize_orphaned"] = conn.execute(
        """
        SELECT COUNT(*) FROM messages_fts_docsize AS d
        LEFT JOIN chunks AS c ON c.id = d.id
        WHERE c.id IS NULL
        """
    ).fetchone()[0]
    result["fts_sample_size"] = result["fts_docsize_count"]
    result["fts_orphaned_in_sample"] = result["fts_docsize_orphaned"]

    # --- vec_chunks ---
    vec_ext_ok = _load_vec_ext(conn)
    result["vec_ext_ok"] = vec_ext_ok
    if vec_ext_ok:
        try:
            result["vec_count"] = conn.execute("SELECT COUNT(*) FROM vec_chunks").fetchone()[0]
            # Проверяем через shadow table vec_chunks_rowids
            orphan = conn.execute(
                """
                SELECT COUNT(*) FROM vec_chunks_rowids AS vr
                LEFT JOIN chunks AS c ON c.id = vr.id
                WHERE c.id IS NULL
                """
            ).fetchone()[0]
            result["vec_orphaned"] = orphan
        except Exception as exc:
            result["vec_count"] = -1
            result["vec_orphaned"] = -1
            result["vec_error"] = str(exc)
    else:
        result["vec_count"] = -1
        result["vec_orphaned"] = -1

    return result


def print_diagnosis(d: dict) -> None:
    print("\n--- Диагностика archive.db ---")
    print(f"  chunks: {d['chunks_count']} строк, id range {d['chunks_id_range']}")
    print(f"  messages_fts: {d['fts_doc_count']} (docsize: {d['fts_docsize_count']})")
    orphan_pct = (
        100 * d["fts_orphaned_in_sample"] / d["fts_docsize_count"]
        if d["fts_docsize_count"]
        else 0
    )
    print(
        f"  FTS десинхронизация: {d['fts_orphaned_in_sample']}/{d['fts_docsize_count']} "
        f"orphaned docsize rows ({orphan_pct:.0f}%) — {'НУЖЕН REPAIR' if d['fts_orphaned_in_sample'] > 0 else 'OK'}"
    )
    if d["vec_ext_ok"]:
        print(
            f"  vec_chunks: {d['vec_count']} векторов, "
            f"{d.get('vec_orphaned', '?')} orphaned — "
            f"{'НУЖЕН REPAIR' if d.get('vec_orphaned', 0) > 0 else 'OK'}"
        )
    else:
        print("  vec_chunks: extension недоступен")
    print()


# ---------------------------------------------------------------------------
# Repair: FTS5.
# ---------------------------------------------------------------------------

def repair_fts(conn: sqlite3.Connection) -> None:
    """
    Пересобрать FTS5 через INSERT INTO messages_fts(messages_fts) VALUES('rebuild').

    FTS5 rebuild читает content='chunks' заново по всем chunk.id → rowid.
    После rebuild JOIN-запросы работают корректно.
    """
    print("  [FTS5] запускаю rebuild...")
    t0 = time.perf_counter()
    conn.execute("INSERT INTO messages_fts(messages_fts) VALUES('rebuild')")
    conn.commit()
    elapsed = time.perf_counter() - t0
    print(f"  [FTS5] rebuild завершён за {elapsed:.2f}s")


# ---------------------------------------------------------------------------
# Repair: vec_chunks.
# ---------------------------------------------------------------------------

def repair_vec(conn: sqlite3.Connection, batch_size: int = 512) -> None:
    """
    DROP + CREATE vec_chunks, затем re-encode всех chunks.

    Использует ту же логику что encode_memory_phase2.py --force.
    """
    from src.core.memory_embedder import (
        DEFAULT_DIM,
        create_vec_table,
        serialize_f32,
    )
    from src.core.memory_embeddings import get_embedding_model

    print("  [VEC] DROP + CREATE vec_chunks...")
    conn.execute("DROP TABLE IF EXISTS vec_chunks")
    conn.commit()
    create_vec_table(conn, dim=DEFAULT_DIM)
    print("  [VEC] таблица пересоздана")

    rows = conn.execute(
        "SELECT id, text_redacted FROM chunks ORDER BY id"
    ).fetchall()
    print(f"  [VEC] загружаю Model2Vec модель для {len(rows)} chunks...")
    model = get_embedding_model()
    print("  [VEC] модель загружена, начинаю encode...")

    t0 = time.perf_counter()
    processed = 0
    for start in range(0, len(rows), batch_size):
        batch = rows[start : start + batch_size]
        texts = [r[1] or "" for r in batch]
        vecs = model.encode(texts)
        payload = [(batch[i][0], serialize_f32(vecs[i])) for i in range(len(batch))]
        conn.executemany(
            "INSERT INTO vec_chunks(rowid, vector) VALUES (?, ?)", payload
        )
        conn.commit()
        processed += len(batch)
        if processed % 1000 == 0 or processed == len(rows):
            elapsed = time.perf_counter() - t0
            rate = processed / elapsed if elapsed > 0 else 0.0
            print(f"  [VEC] [{processed}/{len(rows)}] {rate:.1f} chunks/sec — {elapsed:.1f}s")

    total = time.perf_counter() - t0
    print(f"  [VEC] encode завершён: {processed} chunks за {total:.1f}s")


# ---------------------------------------------------------------------------
# Backup.
# ---------------------------------------------------------------------------

def make_backup(db_path: Path) -> Path:
    """Скопировать db_path → db_path.pre-repair-YYYYMMDD_HHMMSS."""
    ts = datetime.now(timezone.utc).strftime(BACKUP_SUFFIX_FMT)
    backup_path = db_path.with_suffix(f".db.{ts}")
    # Идемпотентность: если backup с таким timestamp уже есть — пропустить
    if backup_path.exists():
        print(f"  [BACKUP] уже существует: {backup_path.name} — пропускаю")
        return backup_path
    print(f"  [BACKUP] копирую {db_path.name} → {backup_path.name}...")
    shutil.copy2(db_path, backup_path)
    size_mb = backup_path.stat().st_size / 1024 / 1024
    print(f"  [BACKUP] готово: {backup_path.name} ({size_mb:.1f} МБ)")
    return backup_path


# ---------------------------------------------------------------------------
# Main.
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Safe repair of FTS5 + vec_chunks in archive.db"
    )
    parser.add_argument("--dry-run", action="store_true", help="только диагностика")
    parser.add_argument("--skip-vec", action="store_true", help="пропустить vec_chunks rebuild")
    parser.add_argument("--skip-fts", action="store_true", help="пропустить FTS5 rebuild")
    parser.add_argument("--no-backup", action="store_true", help="не делать backup")
    parser.add_argument(
        "--batch-size", type=int, default=512, help="batch size для vec encode"
    )
    args = parser.parse_args()

    if not DB_PATH.exists():
        print(f"[ERROR] archive.db не найден: {DB_PATH}")
        return 1

    size_mb = DB_PATH.stat().st_size / 1024 / 1024
    print(f"archive.db: {DB_PATH} ({size_mb:.1f} МБ)")

    # --- Диагностика ---
    conn = sqlite3.connect(str(DB_PATH))
    d = diagnose(conn)
    print_diagnosis(d)

    needs_fts = d["fts_orphaned_in_sample"] > 0
    needs_vec = d.get("vec_orphaned", 0) > 0 and d["vec_ext_ok"]

    if not needs_fts and not needs_vec:
        print("[OK] Нет повреждений — repair не нужен.")
        conn.close()
        return 0

    if args.dry_run:
        print("[DRY-RUN] Изменения не вносятся.")
        conn.close()
        return 0

    conn.close()

    # --- Backup ---
    if not args.no_backup:
        make_backup(DB_PATH)
    else:
        print("  [BACKUP] пропущен (--no-backup)")

    # --- Repair ---
    conn = sqlite3.connect(str(DB_PATH))
    _load_vec_ext(conn)  # нужен для vec операций

    if needs_fts and not args.skip_fts:
        repair_fts(conn)
    elif args.skip_fts:
        print("  [FTS5] пропущен (--skip-fts)")
    else:
        print("  [FTS5] OK — repair не нужен")

    if needs_vec and not args.skip_vec:
        repair_vec(conn, batch_size=args.batch_size)
    elif args.skip_vec:
        print("  [VEC] пропущен (--skip-vec)")
    else:
        print("  [VEC] OK — repair не нужен")

    conn.close()

    # --- Проверка ---
    print("\n--- Проверка после repair ---")
    conn = sqlite3.connect(str(DB_PATH))
    d2 = diagnose(conn)
    print_diagnosis(d2)

    # Smoke test: FTS JOIN query
    try:
        rows = conn.execute(
            """
            SELECT c.chunk_id FROM messages_fts AS f
            JOIN chunks AS c ON c.rowid = f.rowid
            WHERE f.text_redacted MATCH '"краб"'
            LIMIT 1
            """
        ).fetchall()
        print(f"  [SMOKE FTS] OK — нашёл {len(rows)} результатов")
    except Exception as exc:
        print(f"  [SMOKE FTS] FAIL: {exc}")

    conn.close()
    print("\n[DONE] Repair завершён.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
