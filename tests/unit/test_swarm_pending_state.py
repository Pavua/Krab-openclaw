# -*- coding: utf-8 -*-
"""
tests/unit/test_swarm_pending_state.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Unit-тесты для swarm_pending_state.py (Phase 1: write-only checkpoint).
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Фикстура: изолированная временная директория
# ---------------------------------------------------------------------------


@pytest.fixture()
def pending_dir(tmp_path: Path) -> Path:
    """Отдельная директория для каждого теста."""
    d = tmp_path / "swarm_pending"
    d.mkdir()
    return d


@pytest.fixture()
def store(pending_dir: Path):
    """SwarmPendingStore с тестовой директорией."""
    from src.core.swarm_pending_state import SwarmPendingStore

    return SwarmPendingStore(pending_dir=pending_dir)


# ---------------------------------------------------------------------------
# Вспомогательные утилиты
# ---------------------------------------------------------------------------


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# make_round_id
# ---------------------------------------------------------------------------


class TestMakeRoundId:
    def test_format_with_chat_id(self):
        from src.core.swarm_pending_state import make_round_id

        rid = make_round_id("analysts", chat_id=-1001234567890)
        parts = rid.split("_")
        assert parts[0] == "analysts"
        assert parts[1] == "-1001234567890"
        assert parts[2].isdigit()
        assert len(parts[3]) == 4  # hex nonce

    def test_format_no_chat_id(self):
        from src.core.swarm_pending_state import make_round_id

        rid = make_round_id("traders")
        parts = rid.split("_")
        assert parts[0] == "traders"
        assert parts[1] == "0"

    def test_uniqueness(self):
        from src.core.swarm_pending_state import make_round_id

        ids = {make_round_id("analysts") for _ in range(20)}
        # Все уникальные (nonce + time)
        assert len(ids) >= 10  # при быстром запуске ts может совпасть, но nonce — нет


# ---------------------------------------------------------------------------
# create_initial
# ---------------------------------------------------------------------------


class TestCreateInitial:
    def test_creates_file(self, store, pending_dir):
        ok = store.create_initial("test_round_1", team="analysts", topic="тест")
        assert ok is True
        path = pending_dir / "test_round_1.json"
        assert path.exists()

    def test_no_tmp_file_after_write(self, store, pending_dir):
        store.create_initial("test_round_2", team="analysts", topic="тест")
        tmp = pending_dir / "test_round_2.json.tmp"
        assert not tmp.exists(), "tmp-файл должен быть удалён после rename"

    def test_json_shape(self, store, pending_dir):
        store.create_initial(
            "shape_round",
            team="coders",
            topic="проверка схемы",
            initiator_chat_id=-100999,
            initiator_message_id=42,
            ab_id="test_ab",
            ab_variant="candidate",
        )
        data = _read_json(pending_dir / "shape_round.json")

        # Обязательные поля
        assert data["round_id"] == "shape_round"
        assert data["team"] == "coders"
        assert data["topic"] == "проверка схемы"
        assert data["status"] == "pending"
        assert data["attempt_count"] == 0
        assert data["max_attempts"] >= 1
        assert "created_at" in data
        assert "ttl_expires_at" in data

        # cursor
        assert data["cursor"]["role_idx"] == 0
        assert data["cursor"]["delegation_pending"] is None

        # initiator
        assert data["initiator"]["chat_id"] == -100999
        assert data["initiator"]["message_id"] == 42

        # A/B
        assert data["ab_id"] == "test_ab"
        assert data["ab_variant"] == "candidate"

        # completed_roles изначально пуст
        assert data["completed_roles"] == []

    def test_ttl_field_is_future(self, store, pending_dir):
        store.create_initial("ttl_round", team="traders", topic="ttl test")
        data = _read_json(pending_dir / "ttl_round.json")
        from datetime import datetime, timezone

        ttl = datetime.fromisoformat(data["ttl_expires_at"])
        if ttl.tzinfo is None:
            ttl = ttl.replace(tzinfo=timezone.utc)
        assert ttl > datetime.now(timezone.utc), "TTL должен быть в будущем"

    def test_disabled_by_env(self, store, pending_dir, monkeypatch):
        monkeypatch.setenv("KRAB_SWARM_RESUME_ENABLED", "0")
        # Пересоздаём store чтобы env был применён
        import importlib

        import src.core.swarm_pending_state as m

        importlib.reload(m)
        store2 = m.SwarmPendingStore(pending_dir=pending_dir)
        ok = store2.create_initial("disabled_round", team="analysts", topic="test")
        # При ENABLED=0 возвращает False и файл не создаётся
        assert ok is False
        assert not (pending_dir / "disabled_round.json").exists()
        # Restore
        monkeypatch.setenv("KRAB_SWARM_RESUME_ENABLED", "1")
        importlib.reload(m)


# ---------------------------------------------------------------------------
# write_checkpoint
# ---------------------------------------------------------------------------


class TestWriteCheckpoint:
    def test_checkpoint_updates_cursor(self, store, pending_dir):
        store.create_initial("cp_round", team="analysts", topic="тема")
        store.write_checkpoint(
            "cp_round",
            next_role_idx=1,
            next_role_name="critic",
            accumulated_context="[🔬 Аналитик]:\nтекст...\n\n",
            completed_roles=[
                {"role": "analyst", "emoji": "🔬", "title": "Аналитик", "text": "текст..."}
            ],
        )
        data = _read_json(pending_dir / "cp_round.json")
        assert data["cursor"]["role_idx"] == 1
        assert data["cursor"]["role_name"] == "critic"
        assert len(data["completed_roles"]) == 1
        assert "[🔬 Аналитик]" in data["accumulated_context"]

    def test_checkpoint_atomic_rename(self, store, pending_dir):
        store.create_initial("atomic_round", team="traders", topic="атомарность")
        store.write_checkpoint(
            "atomic_round",
            next_role_idx=1,
            next_role_name="risk_assessor",
            accumulated_context="ctx",
            completed_roles=[],
        )
        # .tmp должен быть удалён
        tmp = pending_dir / "atomic_round.json.tmp"
        assert not tmp.exists()
        # .json должен существовать
        assert (pending_dir / "atomic_round.json").exists()

    def test_checkpoint_no_file_no_crash(self, store):
        """Если pending-файла нет — write_checkpoint возвращает False, не бросает."""
        ok = store.write_checkpoint(
            "nonexistent_round",
            next_role_idx=1,
            next_role_name="x",
            accumulated_context="",
            completed_roles=[],
        )
        assert ok is False

    def test_context_clip(self, store, pending_dir):
        store.create_initial("clip_round", team="creative", topic="clip")
        big_ctx = "A" * 20_000
        store.write_checkpoint(
            "clip_round",
            next_role_idx=1,
            next_role_name="x",
            accumulated_context=big_ctx,
            completed_roles=[],
        )
        data = _read_json(pending_dir / "clip_round.json")
        assert len(data["accumulated_context"]) <= 8001  # _CONTEXT_CLIP + небольшой допуск


# ---------------------------------------------------------------------------
# mark_round_complete
# ---------------------------------------------------------------------------


class TestMarkRoundComplete:
    def test_deletes_file(self, store, pending_dir):
        store.create_initial("done_round", team="analysts", topic="done")
        store.mark_round_complete("done_round")
        assert not (pending_dir / "done_round.json").exists()

    def test_no_error_if_missing(self, store):
        """Удаление несуществующего файла — silent."""
        store.mark_round_complete("ghost_round")  # не должно бросить


# ---------------------------------------------------------------------------
# mark_round_failed
# ---------------------------------------------------------------------------


class TestMarkRoundFailed:
    def test_updates_status_and_reason(self, store, pending_dir):
        store.create_initial("fail_round", team="traders", topic="fail test")
        store.mark_round_failed("fail_round", reason="quota_exceeded")
        data = _read_json(pending_dir / "fail_round.json")
        assert data["status"] == "interrupted"
        assert data["failure_reason"] == "quota_exceeded"

    def test_file_preserved_after_fail(self, store, pending_dir):
        store.create_initial("fail_preserve", team="coders", topic="preserve")
        store.mark_round_failed("fail_preserve", reason="timeout")
        # Файл должен остаться для Phase 2 resume
        assert (pending_dir / "fail_preserve.json").exists()

    def test_no_error_if_missing(self, store):
        """mark_round_failed на несуществующем файле — silent."""
        store.mark_round_failed("no_such_round", reason="test")


# ---------------------------------------------------------------------------
# list_pending
# ---------------------------------------------------------------------------


class TestListPending:
    def test_lists_all(self, store, pending_dir):
        store.create_initial("round_a", team="analysts", topic="A")
        store.create_initial("round_b", team="traders", topic="B")
        store.create_initial("round_c", team="coders", topic="C")
        pending = store.list_pending()
        round_ids = {s.round_id for s in pending}
        assert {"round_a", "round_b", "round_c"} == round_ids

    def test_empty_dir(self, store, pending_dir):
        assert store.list_pending() == []

    def test_ignores_tmp_files(self, store, pending_dir):
        # Создаём осиротевший .tmp (симулируем незавершённую запись)
        (pending_dir / "orphan.json.tmp").write_text("{}")
        result = store.list_pending()
        assert result == []


# ---------------------------------------------------------------------------
# SwarmRoundState.is_expired
# ---------------------------------------------------------------------------


class TestRoundStateExpiry:
    def test_not_expired_future(self):
        from datetime import datetime, timedelta, timezone

        from src.core.swarm_pending_state import SwarmRoundState

        state = SwarmRoundState(
            round_id="x",
            team="t",
            topic="top",
            created_at=datetime.now(timezone.utc).isoformat(),
            ttl_expires_at=(datetime.now(timezone.utc) + timedelta(hours=24)).isoformat(),
        )
        assert not state.is_expired()

    def test_expired_past(self):
        from datetime import datetime, timedelta, timezone

        from src.core.swarm_pending_state import SwarmRoundState

        state = SwarmRoundState(
            round_id="x",
            team="t",
            topic="top",
            created_at=datetime.now(timezone.utc).isoformat(),
            ttl_expires_at=(datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
        )
        assert state.is_expired()

    def test_malformed_ttl_not_expired(self):
        """Некорректный TTL — считаем не истёкшим (conservative)."""
        from src.core.swarm_pending_state import SwarmRoundState

        state = SwarmRoundState(
            round_id="x",
            team="t",
            topic="top",
            created_at="",
            ttl_expires_at="INVALID",
        )
        assert not state.is_expired()
