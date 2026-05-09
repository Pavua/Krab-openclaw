"""Wave 44-T-apple-apps — tests for Apple Notes/Calendar/Reminders/Music/Spotlight scripts.

Tests run scripts in-process and mock _run_osa / subprocess.run to avoid hitting
real osascript on CI. Each subcommand verifies happy path JSON output.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TOOLS_DIR = REPO_ROOT / "scripts" / "agent_tools"
sys.path.insert(0, str(TOOLS_DIR))


# ---------- Notes ----------


def test_notes_list_parses_pipe_format(capsys):
    import krab_notes  # type: ignore

    fake = (0, "x-coredata://abc/ICNote/p1|||Title One\nx-coredata://abc/ICNote/p2|||Title Two", "")
    with patch.object(krab_notes, "_run_osa", return_value=fake):
        rc = krab_notes.main(["list"])
    assert rc == 0
    out = capsys.readouterr().out.strip().splitlines()[-1]
    import json

    data = json.loads(out)
    assert data["ok"] is True
    assert data["count"] == 2
    assert data["notes"][0]["title"] == "Title One"


def test_notes_create_returns_id(capsys):
    import krab_notes  # type: ignore

    with patch.object(krab_notes, "_run_osa", return_value=(0, "x-coredata://abc/ICNote/pNEW", "")):
        rc = krab_notes.main(["create", "--title", "Hello", "--body", "World"])
    assert rc == 0
    import json

    data = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert data["ok"] is True
    assert data["id"] == "x-coredata://abc/ICNote/pNEW"
    assert data["title"] == "Hello"


def test_notes_search_no_results(capsys):
    import krab_notes  # type: ignore

    with patch.object(krab_notes, "_run_osa", return_value=(0, "", "")):
        rc = krab_notes.main(["search", "--query", "foobar"])
    assert rc == 0
    import json

    data = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert data["ok"] is True
    assert data["count"] == 0


def test_notes_get_error(capsys):
    import krab_notes  # type: ignore

    with patch.object(krab_notes, "_run_osa", return_value=(1, "", "not found")):
        rc = krab_notes.main(["get", "--id", "missing"])
    assert rc == 1


# ---------- Calendar ----------


def test_calendar_create_ok(capsys):
    import krab_calendar  # type: ignore

    with patch.object(krab_calendar, "_run_osa", return_value=(0, "EVENT-UID-123", "")):
        rc = krab_calendar.main(
            [
                "create",
                "--title",
                "Meeting",
                "--start",
                "2026-05-10T14:00",
                "--duration",
                "30",
            ]
        )
    assert rc == 0
    import json

    data = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert data["ok"] is True
    assert data["id"] == "EVENT-UID-123"
    assert data["title"] == "Meeting"


def test_calendar_events_parses(capsys):
    import krab_calendar  # type: ignore

    fake = (0, "uid1|||Lunch|||date X|||date Y\nuid2|||Standup|||date A|||date B", "")
    with patch.object(krab_calendar, "_run_osa", return_value=fake):
        rc = krab_calendar.main(["events", "--start", "2026-05-09", "--end", "2026-05-10"])
    assert rc == 0
    import json

    data = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert data["ok"] is True
    assert data["count"] == 2
    assert data["events"][0]["title"] == "Lunch"


def test_calendar_create_bad_iso(capsys):
    import krab_calendar  # type: ignore

    rc = krab_calendar.main(["create", "--title", "x", "--start", "not-a-date", "--duration", "10"])
    assert rc == 1


# ---------- Reminders ----------


def test_reminders_create_ok(capsys):
    import krab_reminders  # type: ignore

    with patch.object(krab_reminders, "_run_osa", return_value=(0, "REM-ID-1", "")):
        rc = krab_reminders.main(["create", "--title", "Buy milk"])
    assert rc == 0
    import json

    data = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert data["ok"] is True
    assert data["id"] == "REM-ID-1"
    assert data["list"] == "Reminders"  # default


def test_reminders_create_with_due(capsys):
    import krab_reminders  # type: ignore

    with patch.object(krab_reminders, "_run_osa", return_value=(0, "REM-ID-2", "")):
        rc = krab_reminders.main(["create", "--title", "Pay bill", "--due", "2026-05-10T18:00"])
    assert rc == 0


def test_reminders_list_parses(capsys):
    import krab_reminders  # type: ignore

    with patch.object(
        krab_reminders, "_run_osa", return_value=(0, "id1|||Task A\nid2|||Task B", "")
    ):
        rc = krab_reminders.main(["list"])
    assert rc == 0
    import json

    data = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert data["ok"] is True
    assert data["count"] == 2


# ---------- Music ----------


def test_music_current_playing(capsys):
    import krab_music  # type: ignore

    fake = (0, "Bohemian Rhapsody|||Queen|||A Night at the Opera|||playing", "")
    with patch.object(krab_music, "_run_osa", return_value=fake):
        rc = krab_music.main(["current"])
    assert rc == 0
    import json

    data = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert data["ok"] is True
    assert data["playing"] is True
    assert data["track"] == "Bohemian Rhapsody"
    assert data["artist"] == "Queen"


def test_music_current_stopped(capsys):
    import krab_music  # type: ignore

    with patch.object(krab_music, "_run_osa", return_value=(0, "STOPPED", "")):
        rc = krab_music.main(["current"])
    assert rc == 0
    import json

    data = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert data["ok"] is True
    assert data["playing"] is False


def test_music_play_playlist(capsys):
    import krab_music  # type: ignore

    with patch.object(krab_music, "_run_osa", return_value=(0, "playing playlist", "")):
        rc = krab_music.main(["play", "--playlist", "Focus"])
    assert rc == 0


def test_music_pause(capsys):
    import krab_music  # type: ignore

    with patch.object(krab_music, "_run_osa", return_value=(0, "", "")):
        rc = krab_music.main(["pause"])
    assert rc == 0


# ---------- Spotlight ----------


def test_spotlight_search_ok(capsys):
    import krab_spotlight  # type: ignore

    class FakeProc:
        returncode = 0
        stdout = "/Users/x/file1.txt\n/Users/x/file2.txt\n"
        stderr = ""

    with patch("subprocess.run", return_value=FakeProc()):
        rc = krab_spotlight.main(["search", "--query", "krab"])
    assert rc == 0
    import json

    data = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert data["ok"] is True
    assert data["count"] == 2


def test_spotlight_search_failure(capsys):
    import krab_spotlight  # type: ignore

    class FakeProc:
        returncode = 1
        stdout = ""
        stderr = "permission denied"

    with patch("subprocess.run", return_value=FakeProc()):
        rc = krab_spotlight.main(["search", "--query", "x"])
    assert rc == 1


# ---------- System prompt wiring ----------


def test_system_prompt_contains_apple_apps_block():
    """Sanity: agentic stance prompt mentions Apple-app scripts."""
    src = (REPO_ROOT / "src" / "userbot" / "access_control.py").read_text(encoding="utf-8")
    assert "krab_notes.py" in src
    assert "krab_calendar.py" in src
    assert "krab_reminders.py" in src
    assert "krab_music.py" in src
    assert "krab_spotlight.py" in src


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
