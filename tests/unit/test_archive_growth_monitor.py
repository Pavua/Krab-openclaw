"""Tests for archive growth monitoring."""

from __future__ import annotations

import sqlite3
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from src.core.archive_growth_monitor import (
    GrowthSnapshot,
    append_snapshot_and_check_anomaly,
    growth_summary,
    load_history,
    save_history,
    take_snapshot,
)


@pytest.fixture
def temp_db():
    """Create a temporary archive.db with messages table."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "archive.db"
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, content TEXT)")
        conn.execute("INSERT INTO messages (content) VALUES ('msg1'), ('msg2'), ('msg3')")
        conn.commit()
        conn.close()
        yield db_path


@pytest.fixture
def temp_state():
    """Create a temporary state directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


def test_take_snapshot_with_archive(temp_db):
    """Test snapshot creation with existing archive."""
    with patch("src.core.archive_growth_monitor.ARCHIVE_DB", temp_db):
        snap = take_snapshot()
        assert snap is not None
        assert snap.size_mb > 0
        assert snap.message_count == 3
        assert snap.ts > 0


def test_take_snapshot_missing_db():
    """Test snapshot with missing database returns None."""
    with patch("src.core.archive_growth_monitor.ARCHIVE_DB", Path("/nonexistent/archive.db")):
        snap = take_snapshot()
        assert snap is None


def test_history_persisted_and_loaded(temp_state):
    """Test history is persisted and loaded correctly."""
    with patch("src.core.archive_growth_monitor.STATE_PATH", temp_state / "archive_growth.json"):
        snap1 = GrowthSnapshot(ts=1000, size_mb=10.5, message_count=100)
        snap2 = GrowthSnapshot(ts=2000, size_mb=15.3, message_count=200)

        save_history([snap1, snap2])

        loaded = load_history()
        assert len(loaded) == 2
        assert loaded[0].size_mb == 10.5
        assert loaded[1].message_count == 200


def test_anomaly_detection_triggers_above_threshold(temp_db, temp_state):
    """Test anomaly alert triggers when growth exceeds threshold."""
    with (
        patch("src.core.archive_growth_monitor.ARCHIVE_DB", temp_db),
        patch("src.core.archive_growth_monitor.STATE_PATH", temp_state / "archive_growth.json"),
        patch("src.core.archive_growth_monitor.ANOMALY_MB_PER_DAY", 5.0),
    ):
        # Create history with snapshot 1 day ago (10 MB)
        day_ago = int(time.time()) - 86400
        old_snap = GrowthSnapshot(ts=day_ago - 100, size_mb=10.0, message_count=100)
        save_history([old_snap])

        # Mock current snapshot to be 20 MB (delta = 10 > 5 MB threshold)
        with patch("src.core.archive_growth_monitor.take_snapshot") as mock_snap:
            mock_snap.return_value = GrowthSnapshot(
                ts=int(time.time()), size_mb=20.0, message_count=200
            )

            snap, warning = append_snapshot_and_check_anomaly()
            assert snap is not None
            assert warning is not None
            assert "grew" in warning
            assert "10.0 MB" in warning


def test_anomaly_detection_no_alert_below_threshold(temp_db, temp_state):
    """Test no anomaly alert when growth is below threshold."""
    with (
        patch("src.core.archive_growth_monitor.ARCHIVE_DB", temp_db),
        patch("src.core.archive_growth_monitor.STATE_PATH", temp_state / "archive_growth.json"),
        patch("src.core.archive_growth_monitor.ANOMALY_MB_PER_DAY", 50.0),
    ):
        # Create history with snapshot 1 day ago (10 MB)
        day_ago = int(time.time()) - 86400
        old_snap = GrowthSnapshot(ts=day_ago - 100, size_mb=10.0, message_count=100)
        save_history([old_snap])

        # Mock current snapshot to be 15 MB (delta = 5 < 50 MB threshold)
        with patch("src.core.archive_growth_monitor.take_snapshot") as mock_snap:
            mock_snap.return_value = GrowthSnapshot(
                ts=int(time.time()), size_mb=15.0, message_count=150
            )

            snap, warning = append_snapshot_and_check_anomaly()
            assert snap is not None
            assert warning is None


def test_growth_summary_structure(temp_state):
    """Test growth_summary returns correct structure."""
    with patch("src.core.archive_growth_monitor.STATE_PATH", temp_state / "archive_growth.json"):
        snap1 = GrowthSnapshot(ts=1000, size_mb=10.0, message_count=100)
        snap2 = GrowthSnapshot(ts=1000 + 86400, size_mb=20.0, message_count=200)
        save_history([snap1, snap2])

        summary = growth_summary()
        assert summary["snapshots"] == 2
        assert summary["latest_size_mb"] == 20.0
        assert summary["first_size_mb"] == 10.0
        assert summary["growth_mb_per_day"] == 10.0
        assert summary["growth_messages_per_day"] == 100.0


def test_growth_summary_insufficient_data(temp_state):
    """Test growth_summary with insufficient data."""
    with patch("src.core.archive_growth_monitor.STATE_PATH", temp_state / "archive_growth.json"):
        save_history([])
        summary = growth_summary()
        assert summary["summary"] == "Not enough data"


@pytest.mark.asyncio
async def test_nightly_summary_integration_with_anomaly(temp_db, temp_state):
    """Test archive growth snapshot integrated in nightly summary with anomaly."""
    from src.core.nightly_summary import generate_summary

    with (
        patch("src.core.archive_growth_monitor.ARCHIVE_DB", temp_db),
        patch("src.core.archive_growth_monitor.STATE_PATH", temp_state / "archive_growth.json"),
        patch("src.core.archive_growth_monitor.ANOMALY_MB_PER_DAY", 5.0),
        patch("src.core.nightly_summary._append_cost_stats"),
        patch("src.core.nightly_summary._append_inbox_stats"),
        patch("src.core.nightly_summary._append_swarm_stats"),
        patch("src.core.nightly_summary._append_reminder_stats"),
    ):
        # Create history with old snapshot
        day_ago = int(time.time()) - 86400
        old_snap = GrowthSnapshot(ts=day_ago - 100, size_mb=10.0, message_count=100)
        save_history([old_snap])

        # Mock current snapshot to trigger anomaly
        with patch("src.core.archive_growth_monitor.take_snapshot") as mock_snap:
            mock_snap.return_value = GrowthSnapshot(
                ts=int(time.time()), size_mb=30.0, message_count=500
            )

            summary = await generate_summary()
            assert "🦀 **Krab Daily Digest**" in summary
            assert "⚠️" in summary  # Anomaly warning should be present
            assert "grew" in summary.lower()


@pytest.mark.asyncio
async def test_nightly_summary_integration_no_anomaly(temp_db, temp_state):
    """Test archive growth snapshot integrated in nightly summary without anomaly."""
    from src.core.nightly_summary import generate_summary

    with (
        patch("src.core.archive_growth_monitor.ARCHIVE_DB", temp_db),
        patch("src.core.archive_growth_monitor.STATE_PATH", temp_state / "archive_growth.json"),
        patch("src.core.archive_growth_monitor.ANOMALY_MB_PER_DAY", 50.0),
        patch("src.core.nightly_summary._append_cost_stats"),
        patch("src.core.nightly_summary._append_inbox_stats"),
        patch("src.core.nightly_summary._append_swarm_stats"),
        patch("src.core.nightly_summary._append_reminder_stats"),
    ):
        # Create history with old snapshot
        day_ago = int(time.time()) - 86400
        old_snap = GrowthSnapshot(ts=day_ago - 100, size_mb=10.0, message_count=100)
        save_history([old_snap])

        # Mock current small growth
        with patch("src.core.archive_growth_monitor.take_snapshot") as mock_snap:
            mock_snap.return_value = GrowthSnapshot(
                ts=int(time.time()), size_mb=15.0, message_count=150
            )

            summary = await generate_summary()
            assert "🦀 **Krab Daily Digest**" in summary
            # Should NOT have anomaly warning
            assert not any(
                anomaly_phrase in summary for anomaly_phrase in ["⚠️ Archive.db grew", "Threshold"]
            )
