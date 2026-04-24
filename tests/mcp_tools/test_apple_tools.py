"""Тесты Apple-native MCP tools (Notes / iMessage / Reminders / Calendar).

Мокаем:
- MacOSAutomationService (через server._get_macos_service) — чтобы не запускать osascript
- sqlite3.connect + chat.db для iMessage

Проверяем:
- happy-path: корректный JSON с ok=true
- TCC denial: permission_denied + tcc_required
- write_gate: write-операции блокируются без KRAB_MCP_APPLE_WRITE_ENABLED
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ─────────────────────────────────────────────────────────────────────────────
# Хелперы для mock'а MacOSAutomationService
# ─────────────────────────────────────────────────────────────────────────────


class _FakeMacOSError(Exception):
    pass


def _make_fake_svc(
    *,
    list_notes=None,
    list_reminders=None,
    list_calendar=None,
    create_note=None,
    create_reminder=None,
    create_event=None,
    run_osascript=None,
):
    svc = MagicMock()
    svc.list_notes = AsyncMock(return_value=list_notes or [])
    svc.list_reminders = AsyncMock(return_value=list_reminders or [])
    svc.list_upcoming_calendar_events = AsyncMock(return_value=list_calendar or [])
    svc.create_note = AsyncMock(return_value=create_note or {"id": "x", "folder_name": ""})
    svc.create_reminder = AsyncMock(
        return_value=create_reminder or {"id": "x", "list_name": ""}
    )
    svc.create_calendar_event = AsyncMock(
        return_value=create_event or {"id": "x", "calendar_name": ""}
    )
    svc._run_osascript = AsyncMock(return_value=run_osascript or "")
    return svc


def _patch_service(mcp_server, svc):
    """Патчит _get_macos_service чтобы вернул (svc, _FakeMacOSError)."""
    return patch.object(mcp_server, "_get_macos_service", return_value=(svc, _FakeMacOSError))


# ─────────────────────────────────────────────────────────────────────────────
# Apple Notes
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_notes_list_happy(mcp_server):
    svc = _make_fake_svc(
        list_notes=[
            {"account_name": "iCloud", "folder_name": "Notes", "title": "Hello"},
            {"account_name": "iCloud", "folder_name": "Work", "title": "Todo"},
        ]
    )
    with _patch_service(mcp_server, svc):
        out = await mcp_server.notes_list(mcp_server._NotesListInput(limit=10))
    data = json.loads(out)
    assert data["ok"] is True
    assert data["count"] == 2


@pytest.mark.asyncio
async def test_notes_list_tcc_denied(mcp_server):
    svc = _make_fake_svc()
    svc.list_notes = AsyncMock(
        side_effect=_FakeMacOSError(
            "command_failed: 0:0: execution error: Not authorized to send Apple events to Notes. (-1743)"
        )
    )
    with _patch_service(mcp_server, svc):
        out = await mcp_server.notes_list(mcp_server._NotesListInput())
    data = json.loads(out)
    assert data["ok"] is False
    assert data["error"] == "permission_denied"
    assert "Notes.app" in data["tcc_required"]


@pytest.mark.asyncio
async def test_notes_get_happy(mcp_server):
    svc = _make_fake_svc(run_osascript="Hello Title\n---\nBody text here")
    with _patch_service(mcp_server, svc):
        out = await mcp_server.notes_get(mcp_server._NotesGetInput(note_id="Hello Title"))
    data = json.loads(out)
    assert data["ok"] is True
    assert data["title"] == "Hello Title"
    assert "Body text here" in data["body"]


@pytest.mark.asyncio
async def test_notes_get_not_found(mcp_server):
    svc = _make_fake_svc(run_osascript="")
    with _patch_service(mcp_server, svc):
        out = await mcp_server.notes_get(mcp_server._NotesGetInput(note_id="missing"))
    data = json.loads(out)
    assert data["ok"] is False
    assert data["error"] == "not_found"


@pytest.mark.asyncio
async def test_notes_search_happy(mcp_server):
    svc = _make_fake_svc(run_osascript="id123||Meeting notes||Work\nid124||Meet prep||Work")
    with _patch_service(mcp_server, svc):
        out = await mcp_server.notes_search(
            mcp_server._NotesSearchInput(query="meet", limit=10)
        )
    data = json.loads(out)
    assert data["ok"] is True
    assert data["count"] == 2
    assert data["results"][0]["id"] == "id123"


@pytest.mark.asyncio
async def test_notes_search_empty(mcp_server):
    svc = _make_fake_svc(run_osascript="")
    with _patch_service(mcp_server, svc):
        out = await mcp_server.notes_search(mcp_server._NotesSearchInput(query="xyz"))
    data = json.loads(out)
    assert data["ok"] is True
    assert data["count"] == 0


@pytest.mark.asyncio
async def test_notes_create_disabled_by_default(mcp_server, monkeypatch):
    monkeypatch.delenv("KRAB_MCP_APPLE_WRITE_ENABLED", raising=False)
    out = await mcp_server.notes_create(
        mcp_server._NotesCreateInput(title="T", body="B")
    )
    data = json.loads(out)
    assert data["ok"] is False
    assert data["error"] == "write_disabled"


@pytest.mark.asyncio
async def test_notes_create_enabled(mcp_server, monkeypatch):
    monkeypatch.setenv("KRAB_MCP_APPLE_WRITE_ENABLED", "1")
    svc = _make_fake_svc(create_note={"id": "note42", "folder_name": "Work"})
    with _patch_service(mcp_server, svc):
        out = await mcp_server.notes_create(
            mcp_server._NotesCreateInput(title="T", body="B", folder="Work")
        )
    data = json.loads(out)
    assert data["ok"] is True
    assert data["id"] == "note42"


# ─────────────────────────────────────────────────────────────────────────────
# iMessage (chat.db через sqlite3)
# ─────────────────────────────────────────────────────────────────────────────


def _make_fake_chat_db(tmp_path: Path) -> Path:
    """Создаёт минимальную копию схемы chat.db с парой записей."""
    db_path = tmp_path / "chat.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE handle (ROWID INTEGER PRIMARY KEY, id TEXT);
        CREATE TABLE chat (ROWID INTEGER PRIMARY KEY, display_name TEXT);
        CREATE TABLE message (
            ROWID INTEGER PRIMARY KEY,
            text TEXT,
            date INTEGER,
            is_from_me INTEGER,
            is_read INTEGER,
            handle_id INTEGER
        );
        CREATE TABLE chat_message_join (chat_id INTEGER, message_id INTEGER);
        INSERT INTO handle VALUES (1, '+34600111222');
        INSERT INTO handle VALUES (2, 'alice@example.com');
        INSERT INTO chat VALUES (1, 'Alice');
        INSERT INTO message VALUES (100, 'hello world', 700000000000000000, 0, 0, 1);
        INSERT INTO message VALUES (101, 'hello again', 700000000000000001, 1, 1, 1);
        INSERT INTO message VALUES (102, 'unrelated', 700000000000000002, 0, 1, 2);
        INSERT INTO chat_message_join VALUES (1, 100);
        INSERT INTO chat_message_join VALUES (1, 101);
        """
    )
    conn.commit()
    conn.close()
    return db_path


@pytest.mark.asyncio
async def test_imessage_search_happy(mcp_server, tmp_path, monkeypatch):
    db_path = _make_fake_chat_db(tmp_path)
    monkeypatch.setattr(mcp_server, "_CHAT_DB_PATH", db_path)
    out = await mcp_server.imessage_search(
        mcp_server._IMessageSearchInput(query="hello", limit=10)
    )
    data = json.loads(out)
    assert data["ok"] is True
    assert data["count"] == 2
    # ORDER BY date DESC
    assert data["results"][0]["id"] == 101


@pytest.mark.asyncio
async def test_imessage_search_db_not_found(mcp_server, tmp_path, monkeypatch):
    monkeypatch.setattr(mcp_server, "_CHAT_DB_PATH", tmp_path / "nope.db")
    out = await mcp_server.imessage_search(
        mcp_server._IMessageSearchInput(query="anything")
    )
    data = json.loads(out)
    assert data["ok"] is False
    assert data["error"] == "chat_db_not_found"


@pytest.mark.asyncio
async def test_imessage_unread(mcp_server, tmp_path, monkeypatch):
    db_path = _make_fake_chat_db(tmp_path)
    monkeypatch.setattr(mcp_server, "_CHAT_DB_PATH", db_path)
    out = await mcp_server.imessage_unread(mcp_server._IMessageUnreadInput(limit=10))
    data = json.loads(out)
    assert data["ok"] is True
    # is_read=0 AND is_from_me=0 → только ROWID 100
    assert data["count"] == 1
    assert data["results"][0]["id"] == 100


@pytest.mark.asyncio
async def test_imessage_history_by_chat_rowid(mcp_server, tmp_path, monkeypatch):
    db_path = _make_fake_chat_db(tmp_path)
    monkeypatch.setattr(mcp_server, "_CHAT_DB_PATH", db_path)
    out = await mcp_server.imessage_history(
        mcp_server._IMessageHistoryInput(chat_id_or_handle="1", limit=10)
    )
    data = json.loads(out)
    assert data["ok"] is True
    assert data["count"] == 2


@pytest.mark.asyncio
async def test_imessage_history_by_handle(mcp_server, tmp_path, monkeypatch):
    db_path = _make_fake_chat_db(tmp_path)
    monkeypatch.setattr(mcp_server, "_CHAT_DB_PATH", db_path)
    out = await mcp_server.imessage_history(
        mcp_server._IMessageHistoryInput(chat_id_or_handle="alice@example.com", limit=10)
    )
    data = json.loads(out)
    assert data["ok"] is True
    assert data["count"] == 1
    assert data["results"][0]["id"] == 102


# ─────────────────────────────────────────────────────────────────────────────
# Reminders
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reminders_list_happy(mcp_server):
    svc = _make_fake_svc(
        list_reminders=[
            {"list_name": "Home", "title": "buy milk", "due_label": ""},
            {"list_name": "Work", "title": "review PR", "due_label": ""},
        ]
    )
    with _patch_service(mcp_server, svc):
        out = await mcp_server.reminders_list(mcp_server._RemindersListInput())
    data = json.loads(out)
    assert data["ok"] is True
    assert data["count"] == 2


@pytest.mark.asyncio
async def test_reminders_list_filter_by_name(mcp_server):
    svc = _make_fake_svc(
        list_reminders=[
            {"list_name": "Home", "title": "buy milk", "due_label": ""},
            {"list_name": "Work", "title": "review PR", "due_label": ""},
        ]
    )
    with _patch_service(mcp_server, svc):
        out = await mcp_server.reminders_list(
            mcp_server._RemindersListInput(list_name="Home")
        )
    data = json.loads(out)
    assert data["count"] == 1
    assert data["reminders"][0]["list_name"] == "Home"


@pytest.mark.asyncio
async def test_reminders_create_gated(mcp_server, monkeypatch):
    monkeypatch.delenv("KRAB_MCP_APPLE_WRITE_ENABLED", raising=False)
    out = await mcp_server.reminders_create(
        mcp_server._RemindersCreateInput(title="buy milk")
    )
    data = json.loads(out)
    assert data["ok"] is False
    assert data["error"] == "write_disabled"


@pytest.mark.asyncio
async def test_reminders_create_enabled(mcp_server, monkeypatch):
    monkeypatch.setenv("KRAB_MCP_APPLE_WRITE_ENABLED", "1")
    svc = _make_fake_svc(create_reminder={"id": "rem1", "list_name": "Home"})
    with _patch_service(mcp_server, svc):
        out = await mcp_server.reminders_create(
            mcp_server._RemindersCreateInput(
                title="buy milk", due_date="2026-05-01T10:00:00", list_name="Home"
            )
        )
    data = json.loads(out)
    assert data["ok"] is True
    assert data["id"] == "rem1"


# ─────────────────────────────────────────────────────────────────────────────
# Calendar
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_calendar_events_happy(mcp_server):
    svc = _make_fake_svc(
        list_calendar=[
            {"calendar_name": "Personal", "title": "Doctor", "start_label": "2026-04-25 10:00"},
        ]
    )
    with _patch_service(mcp_server, svc):
        out = await mcp_server.calendar_events(mcp_server._CalendarEventsInput())
    data = json.loads(out)
    assert data["ok"] is True
    assert data["count"] == 1


@pytest.mark.asyncio
async def test_calendar_events_tcc_denied(mcp_server):
    svc = _make_fake_svc()
    svc.list_upcoming_calendar_events = AsyncMock(
        side_effect=_FakeMacOSError(
            "command_failed: execution error: Calendar.app privacy -1743"
        )
    )
    with _patch_service(mcp_server, svc):
        out = await mcp_server.calendar_events(mcp_server._CalendarEventsInput())
    data = json.loads(out)
    assert data["ok"] is False
    assert data["error"] == "permission_denied"
    assert "Calendar" in data["tcc_required"]


@pytest.mark.asyncio
async def test_calendar_create_gated(mcp_server, monkeypatch):
    monkeypatch.delenv("KRAB_MCP_APPLE_WRITE_ENABLED", raising=False)
    out = await mcp_server.calendar_create_event(
        mcp_server._CalendarCreateInput(
            title="Meeting",
            start="2026-05-01T10:00:00",
            end="2026-05-01T11:00:00",
        )
    )
    data = json.loads(out)
    assert data["ok"] is False
    assert data["error"] == "write_disabled"


@pytest.mark.asyncio
async def test_calendar_create_enabled(mcp_server, monkeypatch):
    monkeypatch.setenv("KRAB_MCP_APPLE_WRITE_ENABLED", "1")
    svc = _make_fake_svc(create_event={"id": "evt1", "calendar_name": "Personal"})
    with _patch_service(mcp_server, svc):
        out = await mcp_server.calendar_create_event(
            mcp_server._CalendarCreateInput(
                title="Meeting",
                start="2026-05-01T10:00:00",
                end="2026-05-01T11:00:00",
                calendar_name="Personal",
            )
        )
    data = json.loads(out)
    assert data["ok"] is True
    assert data["id"] == "evt1"


@pytest.mark.asyncio
async def test_calendar_create_invalid_iso(mcp_server, monkeypatch):
    monkeypatch.setenv("KRAB_MCP_APPLE_WRITE_ENABLED", "1")
    out = await mcp_server.calendar_create_event(
        mcp_server._CalendarCreateInput(
            title="Meeting", start="not-an-iso-date", end="also-not"
        )
    )
    data = json.loads(out)
    assert data["ok"] is False
    assert data["error"] == "invalid_iso_datetime"
