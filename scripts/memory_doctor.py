#!/usr/bin/env python3
"""
memory_doctor.py — health check для archive.db (FTS5 + vec_chunks + chunks)
плюс integrity-обход всех runtime sqlite-файлов Krab.

Запуск:
    venv/bin/python3 scripts/memory_doctor.py            # read-only diagnostic
    venv/bin/python3 scripts/memory_doctor.py --fix      # авто-починка orphans
    venv/bin/python3 scripts/memory_doctor.py --all-db   # только обход всех DB
    venv/bin/python3 scripts/memory_doctor.py --json

Что проверяет (archive.db):
1. **chunks ↔ vec_chunks alignment** — каждый chunk.id должен иметь
   соответствующую vec_chunks.rowid строку (vector embedding).
2. **chunk_messages ↔ messages alignment** — все message_id в chunk_messages
   должны существовать в messages.
3. **chunk_messages ↔ chunks alignment** — все chunk_id в chunk_messages
   должны существовать в chunks.
4. **indexer_state coverage** — для каждого chat_id в indexer_state
   должны быть chunks (sanity check).
5. **vec0 config** — vec_chunks_meta содержит indexed_at + model_dim +
   model_name (это **внутренние** поля vec0, НЕ метаданные чанков!).
6. **FTS5 sync** — messages_fts.rowid coverage относительно chunks.id
   (FTS5 indexes chunks.text_redacted via content='chunks', НЕ messages).

Что проверяет (all-db sweep, Session 28+):
- `PRAGMA integrity_check` + `PRAGMA quick_check` для всех known runtime DB:
  archive.db, history_cache, search_cache, test_cache, kraab.session,
  swarm_*.session, runs.sqlite, memory/main.sqlite, flows/registry.sqlite.
- WAL/journal mode + размер файла + presence sidecar (-wal/-shm).
- Missing files = graceful skip (не ошибка).
- 0-byte stub-файлы (legacy/placeholder) маркируются `empty` без падения.
- Реальный corrupt detected → выводится отдельной секцией с маркером.

При --fix:
- Удаляет orphan chunk_messages rows (chunk_id или message_id не существует).
- Удаляет chunks без vector embedding (после warning prompt).
- НЕ трогает vec_chunks_meta (vec0 internal).
- НЕ трогает другие DB (только archive.db).

Note (2026-04-25 finding):
    "vec_chunks_meta desync" в Session 13 backlog — **misdiagnosis**.
    vec_chunks_meta это (key, value) config таблица созданная vec0,
    НЕ chunk metadata. Реальный desync check — chunks.id vs vec_chunks.rowid.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import sqlite_vec  # type: ignore[import-untyped]

DEFAULT_DB = Path.home() / ".openclaw" / "krab_memory" / "archive.db"

# Маркеры corruption (синхронизированы с src/bootstrap/db_corruption_guard.py).
_CORRUPTION_MARKERS: tuple[str, ...] = (
    "database disk image is malformed",
    "file is not a database",
    "file is encrypted or is not a database",
    "malformed database schema",
    "disk i/o error",
)


def _project_root() -> Path:
    """Корень репозитория Краба (родитель `scripts/`)."""
    return Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class KnownDb:
    """Описание известного runtime sqlite-файла."""

    path: Path
    kind: str  # session | archive | cache | tasks | memory | flows
    owner: str  # короткое имя модуля-владельца (для отчёта)


def known_db_paths() -> list[KnownDb]:
    """Полный список runtime DB Krab.

    Lazy чтобы тесты могли monkeypatch HOME / TELEGRAM_SESSION_NAME.
    """
    home = Path.home()
    root = _project_root()
    sessions_dir = root / "data" / "sessions"
    session_name = os.getenv("TELEGRAM_SESSION_NAME", "kraab")
    runtime = home / ".openclaw" / "krab_runtime_state"
    return [
        # Pyrogram session (critical) + swarm sessions
        KnownDb(sessions_dir / f"{session_name}.session", "session", "pyrogram"),
        KnownDb(sessions_dir / "swarm_analysts.session", "session", "pyrogram-swarm"),
        KnownDb(sessions_dir / "swarm_coders.session", "session", "pyrogram-swarm"),
        KnownDb(sessions_dir / "swarm_creative.session", "session", "pyrogram-swarm"),
        KnownDb(sessions_dir / "swarm_traders.session", "session", "pyrogram-swarm"),
        # Memory layer
        KnownDb(home / ".openclaw/krab_memory/archive.db", "archive", "memory_archive"),
        # Cache layer (cache_manager.py)
        KnownDb(runtime / "history_cache.db", "cache", "cache_manager.history"),
        KnownDb(runtime / "search_cache.db", "cache", "cache_manager.search"),
        KnownDb(runtime / "test_cache.db", "cache", "cache_manager.test"),
        # OpenClaw runtime
        KnownDb(home / ".openclaw/tasks/runs.sqlite", "tasks", "openclaw_task_poller"),
        KnownDb(home / ".openclaw/memory/main.sqlite", "memory", "openclaw_memory"),
        KnownDb(home / ".openclaw/flows/registry.sqlite", "flows", "openclaw_flows"),
    ]


@dataclass
class DbReport:
    """Отчёт по одной DB."""

    path: str
    kind: str
    owner: str
    exists: bool
    empty: bool = False
    size_bytes: int = 0
    integrity: str = ""
    quick_check: str = ""
    journal_mode: str = ""
    has_wal: bool = False
    has_shm: bool = False
    error: str = ""
    corrupt: bool = False
    ok: bool = False
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "kind": self.kind,
            "owner": self.owner,
            "exists": self.exists,
            "empty": self.empty,
            "size_bytes": self.size_bytes,
            "integrity": self.integrity,
            "quick_check": self.quick_check,
            "journal_mode": self.journal_mode,
            "has_wal": self.has_wal,
            "has_shm": self.has_shm,
            "error": self.error,
            "corrupt": self.corrupt,
            "ok": self.ok,
        }


def _is_corruption_text(text: str) -> bool:
    """Проверяет строку (PRAGMA result или exc message) на corruption-маркер."""
    low = (text or "").lower()
    return any(marker in low for marker in _CORRUPTION_MARKERS)


def check_single_db(entry: KnownDb, *, timeout_sec: float = 3.0) -> DbReport:
    """Запускает integrity_check + quick_check на одной DB.

    Открывает через `file:?mode=ro` чтобы НЕ создавать новый файл и НЕ
    провоцировать запись WAL во время диагностики. Missing/empty —
    не ошибка, просто помечаются.
    """
    rep = DbReport(
        path=str(entry.path),
        kind=entry.kind,
        owner=entry.owner,
        exists=entry.path.exists(),
    )
    if not rep.exists:
        rep.ok = True  # отсутствие — допустимо (создастся при первом write)
        return rep
    rep.size_bytes = entry.path.stat().st_size
    if rep.size_bytes == 0:
        rep.empty = True
        rep.ok = True  # 0-байтовый stub legacy не считаем поломкой
        return rep
    rep.has_wal = entry.path.with_name(entry.path.name + "-wal").exists()
    rep.has_shm = entry.path.with_name(entry.path.name + "-shm").exists()
    try:
        uri = f"file:{entry.path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=timeout_sec)
        try:
            ic = conn.execute("PRAGMA integrity_check").fetchone()
            qc = conn.execute("PRAGMA quick_check").fetchone()
            jm = conn.execute("PRAGMA journal_mode").fetchone()
            rep.integrity = (ic[0] if ic else "").strip()
            rep.quick_check = (qc[0] if qc else "").strip()
            rep.journal_mode = (jm[0] if jm else "").strip()
        finally:
            conn.close()
    except sqlite3.DatabaseError as exc:
        rep.error = f"{type(exc).__name__}: {exc}"
        rep.corrupt = _is_corruption_text(rep.error)
        rep.ok = False
        return rep
    except Exception as exc:  # noqa: BLE001
        rep.error = f"{type(exc).__name__}: {exc}"
        rep.ok = False
        return rep
    rep.corrupt = (
        _is_corruption_text(rep.integrity)
        or _is_corruption_text(rep.quick_check)
        or rep.integrity.lower() != "ok"
        or rep.quick_check.lower() != "ok"
    )
    rep.ok = rep.integrity.lower() == "ok" and rep.quick_check.lower() == "ok"
    return rep


def check_all_databases(
    entries: list[KnownDb] | None = None,
) -> dict[str, Any]:
    """Обход всех known DB. Возвращает summary + список отчётов."""
    if entries is None:
        entries = known_db_paths()
    reports = [check_single_db(e) for e in entries]
    corrupt = [r for r in reports if r.corrupt]
    failed = [r for r in reports if not r.ok and not r.corrupt]
    return {
        "total": len(reports),
        "ok_count": sum(1 for r in reports if r.ok),
        "corrupt_count": len(corrupt),
        "failed_count": len(failed),
        "missing_count": sum(1 for r in reports if not r.exists),
        "empty_count": sum(1 for r in reports if r.empty),
        "all_ok": not corrupt and not failed,
        "reports": [r.to_dict() for r in reports],
    }


# --- archive.db специфические проверки (исторический функционал) ---


def connect(db_path: Path) -> sqlite3.Connection:
    """Открывает archive.db с подключённым vec0 расширением."""
    conn = sqlite3.connect(db_path)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    return conn


def check_chunks_vec_alignment(c: sqlite3.Cursor) -> dict:
    """chunks.id vs vec_chunks.rowid — должны совпадать 1-к-1."""
    n_chunks = c.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    n_vec = c.execute("SELECT COUNT(*) FROM vec_chunks").fetchone()[0]
    chunks_no_vec = c.execute(
        "SELECT COUNT(*) FROM chunks WHERE id NOT IN (SELECT rowid FROM vec_chunks)"
    ).fetchone()[0]
    vec_orphans = c.execute(
        "SELECT COUNT(*) FROM vec_chunks WHERE rowid NOT IN (SELECT id FROM chunks)"
    ).fetchone()[0]
    return {
        "chunks_total": n_chunks,
        "vec_chunks_total": n_vec,
        "chunks_without_vec": chunks_no_vec,
        "vec_orphans": vec_orphans,
        "ok": chunks_no_vec == 0 and vec_orphans == 0,
    }


def check_chunk_messages(c: sqlite3.Cursor) -> dict:
    """chunk_messages → chunks + messages."""
    cm_total = c.execute("SELECT COUNT(*) FROM chunk_messages").fetchone()[0]
    orphan_chunk = c.execute(
        "SELECT COUNT(*) FROM chunk_messages cm "
        "WHERE cm.chunk_id NOT IN (SELECT chunk_id FROM chunks)"
    ).fetchone()[0]
    # messages has composite PK (message_id, chat_id) — match on both.
    orphan_msg = c.execute(
        "SELECT COUNT(*) FROM chunk_messages cm "
        "WHERE NOT EXISTS ("
        "  SELECT 1 FROM messages m "
        "  WHERE m.message_id = cm.message_id AND m.chat_id = cm.chat_id"
        ")"
    ).fetchone()[0]
    return {
        "chunk_messages_total": cm_total,
        "orphan_chunk_id": orphan_chunk,
        "orphan_message_id": orphan_msg,
        "ok": orphan_chunk == 0 and orphan_msg == 0,
    }


def check_indexer_state(c: sqlite3.Cursor) -> dict:
    """indexer_state coverage — для каждого chat_id должны быть chunks."""
    rows = c.execute("SELECT chat_id, last_message_id FROM indexer_state").fetchall()
    chat_ids = {r[0] for r in rows}
    chats_with_chunks = {r[0] for r in c.execute("SELECT DISTINCT chat_id FROM chunks").fetchall()}
    missing = chat_ids - chats_with_chunks
    return {
        "indexer_state_chats": len(chat_ids),
        "chats_with_chunks": len(chats_with_chunks),
        "missing_chunks_for_chats": sorted(missing),
        "ok": not missing,
    }


def check_vec0_config(c: sqlite3.Cursor) -> dict:
    """vec_chunks_meta = vec0 internal config (key, value)."""
    rows = dict(c.execute("SELECT key, value FROM vec_chunks_meta").fetchall())
    expected = {"indexed_at", "model_dim", "model_name"}
    missing_keys = expected - set(rows)
    return {
        "config": rows,
        "missing_keys": sorted(missing_keys),
        "ok": not missing_keys,
    }


def check_fts(c: sqlite3.Cursor) -> dict:
    """messages_fts.rowid должен совпадать с chunks.id (FTS5 content='chunks').

    Note: FTS5 индексирует chunks.text_redacted, не индивидуальные messages
    (см. CREATE VIRTUAL TABLE messages_fts ... content='chunks').
    Поэтому корректная проверка — fts.rowid coverage относительно chunks.id.
    """
    n_chunks = c.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    n_fts = c.execute("SELECT COUNT(*) FROM messages_fts").fetchone()[0]
    # Chunks без FTS строки (rowid mismatch)
    fts_orphans = c.execute(
        "SELECT COUNT(*) FROM chunks WHERE id NOT IN (SELECT rowid FROM messages_fts)"
    ).fetchone()[0]
    delta = n_chunks - n_fts
    return {
        "chunks_total": n_chunks,
        "fts_total": n_fts,
        "chunks_without_fts": fts_orphans,
        "delta": delta,
        "ok": fts_orphans == 0 and abs(delta) < 100,
    }


def fix_chunk_message_orphans(c: sqlite3.Cursor) -> int:
    """Удаляет orphan rows. Возвращает кол-во удалённых."""
    deleted = 0
    n = c.execute(
        "DELETE FROM chunk_messages WHERE chunk_id NOT IN (SELECT chunk_id FROM chunks)"
    ).rowcount
    deleted += n or 0
    n = c.execute(
        "DELETE FROM chunk_messages "
        "WHERE NOT EXISTS ("
        "  SELECT 1 FROM messages m "
        "  WHERE m.message_id = chunk_messages.message_id "
        "    AND m.chat_id = chunk_messages.chat_id"
        ")"
    ).rowcount
    deleted += n or 0
    return deleted


def _print_all_db_summary(summary: dict[str, Any]) -> None:
    """Человекочитаемый вывод all-db sweep."""
    print("\n=== all-db integrity sweep ===")
    print(
        f"total={summary['total']}  ok={summary['ok_count']}  "
        f"corrupt={summary['corrupt_count']}  failed={summary['failed_count']}  "
        f"missing={summary['missing_count']}  empty={summary['empty_count']}"
    )
    for r in summary["reports"]:
        if not r["exists"]:
            mark = "·"
            note = "missing"
        elif r["empty"]:
            mark = "·"
            note = "empty(stub)"
        elif r["corrupt"]:
            mark = "🚨"
            note = f"CORRUPT: {r['error'] or r['integrity']}"
        elif not r["ok"]:
            mark = "⚠️"
            note = f"failed: {r['error'] or r['integrity']}"
        else:
            note = (
                f"ic={r['integrity']} qc={r['quick_check']} "
                f"jm={r['journal_mode']} {r['size_bytes'] // 1024}KB"
            )
            mark = "✅"
        print(f"  {mark} [{r['kind']}/{r['owner']}] {r['path']}\n     {note}")
    if summary["corrupt_count"]:
        print("\n🚨 CORRUPTION DETECTED — quarantine рекомендуется")
    elif summary["failed_count"]:
        print("\n⚠️  Не все DB прошли проверку (см. выше)")
    else:
        print("\n✅ all known DB integrity OK")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--fix", action="store_true", help="Auto-fix orphans")
    parser.add_argument("--json", action="store_true", help="Machine-readable output")
    parser.add_argument(
        "--all-db",
        action="store_true",
        help="Только integrity sweep по всем known DB (без archive-specific checks)",
    )
    parser.add_argument(
        "--skip-all-db",
        action="store_true",
        help="Не запускать all-db sweep (по умолчанию запускается всегда)",
    )
    args = parser.parse_args()

    # --- режим только sweep ---
    if args.all_db:
        summary = check_all_databases()
        if args.json:
            import json

            print(json.dumps(summary, ensure_ascii=False, indent=2))
        else:
            _print_all_db_summary(summary)
        return 0 if summary["all_ok"] else 1

    # --- archive.db checks (исторический режим) ---
    if not args.db.exists() or args.db.stat().st_size == 0:
        print(f"ERROR: {args.db} not found or empty", file=sys.stderr)
        return 2

    conn = connect(args.db)
    c = conn.cursor()

    results = {
        "db_path": str(args.db),
        "db_size_mb": round(args.db.stat().st_size / 1024 / 1024, 1),
        "chunks_vec_alignment": check_chunks_vec_alignment(c),
        "chunk_messages": check_chunk_messages(c),
        "indexer_state": check_indexer_state(c),
        "vec0_config": check_vec0_config(c),
        "fts": check_fts(c),
    }

    # Дополнительный all-db sweep — добавляет реальное покрытие incident-сценария.
    if not args.skip_all_db:
        results["all_databases"] = check_all_databases()

    archive_ok = all(
        v["ok"]
        for k, v in results.items()
        if isinstance(v, dict) and "ok" in v and k != "all_databases"
    )
    sweep_ok = results.get("all_databases", {}).get("all_ok", True)
    all_ok = archive_ok and sweep_ok

    if args.fix and not results["chunk_messages"]["ok"]:
        deleted = fix_chunk_message_orphans(c)
        conn.commit()
        results["fix_applied"] = {"deleted_chunk_messages": deleted}

    if args.json:
        import json

        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print(f"=== memory_doctor — {args.db}")
        print(f"Size: {results['db_size_mb']} MB")
        for section, data in results.items():
            if section == "all_databases":
                continue
            if not isinstance(data, dict) or "ok" not in data:
                continue
            mark = "✅" if data["ok"] else "⚠️"
            print(f"\n{mark} {section}")
            for k, v in data.items():
                if k == "ok":
                    continue
                print(f"   {k}: {v}")
        if "all_databases" in results:
            _print_all_db_summary(results["all_databases"])
        print(f"\n{'✅ ALL CHECKS PASSED' if all_ok else '⚠️  ISSUES FOUND'}")

    conn.close()
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
