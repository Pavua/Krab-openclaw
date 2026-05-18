# -*- coding: utf-8 -*-
"""Wave 40-A: тесты для nightly_self_audit.py.

Паттерн: mock внешних зависимостей (psutil, launchctl, sqlite3,
urllib.request, shutil, Path, subprocess).
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, mock_open, patch

import pytest

from src.core.nightly_self_audit import (
    AuditFinding,
    audit_database_integrity,
    audit_disk_space,
    audit_inbox_bloat,
    audit_oauth_tokens,
    audit_process_health,
    audit_zombie_escalations,
    run_full_audit,
)

# ---------------------------------------------------------------------------
# AuditFinding helpers
# ---------------------------------------------------------------------------


def test_audit_finding_ok_markdown():
    """ok → ✅ в markdown."""
    f = AuditFinding("Disk", "ok", "все хорошо")
    md = f.to_markdown()
    assert "✅" in md
    assert "*Disk*" in md
    assert "все хорошо" in md


def test_audit_finding_warn_markdown():
    """warn → ⚠️ + detail если есть."""
    f = AuditFinding("DB integrity", "warn", "что-то не то", "детали здесь")
    md = f.to_markdown()
    assert "⚠️" in md
    assert "детали здесь" in md


def test_audit_finding_critical_markdown():
    """critical → 🔴."""
    f = AuditFinding("Process", "critical", "упал!")
    assert "🔴" in f.to_markdown()


# ---------------------------------------------------------------------------
# 1. audit_process_health — krab running → ok
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_health_krab_running():
    """Если krab процесс найден, результат ok или warn (зависит от daemons)."""
    mock_proc = MagicMock()
    mock_proc.info = {"cmdline": ["/usr/bin/python", "src/main.py"]}
    mock_proc.pid = 12345
    mock_proc.create_time.return_value = time.time() - 3600  # 1h uptime

    mock_psutil = MagicMock()
    mock_psutil.process_iter.return_value = [mock_proc]
    mock_psutil.Process.return_value = mock_proc
    mock_psutil.NoSuchProcess = Exception
    mock_psutil.AccessDenied = Exception

    # launchctl возвращает 3 активных daemon'а, включая ai.krab.core с pid
    launchctl_out = "12345\t0\tai.krab.core\n12346\t0\tai.krab.inbox-watcher\n12347\t0\tai.krab.gateway-watchdog\n"
    mock_result = MagicMock()
    mock_result.stdout = launchctl_out
    mock_result.returncode = 0

    with patch.dict("sys.modules", {"psutil": mock_psutil}):
        with patch("subprocess.run", return_value=mock_result):
            finding = await audit_process_health()

    assert finding.status in ("ok", "warn")
    assert "Uptime" in finding.summary


@pytest.mark.asyncio
async def test_process_health_krab_not_running():
    """Если krab процесс не найден (launchctl loaded_idle) → critical."""
    # launchctl говорит: ai.krab.core loaded но без pid (state=loaded_idle)
    launchctl_out = "-\t0\tai.krab.core\n12346\t0\tai.krab.inbox-watcher\n"
    mock_result = MagicMock()
    mock_result.stdout = launchctl_out
    mock_result.returncode = 0

    mock_psutil = MagicMock()
    mock_psutil.process_iter.return_value = []
    mock_psutil.NoSuchProcess = Exception
    mock_psutil.AccessDenied = Exception

    with patch.dict("sys.modules", {"psutil": mock_psutil}):
        with patch("subprocess.run", return_value=mock_result):
            finding = await audit_process_health()

    assert finding.status == "critical"
    assert "not running" in finding.summary
    assert "loaded_idle" in finding.summary


# ---------------------------------------------------------------------------
# 2. audit_database_integrity — clean DBs → ok
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_db_integrity_all_ok(tmp_path):
    """Если все DBs integrity_check = ok → finding ok."""
    # Создаём валидную SQLite DB
    db = tmp_path / "test.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE t (x INTEGER)")
    conn.close()

    with patch(
        "src.core.nightly_self_audit.Path.home",
        return_value=tmp_path,
    ):
        # Патчим список DB путей через замену Path.home() — проще напрямую
        # Используем patch на уровне функции через sqlite3.connect
        with patch("sqlite3.connect") as mock_conn:
            mock_cursor = MagicMock()
            mock_cursor.fetchone.return_value = ("ok",)
            mock_conn.return_value.__enter__ = lambda s: s
            mock_conn.return_value.__exit__ = MagicMock(return_value=False)
            mock_conn.return_value.execute.return_value = mock_cursor

            # Мокаем Path.exists() чтобы DB пути "существовали"
            with patch.object(Path, "exists", return_value=True):
                with patch(
                    "sqlite3.connect",
                    return_value=MagicMock(
                        execute=lambda q: MagicMock(fetchone=lambda: ("ok",)),
                        close=lambda: None,
                    ),
                ):
                    finding = await audit_database_integrity()

    # С реальными DBs или моком — результат должен быть ok или warn (нет файлов)
    assert finding.dimension == "DB integrity"
    assert finding.status in ("ok", "warn")


@pytest.mark.asyncio
async def test_db_integrity_malformed():
    """Если integrity_check возвращает не ok → critical."""
    with patch.object(Path, "exists", return_value=True):
        with patch(
            "sqlite3.connect",
            return_value=MagicMock(
                execute=lambda q: MagicMock(fetchone=lambda: ("malformed pages 5",)),
                close=lambda: None,
            ),
        ):
            finding = await audit_database_integrity()

    assert finding.status == "critical"
    assert "проблем" in finding.summary


# ---------------------------------------------------------------------------
# 3. audit_disk_space — три сценария
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_disk_space_ok():
    """<85% → ok."""
    with patch("shutil.disk_usage", return_value=(1000, 700, 300)):  # 70%
        finding = await audit_disk_space()
    assert finding.status == "ok"
    assert "70%" in finding.summary


@pytest.mark.asyncio
async def test_disk_space_warn():
    """85-95% → warn."""
    with patch("shutil.disk_usage", return_value=(1000, 900, 100)):  # 90%
        finding = await audit_disk_space()
    assert finding.status == "warn"
    assert "90%" in finding.summary


@pytest.mark.asyncio
async def test_disk_space_critical():
    """>95% → critical."""
    with patch("shutil.disk_usage", return_value=(1000, 970, 30)):  # 97%
        finding = await audit_disk_space()
    assert finding.status == "critical"
    assert "97%" in finding.summary


# ---------------------------------------------------------------------------
# 4. audit_oauth_tokens — valid → ok, expired → warn (graceful)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_oauth_valid():
    """Токен действителен (expires через 48h) → ok."""
    future_ts_ms = (time.time() + 48 * 3600) * 1000
    creds_data = json.dumps({"expiry_date": int(future_ts_ms)})

    with patch.object(Path, "exists", return_value=True):
        with patch.object(Path, "read_text", return_value=creds_data):
            finding = await audit_oauth_tokens()

    assert finding.status == "ok"


@pytest.mark.asyncio
async def test_oauth_expired_graceful():
    """Токен истёк И daemon resync завис → warn (graceful, critical-уровня нет)."""
    expired_ts_ms = (time.time() - 2 * 3600) * 1000  # 2h назад
    creds_data = json.dumps({"expiry_date": int(expired_ts_ms)})

    with patch.object(Path, "exists", return_value=True):
        with patch.object(Path, "read_text", return_value=creds_data):
            # Daemon последний раз обновлял давно → warn должен сработать
            with patch(
                "src.core.nightly_self_audit._last_oauth_force_refresh_age_sec",
                return_value=99 * 60,
            ):
                finding = await audit_oauth_tokens()

    # Должен быть warn, не critical
    assert finding.status == "warn"
    assert "истёк" in finding.summary


# ---------------------------------------------------------------------------
# Wave 50-B sawtooth aware OAuth checks (S61 W1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_oauth_sawtooth_dip_with_alive_daemon_is_ok():
    """expiry < 60min, но daemon refresh < 30min назад → НЕ warn (sawtooth норма)."""
    # Token expires через 18 мин (типичный sawtooth dip перед force-refresh)
    expiring_ts_ms = (time.time() + 18 * 60) * 1000
    creds_data = json.dumps({"expiry_date": int(expiring_ts_ms)})

    with patch.object(Path, "exists", return_value=True):
        with patch.object(Path, "read_text", return_value=creds_data):
            # Daemon refresh 5 мин назад — alive
            with patch(
                "src.core.nightly_self_audit._last_oauth_force_refresh_age_sec",
                return_value=5 * 60,
            ):
                finding = await audit_oauth_tokens()

    assert finding.status == "ok"
    assert "истекает" not in finding.summary


@pytest.mark.asyncio
async def test_oauth_expiring_warn_when_daemon_stuck():
    """expiry < 60min И daemon refresh > 30min назад → warn (daemon завис)."""
    expiring_ts_ms = (time.time() + 18 * 60) * 1000
    creds_data = json.dumps({"expiry_date": int(expiring_ts_ms)})

    with patch.object(Path, "exists", return_value=True):
        with patch.object(Path, "read_text", return_value=creds_data):
            # Daemon последний force-refresh 90мин назад — stuck
            with patch(
                "src.core.nightly_self_audit._last_oauth_force_refresh_age_sec",
                return_value=90 * 60,
            ):
                finding = await audit_oauth_tokens()

    assert finding.status == "warn"
    assert "daemon" in finding.summary.lower()


@pytest.mark.asyncio
async def test_oauth_expired_with_alive_daemon_is_ok():
    """expired (-30 min), но daemon refresh свежий → sawtooth норма, не warn."""
    expired_ts_ms = (time.time() - 30 * 60) * 1000
    creds_data = json.dumps({"expiry_date": int(expired_ts_ms)})

    with patch.object(Path, "exists", return_value=True):
        with patch.object(Path, "read_text", return_value=creds_data):
            with patch(
                "src.core.nightly_self_audit._last_oauth_force_refresh_age_sec",
                return_value=10 * 60,
            ):
                finding = await audit_oauth_tokens()

    assert finding.status == "ok"


def test_last_oauth_force_refresh_age_parses_log(tmp_path, monkeypatch):
    """Helper парсит timestamp последнего force-refresh success из лога."""
    from src.core import nightly_self_audit as nsa

    log = tmp_path / "oauth_resync.log"
    # Свежий timestamp 2 мин назад
    ts = datetime.fromtimestamp(time.time() - 120, tz=timezone.utc).isoformat()
    log.write_text(
        f"[{ts}] already synced — no-op (expiry_in_min=-45.0)\n"
        f"[{ts}] token expired (expiry_in_min=-60.0) — attempting force-refresh\n"
        f"[{ts}] force-refresh success: new expiry_in_min=60.0\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(nsa, "_OAUTH_RESYNC_LOG_CANDIDATES", (log,))

    age = nsa._last_oauth_force_refresh_age_sec()
    assert age is not None
    assert 0 <= age <= 300  # parsed свежим (< 5 мин)


def test_last_oauth_force_refresh_age_missing_log(monkeypatch):
    """Helper возвращает None если ни одного лог-файла нет."""
    from src.core import nightly_self_audit as nsa

    monkeypatch.setattr(nsa, "_OAUTH_RESYNC_LOG_CANDIDATES", (Path("/nonexistent/krab/log"),))
    assert nsa._last_oauth_force_refresh_age_sec() is None


# ---------------------------------------------------------------------------
# 5. run_full_audit — агрегация + markdown
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_full_audit_aggregates():
    """run_full_audit возвращает findings + counts + markdown report."""
    # Мокаем все 8 проверок как ok
    ok_finding = AuditFinding("Test", "ok", "всё хорошо")

    with patch(
        "src.core.nightly_self_audit.audit_process_health",
        new=AsyncMock(return_value=ok_finding),
    ):
        with patch(
            "src.core.nightly_self_audit.audit_database_integrity",
            new=AsyncMock(return_value=ok_finding),
        ):
            with patch(
                "src.core.nightly_self_audit.audit_bypass_perf_trend",
                new=AsyncMock(return_value=ok_finding),
            ):
                with patch(
                    "src.core.nightly_self_audit.audit_memory_trend",
                    new=AsyncMock(return_value=ok_finding),
                ):
                    with patch(
                        "src.core.nightly_self_audit.audit_disk_space",
                        new=AsyncMock(return_value=ok_finding),
                    ):
                        with patch(
                            "src.core.nightly_self_audit.audit_inbox_bloat",
                            new=AsyncMock(return_value=ok_finding),
                        ):
                            with patch(
                                "src.core.nightly_self_audit.audit_oauth_tokens",
                                new=AsyncMock(return_value=ok_finding),
                            ):
                                with patch(
                                    "src.core.nightly_self_audit.audit_zombie_escalations",
                                    new=AsyncMock(return_value=ok_finding),
                                ):
                                    result = await run_full_audit()

    assert result["ok"] is True
    assert result["counts"]["ok"] == 8
    assert result["counts"]["warn"] == 0
    assert result["counts"]["critical"] == 0
    assert "Krab Nightly Audit" in result["report"]
    assert len(result["findings"]) == 8


@pytest.mark.asyncio
async def test_run_full_audit_no_telegram_if_all_ok():
    """Если all ok → Telegram НЕ отправляется (quiet mode)."""
    ok_finding = AuditFinding("Test", "ok", "тихо")

    sent_calls = []

    def mock_urlopen(req, timeout=None):
        sent_calls.append(req)
        return MagicMock(
            __enter__=lambda s: s, __exit__=MagicMock(return_value=False), read=lambda: b"{}"
        )

    with patch(
        "src.core.nightly_self_audit.audit_process_health",
        new=AsyncMock(return_value=ok_finding),
    ):
        with patch(
            "src.core.nightly_self_audit.audit_database_integrity",
            new=AsyncMock(return_value=ok_finding),
        ):
            with patch(
                "src.core.nightly_self_audit.audit_bypass_perf_trend",
                new=AsyncMock(return_value=ok_finding),
            ):
                with patch(
                    "src.core.nightly_self_audit.audit_memory_trend",
                    new=AsyncMock(return_value=ok_finding),
                ):
                    with patch(
                        "src.core.nightly_self_audit.audit_disk_space",
                        new=AsyncMock(return_value=ok_finding),
                    ):
                        with patch(
                            "src.core.nightly_self_audit.audit_inbox_bloat",
                            new=AsyncMock(return_value=ok_finding),
                        ):
                            with patch(
                                "src.core.nightly_self_audit.audit_oauth_tokens",
                                new=AsyncMock(return_value=ok_finding),
                            ):
                                with patch(
                                    "src.core.nightly_self_audit.audit_zombie_escalations",
                                    new=AsyncMock(return_value=ok_finding),
                                ):
                                    with patch(
                                        "urllib.request.urlopen",
                                        side_effect=mock_urlopen,
                                    ):
                                        result = await run_full_audit()

    # /api/notify не должен быть вызван
    notify_calls = [c for c in sent_calls if hasattr(c, "full_url") and "notify" in str(c)]
    assert result["has_issues"] is False
    assert len(notify_calls) == 0


@pytest.mark.asyncio
async def test_run_full_audit_sends_telegram_if_warn():
    """Если есть warn → Telegram отправляется."""
    warn_finding = AuditFinding("Disk", "warn", "90% использовано")
    ok_finding = AuditFinding("Test", "ok", "ok")

    notify_called = []

    class MockResponse:
        def read(self):
            return b"{}"

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def mock_urlopen(req, timeout=None):
        url_str = req.full_url if hasattr(req, "full_url") else str(req)
        notify_called.append(url_str)
        return MockResponse()

    with patch(
        "src.core.nightly_self_audit.audit_process_health",
        new=AsyncMock(return_value=ok_finding),
    ):
        with patch(
            "src.core.nightly_self_audit.audit_database_integrity",
            new=AsyncMock(return_value=ok_finding),
        ):
            with patch(
                "src.core.nightly_self_audit.audit_bypass_perf_trend",
                new=AsyncMock(return_value=ok_finding),
            ):
                with patch(
                    "src.core.nightly_self_audit.audit_memory_trend",
                    new=AsyncMock(return_value=ok_finding),
                ):
                    with patch(
                        "src.core.nightly_self_audit.audit_disk_space",
                        new=AsyncMock(return_value=warn_finding),
                    ):
                        with patch(
                            "src.core.nightly_self_audit.audit_inbox_bloat",
                            new=AsyncMock(return_value=ok_finding),
                        ):
                            with patch(
                                "src.core.nightly_self_audit.audit_oauth_tokens",
                                new=AsyncMock(return_value=ok_finding),
                            ):
                                with patch(
                                    "src.core.nightly_self_audit.audit_zombie_escalations",
                                    new=AsyncMock(return_value=ok_finding),
                                ):
                                    with patch(
                                        "urllib.request.urlopen",
                                        side_effect=mock_urlopen,
                                    ):
                                        result = await run_full_audit()

    assert result["has_issues"] is True
    assert result["counts"]["warn"] >= 1
    # urlopen должен был вызваться для /api/notify
    assert any("notify" in url for url in notify_called)
