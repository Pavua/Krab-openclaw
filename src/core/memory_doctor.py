"""Memory Doctor — диагностика и ремонт Memory Layer (archive.db).

Публичный API:
  - async run_diagnostics() -> dict   — все 6 проверок, без side-effects
  - async run_repairs(checks) -> dict — авто-ремонт по результатам диагностики

Вызывается из:
  - GET  /api/memory/doctor        (диагностика)
  - POST /api/memory/doctor/fix    (диагностика + ремонт)
  - scripts/memory_doctor.command  (shell-обёртка)
"""

from __future__ import annotations

import asyncio
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

# Canonical путь к archive.db Memory Layer
_DEFAULT_DB = Path.home() / ".openclaw" / "krab_memory" / "archive.db"
_PANEL_URL = "http://127.0.0.1:8080"
_MCP_PORT = 8011

# Пороги
_SIZE_WARN_GB = 2.0
_ENCODED_FAIL_PCT = 50.0
_ENCODED_WARN_PCT = 80.0
_QUEUE_WARN = 1000


# ── helpers ──────────────────────────────────────────────────────────────────


def _sqlite_scalar(conn: sqlite3.Connection, sql: str, default: Any = 0) -> Any:
    try:
        row = conn.execute(sql).fetchone()
        return row[0] if row else default
    except sqlite3.OperationalError:
        return default


def _db_counts(db: Path) -> dict[str, int]:
    """Возвращает total_messages, total_chats, total_chunks, encoded_chunks."""
    conn = sqlite3.connect(str(db))
    try:
        total_msgs = _sqlite_scalar(conn, "SELECT COUNT(*) FROM messages")
        total_chats = _sqlite_scalar(conn, "SELECT COUNT(DISTINCT chat_id) FROM messages")
        # Поддержка v2-схемы (chunks) и legacy (memory_chunks)
        total_chunks = _sqlite_scalar(conn, "SELECT COUNT(*) FROM chunks")
        if total_chunks == 0:
            total_chunks = _sqlite_scalar(conn, "SELECT COUNT(*) FROM memory_chunks")
        # sqlite-vec хранит rowids в vec_chunks_rowids; legacy — колонка embedding
        encoded = _sqlite_scalar(conn, "SELECT COUNT(*) FROM vec_chunks_rowids")
        if encoded == 0:
            encoded = _sqlite_scalar(
                conn,
                "SELECT COUNT(*) FROM memory_chunks WHERE embedding IS NOT NULL",
            )
        return {
            "total_messages": int(total_msgs),
            "total_chats": int(total_chats),
            "total_chunks": int(total_chunks),
            "encoded_chunks": int(encoded),
        }
    finally:
        conn.close()


def _db_integrity(db: Path) -> str:
    """Возвращает результат PRAGMA integrity_check (первая строка)."""
    try:
        conn = sqlite3.connect(str(db))
        result = conn.execute("PRAGMA integrity_check;").fetchone()
        conn.close()
        return str(result[0]) if result else "error"
    except Exception as exc:  # noqa: BLE001
        return f"error: {exc}"


def _top_chats(db: Path, limit: int = 6) -> list[dict[str, Any]]:
    try:
        conn = sqlite3.connect(str(db))
        rows = conn.execute(
            "SELECT chat_id, COUNT(*) AS cnt FROM messages "
            "GROUP BY chat_id ORDER BY cnt DESC LIMIT ?",
            (limit,),
        ).fetchall()
        conn.close()
        return [{"chat_id": r[0], "count": r[1]} for r in rows]
    except Exception:  # noqa: BLE001
        return []


async def _panel_indexer_stats() -> dict[str, Any]:
    """GET /api/memory/indexer с таймаутом 3 с."""
    try:
        import httpx

        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"{_PANEL_URL}/api/memory/indexer")
            return resp.json()
    except Exception:  # noqa: BLE001
        return {}


async def _mcp_reachable() -> bool:
    """Проверяет TCP-доступность MCP yung-nagato (:8011)."""
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection("127.0.0.1", _MCP_PORT), timeout=2.0
        )
        writer.close()
        await writer.wait_closed()
        return True
    except Exception:  # noqa: BLE001
        return False


# ── Public API ────────────────────────────────────────────────────────────────


async def run_diagnostics(db_path: Path | None = None) -> dict[str, Any]:
    """Запускает все 6 проверок Memory Layer. Без side-effects.

    Returns:
        Словарь с полями:
          - ok: bool — True если все проверки прошли
          - checks: dict[str, CheckResult]
          - summary: str — краткое резюме
          - ts: float — unix timestamp
    """
    db = db_path or _DEFAULT_DB
    checks: dict[str, Any] = {}
    ts = time.time()

    # 1. archive.db — наличие и размер
    if not db.exists():
        return {
            "ok": False,
            "checks": {
                "db_exists": {
                    "status": "fail",
                    "message": f"archive.db не найден: {db}",
                }
            },
            "summary": "archive.db отсутствует — Memory Layer недоступен",
            "ts": ts,
        }

    size_bytes = db.stat().st_size
    size_mb = round(size_bytes / 1024 / 1024, 2)
    size_gb = size_bytes / (1024**3)
    size_status = "ok"
    size_msg = f"{size_mb} МБ"
    if size_bytes < 1024:
        size_status = "fail"
        size_msg = f"Слишком мал ({size_bytes} байт) — возможно пустой"
    elif size_gb > _SIZE_WARN_GB:
        size_status = "warn"
        size_msg = f"{size_mb} МБ > {_SIZE_WARN_GB} ГБ — рассмотрите VACUUM"
    checks["db_size"] = {
        "status": size_status,
        "size_bytes": size_bytes,
        "size_mb": size_mb,
        "message": size_msg,
    }

    # 2. Целостность
    integrity = _db_integrity(db)
    checks["integrity"] = {
        "status": "ok" if integrity.lower() == "ok" else "fail",
        "result": integrity,
        "message": "Целостность OK" if integrity.lower() == "ok" else f"Ошибка: {integrity}",
    }

    # 3. Счётчики
    counts = _db_counts(db)
    counts_status = "ok"
    counts_msg = (
        f"messages={counts['total_messages']}, "
        f"chats={counts['total_chats']}, "
        f"chunks={counts['total_chunks']}"
    )
    if counts["total_messages"] == 0:
        counts_status = "warn"
        counts_msg = "Нет сообщений — Memory Layer пуст"
    checks["counts"] = {
        "status": counts_status,
        "message": counts_msg,
        **counts,
    }

    # 4. Encoded ratio
    total_chunks = counts["total_chunks"]
    encoded = counts["encoded_chunks"]
    if total_chunks > 0:
        ratio = round(100.0 * encoded / total_chunks, 1)
        if ratio < _ENCODED_FAIL_PCT:
            enc_status = "fail"
            enc_msg = f"{ratio}% < {_ENCODED_FAIL_PCT}% — семантический поиск деградирован"
        elif ratio < _ENCODED_WARN_PCT:
            enc_status = "warn"
            enc_msg = f"{ratio}% < {_ENCODED_WARN_PCT}% — рекомендуется backfill"
        else:
            enc_status = "ok"
            enc_msg = f"{ratio}% — в норме"
    else:
        ratio = 0.0
        enc_status = "skip"
        enc_msg = "Chunks отсутствуют — ratio не вычисляется"
    checks["encoded_ratio"] = {
        "status": enc_status,
        "encoded_chunks": encoded,
        "total_chunks": total_chunks,
        "ratio_pct": ratio,
        "message": enc_msg,
    }

    # 5. Indexer queue depth
    indexer = await _panel_indexer_stats()
    if not indexer:
        checks["indexer"] = {
            "status": "skip",
            "message": f"Panel API ({_PANEL_URL}) недоступен",
        }
    else:
        q_size = indexer.get("queue_size", 0) or 0
        is_running = indexer.get("is_running", False)
        idx_status = "ok"
        idx_msg = f"running={is_running}, queue={q_size}"
        if not is_running:
            idx_status = "warn"
            idx_msg = "Indexer не запущен"
        elif q_size > _QUEUE_WARN:
            idx_status = "warn"
            idx_msg = f"Очередь indexer'а: {q_size} > {_QUEUE_WARN}"
        checks["indexer"] = {
            "status": idx_status,
            "is_running": is_running,
            "queue_size": q_size,
            "processed_total": indexer.get("processed_total", 0),
            "message": idx_msg,
        }

    # 6. MCP memory_search reachability
    mcp_ok = await _mcp_reachable()
    checks["mcp_reachable"] = {
        "status": "ok" if mcp_ok else "fail",
        "port": _MCP_PORT,
        "message": f"MCP yung-nagato :{_MCP_PORT} {'доступен' if mcp_ok else 'недоступен'}",
    }

    # Топ чатов (бонус, всегда собираем)
    checks["top_chats"] = {
        "status": "ok",
        "data": _top_chats(db),
    }

    # Финальный ok = нет ни одного "fail"
    failed = [k for k, v in checks.items() if isinstance(v, dict) and v.get("status") == "fail"]
    warnings = [k for k, v in checks.items() if isinstance(v, dict) and v.get("status") == "warn"]
    overall_ok = len(failed) == 0
    summary_parts = []
    if failed:
        summary_parts.append(f"FAIL: {', '.join(failed)}")
    if warnings:
        summary_parts.append(f"WARN: {', '.join(warnings)}")
    if not summary_parts:
        summary_parts.append("Все проверки в норме")

    return {
        "ok": overall_ok,
        "checks": checks,
        "failed": failed,
        "warnings": warnings,
        "summary": " | ".join(summary_parts),
        "ts": ts,
        "db_path": str(db),
    }


async def run_repairs(
    checks: dict[str, Any] | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    """Выполняет авто-ремонт по результатам диагностики.

    Если checks не передан, сначала запускает run_diagnostics().

    Returns:
        Словарь с полями repairs: list[dict] — результаты каждого действия.
    """
    db = db_path or _DEFAULT_DB
    if checks is None:
        diag = await run_diagnostics(db_path=db)
        checks = diag.get("checks", {})

    repairs: list[dict[str, Any]] = []
    ts = time.time()

    # Ремонт 1: WAL checkpoint — всегда при наличии БД
    try:
        conn = sqlite3.connect(str(db))
        row = conn.execute("PRAGMA wal_checkpoint(TRUNCATE);").fetchone()
        conn.close()
        repairs.append(
            {
                "action": "wal_checkpoint",
                "status": "ok",
                "result": list(row) if row else None,
                "message": "WAL checkpoint(TRUNCATE) выполнен",
            }
        )
    except Exception as exc:  # noqa: BLE001
        repairs.append(
            {
                "action": "wal_checkpoint",
                "status": "fail",
                "message": str(exc),
            }
        )

    # Ремонт 2: Backfill embeddings если encoded ratio < 50%
    enc_check = checks.get("encoded_ratio", {})
    if enc_check.get("status") == "fail":
        backfill_script = (
            Path(__file__).parent.parent.parent / "scripts" / "encode_memory_phase2.py"
        )
        if backfill_script.exists():
            python = _find_python()
            try:
                result = subprocess.run(
                    [python, str(backfill_script), "--limit", "5000"],
                    capture_output=True,
                    text=True,
                    timeout=300,
                )
                repairs.append(
                    {
                        "action": "backfill_embeddings",
                        "status": "ok" if result.returncode == 0 else "fail",
                        "returncode": result.returncode,
                        "stdout_tail": result.stdout[-500:] if result.stdout else "",
                        "message": "encode_memory_phase2.py --limit 5000 выполнен",
                    }
                )
            except subprocess.TimeoutExpired:
                repairs.append(
                    {
                        "action": "backfill_embeddings",
                        "status": "timeout",
                        "message": "encode_memory_phase2.py завершён по таймауту (300с)",
                    }
                )
            except Exception as exc:  # noqa: BLE001
                repairs.append(
                    {
                        "action": "backfill_embeddings",
                        "status": "fail",
                        "message": str(exc),
                    }
                )
        else:
            repairs.append(
                {
                    "action": "backfill_embeddings",
                    "status": "skip",
                    "message": f"Скрипт не найден: {backfill_script}",
                }
            )

    # Ремонт 3: Перезапуск MCP yung-nagato если недоступен
    mcp_check = checks.get("mcp_reachable", {})
    if mcp_check.get("status") == "fail":
        import os

        uid = os.getuid()
        label = "com.krab.mcp-yung-nagato"
        try:
            result = subprocess.run(
                ["launchctl", "kickstart", "-k", f"gui/{uid}/{label}"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            repairs.append(
                {
                    "action": "restart_mcp_yung_nagato",
                    "status": "ok" if result.returncode == 0 else "fail",
                    "returncode": result.returncode,
                    "message": f"launchctl kickstart {label}: rc={result.returncode}",
                }
            )
        except Exception as exc:  # noqa: BLE001
            repairs.append(
                {
                    "action": "restart_mcp_yung_nagato",
                    "status": "fail",
                    "message": str(exc),
                }
            )

    repairs_ok = all(r.get("status") in ("ok", "skip") for r in repairs)
    return {
        "ok": repairs_ok,
        "repairs": repairs,
        "ts": ts,
        "db_path": str(db),
    }


def _find_python() -> str:
    """Возвращает путь к Python: venv/bin/python или системный."""
    venv_py = Path(__file__).parent.parent.parent / "venv" / "bin" / "python"
    if venv_py.exists():
        return str(venv_py)
    return sys.executable
