# -*- coding: utf-8 -*-
"""
Тесты _check_archive_db_size() в ProactiveWatchService.

Проверяем:
1. Нет алерта ниже warning threshold.
2. Warning при 500+ MB.
3. Critical при 1 GB+.
4. Cooldown предотвращает повторный алерт в течение 12 часов.
5. Алерт снова срабатывает после cooldown.
6. Файл отсутствует → нет алерта (graceful).
"""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.core.proactive_watch import ProactiveWatchService

# --------------------------------------------------------------------------- #
# Вспомогательные fixture                                                      #
# --------------------------------------------------------------------------- #


def _make_service(tmp_path: Path) -> ProactiveWatchService:
    """Возвращает service с изолированным state в tmp_path."""
    return ProactiveWatchService(state_path=tmp_path / "state.json")


def _make_db(tmp_path: Path, size_mb: float) -> Path:
    """Создаёт фейковый archive.db заданного размера."""
    db = tmp_path / "archive.db"
    db.write_bytes(b"\x00" * int(size_mb * 1024 * 1024))
    return db


def _mock_inbox_upsert(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """Патчит inbox_service.upsert_item и возвращает список вызовов."""
    calls: list[dict] = []

    import src.core.proactive_watch as pw_mod

    mock_inbox = MagicMock()
    mock_inbox.upsert_item.side_effect = lambda **kwargs: calls.append(kwargs)
    mock_inbox.build_identity.return_value = {}
    monkeypatch.setattr(pw_mod, "inbox_service", mock_inbox)
    return calls


# --------------------------------------------------------------------------- #
# Тесты                                                                        #
# --------------------------------------------------------------------------- #


def test_no_alert_under_threshold(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Archive < warn threshold → никакого алерта."""
    db = _make_db(tmp_path, size_mb=100)
    calls = _mock_inbox_upsert(monkeypatch)
    service = _make_service(tmp_path)

    monkeypatch.setenv("ARCHIVE_DB_PATH", str(db))
    monkeypatch.setenv("ARCHIVE_DB_WARN_MB", "500")
    monkeypatch.setenv("ARCHIVE_DB_CRIT_MB", "1024")

    result = service._check_archive_db_size()

    assert result is False
    assert calls == []


def test_warn_alert_triggered(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """500 MB+ → warning alert пишется в inbox."""
    db = _make_db(tmp_path, size_mb=600)
    calls = _mock_inbox_upsert(monkeypatch)
    service = _make_service(tmp_path)

    monkeypatch.setenv("ARCHIVE_DB_PATH", str(db))
    monkeypatch.setenv("ARCHIVE_DB_WARN_MB", "500")
    monkeypatch.setenv("ARCHIVE_DB_CRIT_MB", "1024")

    result = service._check_archive_db_size()

    assert result is True
    assert len(calls) == 1
    call = calls[0]
    assert call["severity"] == "warning"
    assert call["metadata"]["is_critical"] is False
    assert call["metadata"]["size_mb"] == pytest.approx(600.0, abs=1.0)
    assert "Warning" in call["title"]


def test_crit_alert_triggered(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """1 GB+ → critical alert (severity=error)."""
    db = _make_db(tmp_path, size_mb=1200)
    calls = _mock_inbox_upsert(monkeypatch)
    service = _make_service(tmp_path)

    monkeypatch.setenv("ARCHIVE_DB_PATH", str(db))
    monkeypatch.setenv("ARCHIVE_DB_WARN_MB", "500")
    monkeypatch.setenv("ARCHIVE_DB_CRIT_MB", "1024")

    result = service._check_archive_db_size()

    assert result is True
    assert len(calls) == 1
    call = calls[0]
    assert call["severity"] == "error"
    assert call["metadata"]["is_critical"] is True
    assert "Critical" in call["title"]


def test_cooldown_prevents_spam(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Два вызова подряд в пределах cooldown → только один алерт."""
    db = _make_db(tmp_path, size_mb=600)
    calls = _mock_inbox_upsert(monkeypatch)
    service = _make_service(tmp_path)

    monkeypatch.setenv("ARCHIVE_DB_PATH", str(db))
    monkeypatch.setenv("ARCHIVE_DB_WARN_MB", "500")
    monkeypatch.setenv("ARCHIVE_DB_CRIT_MB", "1024")

    first = service._check_archive_db_size()
    second = service._check_archive_db_size()

    assert first is True
    assert second is False
    assert len(calls) == 1  # только один upsert


def test_cooldown_expires_and_alert_fires_again(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """После истечения cooldown алерт срабатывает снова."""
    db = _make_db(tmp_path, size_mb=600)
    calls = _mock_inbox_upsert(monkeypatch)
    service = _make_service(tmp_path)

    monkeypatch.setenv("ARCHIVE_DB_PATH", str(db))
    monkeypatch.setenv("ARCHIVE_DB_WARN_MB", "500")
    monkeypatch.setenv("ARCHIVE_DB_CRIT_MB", "1024")

    # Первый алерт
    service._check_archive_db_size()
    assert len(calls) == 1

    # Эмулируем прошедшее время — записываем старый timestamp прямо в state
    state = service._load_state()
    # Откручиваем время назад на 13 часов (> 12h cooldown)
    state["archive_db_size_last_alert"] = time.time() - (13 * 3600)
    service._save_state(state)

    second = service._check_archive_db_size()
    assert second is True
    assert len(calls) == 2


def test_no_alert_when_db_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Если archive.db не существует → нет алерта, нет исключения."""
    calls = _mock_inbox_upsert(monkeypatch)
    service = _make_service(tmp_path)

    missing = tmp_path / "nonexistent.db"
    monkeypatch.setenv("ARCHIVE_DB_PATH", str(missing))

    result = service._check_archive_db_size()

    assert result is False
    assert calls == []
