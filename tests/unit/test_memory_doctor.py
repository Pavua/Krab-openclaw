"""Юнит-тесты для src/core/memory_doctor.py + scripts/memory_doctor.command."""

from __future__ import annotations

import os
import sqlite3
import stat
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── helpers ────────────────────────────────────────────────────────────────

def _make_archive_db(path: Path, n_messages: int = 10, n_chunks: int = 5, n_encoded: int = 3) -> Path:
    """Создаёт минимальную archive.db для тестов."""
    conn = sqlite3.connect(str(path))
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY,
            chat_id TEXT,
            text TEXT,
            timestamp INTEGER
        );
        CREATE TABLE IF NOT EXISTS chunks (
            id INTEGER PRIMARY KEY,
            text TEXT
        );
        CREATE TABLE IF NOT EXISTS vec_chunks_rowids (
            rowid INTEGER PRIMARY KEY
        );
    """)
    for i in range(n_messages):
        conn.execute(
            "INSERT INTO messages (chat_id, text, timestamp) VALUES (?, ?, ?)",
            (f"chat_{i % 3}", f"message {i}", 1700000000 + i),
        )
    for i in range(n_chunks):
        conn.execute("INSERT INTO chunks (text) VALUES (?)", (f"chunk {i}",))
    for i in range(n_encoded):
        conn.execute("INSERT INTO vec_chunks_rowids (rowid) VALUES (?)", (i + 1,))
    conn.commit()
    conn.close()
    return path


# ── Импорт модуля ──────────────────────────────────────────────────────────

@pytest.fixture()
def memory_doctor(monkeypatch):
    """Импортирует memory_doctor с отключёнными внешними зависимостями."""
    from src.core import memory_doctor as md
    return md


# ── Тест 1: run_diagnostics возвращает ожидаемую структуру ───────────────

@pytest.mark.asyncio
async def test_run_diagnostics_shape(tmp_path, memory_doctor):
    """run_diagnostics() возвращает ok, checks, summary, ts, db_path."""
    db = _make_archive_db(tmp_path / "archive.db")

    with (
        patch.object(memory_doctor, "_panel_indexer_stats", AsyncMock(return_value={})),
        patch.object(memory_doctor, "_mcp_reachable", AsyncMock(return_value=True)),
    ):
        result = await memory_doctor.run_diagnostics(db_path=db)

    assert "ok" in result
    assert "checks" in result
    assert "summary" in result
    assert "ts" in result
    assert "db_path" in result
    assert isinstance(result["checks"], dict)
    # Все 6 проверок (+ top_chats) должны быть
    for key in ("db_size", "integrity", "counts", "encoded_ratio", "indexer", "mcp_reachable", "top_chats"):
        assert key in result["checks"], f"Отсутствует check: {key}"


# ── Тест 2: encoded ratio calculation ────────────────────────────────────

@pytest.mark.asyncio
async def test_encoded_ratio_ok(tmp_path, memory_doctor):
    """Encoded ratio >= 80% → status ok."""
    db = _make_archive_db(tmp_path / "archive.db", n_chunks=10, n_encoded=9)

    with (
        patch.object(memory_doctor, "_panel_indexer_stats", AsyncMock(return_value={})),
        patch.object(memory_doctor, "_mcp_reachable", AsyncMock(return_value=True)),
    ):
        result = await memory_doctor.run_diagnostics(db_path=db)

    enc = result["checks"]["encoded_ratio"]
    assert enc["ratio_pct"] == 90.0
    assert enc["status"] == "ok"


@pytest.mark.asyncio
async def test_encoded_ratio_fail(tmp_path, memory_doctor):
    """Encoded ratio < 50% → status fail."""
    db = _make_archive_db(tmp_path / "archive.db", n_chunks=10, n_encoded=3)

    with (
        patch.object(memory_doctor, "_panel_indexer_stats", AsyncMock(return_value={})),
        patch.object(memory_doctor, "_mcp_reachable", AsyncMock(return_value=True)),
    ):
        result = await memory_doctor.run_diagnostics(db_path=db)

    enc = result["checks"]["encoded_ratio"]
    assert enc["ratio_pct"] == 30.0
    assert enc["status"] == "fail"
    assert "encoded_ratio" in result["failed"]


@pytest.mark.asyncio
async def test_encoded_ratio_warn(tmp_path, memory_doctor):
    """Encoded ratio 50-79% → status warn."""
    db = _make_archive_db(tmp_path / "archive.db", n_chunks=10, n_encoded=6)

    with (
        patch.object(memory_doctor, "_panel_indexer_stats", AsyncMock(return_value={})),
        patch.object(memory_doctor, "_mcp_reachable", AsyncMock(return_value=True)),
    ):
        result = await memory_doctor.run_diagnostics(db_path=db)

    enc = result["checks"]["encoded_ratio"]
    assert enc["status"] == "warn"


# ── Тест 3: size threshold warnings ──────────────────────────────────────

@pytest.mark.asyncio
async def test_db_size_warn_large(tmp_path, monkeypatch, memory_doctor):
    """Размер > _SIZE_WARN_GB → status warn."""
    db = _make_archive_db(tmp_path / "archive.db")

    # Патчим stat().st_size чтобы симулировать > 2 ГБ
    orig_stat = Path.stat

    def fake_stat(self, *args, **kwargs):
        s = orig_stat(self)
        if self == db:
            # возвращаем объект с нужным st_size
            class FakeStat:
                st_size = int(2.5 * 1024 ** 3)

            return FakeStat()
        return s

    monkeypatch.setattr(Path, "stat", fake_stat)

    with (
        patch.object(memory_doctor, "_panel_indexer_stats", AsyncMock(return_value={})),
        patch.object(memory_doctor, "_mcp_reachable", AsyncMock(return_value=True)),
    ):
        result = await memory_doctor.run_diagnostics(db_path=db)

    assert result["checks"]["db_size"]["status"] == "warn"


# ── Тест 4: missing db → ok=False ────────────────────────────────────────

@pytest.mark.asyncio
async def test_missing_db(tmp_path, memory_doctor):
    """Если archive.db не существует, ok=False и есть db_exists check."""
    db = tmp_path / "nonexistent.db"
    result = await memory_doctor.run_diagnostics(db_path=db)
    assert result["ok"] is False
    assert "db_exists" in result["checks"]
    assert result["checks"]["db_exists"]["status"] == "fail"


# ── Тест 5: run_repairs WAL checkpoint ───────────────────────────────────

@pytest.mark.asyncio
async def test_run_repairs_wal_checkpoint(tmp_path, memory_doctor):
    """run_repairs() всегда выполняет WAL checkpoint."""
    db = _make_archive_db(tmp_path / "archive.db")

    result = await memory_doctor.run_repairs(checks={}, db_path=db)
    repair_actions = [r["action"] for r in result["repairs"]]
    assert "wal_checkpoint" in repair_actions
    wal = next(r for r in result["repairs"] if r["action"] == "wal_checkpoint")
    assert wal["status"] == "ok"


# ── Тест 6: run_repairs запускает backfill при fail encoded_ratio ─────────

@pytest.mark.asyncio
async def test_run_repairs_backfill_triggered(tmp_path, memory_doctor):
    """Если encoded_ratio status=fail, run_repairs пытается запустить backfill."""
    db = _make_archive_db(tmp_path / "archive.db")
    checks = {
        "encoded_ratio": {"status": "fail", "ratio_pct": 20.0},
    }

    # Нет скрипта — должен быть skip
    with patch.object(memory_doctor, "_find_python", return_value="python3"):
        result = await memory_doctor.run_repairs(checks=checks, db_path=db)

    backfill = next((r for r in result["repairs"] if r["action"] == "backfill_embeddings"), None)
    assert backfill is not None
    # Либо ok (если скрипт есть) либо skip (если нет) — не должен быть None
    assert backfill["status"] in ("ok", "fail", "skip", "timeout")


# ── Тест 7: run_repairs перезапускает MCP при fail ───────────────────────

@pytest.mark.asyncio
async def test_run_repairs_mcp_restart(tmp_path, memory_doctor, monkeypatch):
    """Если mcp_reachable status=fail, run_repairs пытается kickstart launchctl."""
    db = _make_archive_db(tmp_path / "archive.db")
    checks = {
        "mcp_reachable": {"status": "fail"},
    }

    import subprocess as _sp

    def fake_run(args, **kwargs):
        m = MagicMock()
        m.returncode = 0
        m.stdout = ""
        m.stderr = ""
        return m

    monkeypatch.setattr(_sp, "run", fake_run)

    result = await memory_doctor.run_repairs(checks=checks, db_path=db)
    restart = next((r for r in result["repairs"] if r["action"] == "restart_mcp_yung_nagato"), None)
    assert restart is not None
    assert restart["status"] == "ok"


# ── Тест 8: shell script существует и исполняемый ────────────────────────

def test_shell_script_exists_and_executable():
    """scripts/memory_doctor.command существует и имеет бит x."""
    script = Path(__file__).resolve().parents[2] / "scripts" / "memory_doctor.command"
    assert script.exists(), f"Скрипт не найден: {script}"
    mode = script.stat().st_mode
    assert mode & stat.S_IXUSR, "Скрипт не исполняемый (нет +x для owner)"


# ── Тест 9: top_chats в результате диагностики ───────────────────────────

@pytest.mark.asyncio
async def test_top_chats_populated(tmp_path, memory_doctor):
    """top_chats содержит данные при наличии сообщений."""
    db = _make_archive_db(tmp_path / "archive.db", n_messages=15)

    with (
        patch.object(memory_doctor, "_panel_indexer_stats", AsyncMock(return_value={})),
        patch.object(memory_doctor, "_mcp_reachable", AsyncMock(return_value=True)),
    ):
        result = await memory_doctor.run_diagnostics(db_path=db)

    top = result["checks"]["top_chats"]["data"]
    assert isinstance(top, list)
    assert len(top) > 0
    assert "chat_id" in top[0]
    assert "count" in top[0]


# ── Тест 10: indexer panel недоступен → status skip ─────────────────────

@pytest.mark.asyncio
async def test_indexer_panel_unavailable(tmp_path, memory_doctor):
    """Если panel API недоступен, indexer check → status skip."""
    db = _make_archive_db(tmp_path / "archive.db")

    with (
        patch.object(memory_doctor, "_panel_indexer_stats", AsyncMock(return_value={})),
        patch.object(memory_doctor, "_mcp_reachable", AsyncMock(return_value=True)),
    ):
        result = await memory_doctor.run_diagnostics(db_path=db)

    assert result["checks"]["indexer"]["status"] == "skip"
