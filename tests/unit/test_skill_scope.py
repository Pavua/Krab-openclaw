# -*- coding: utf-8 -*-
"""Tests for src/core/skill_scope.py (Chado §4 P2)."""
from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

import src.core.skill_scope as ss


@pytest.fixture(autouse=True)
def clean_registry(tmp_path, monkeypatch):
    """Each test starts with a fresh registry and a tmp persist path."""
    monkeypatch.setattr(ss, "_PERSIST_PATH", tmp_path / "skill_scopes.json")
    ss.reset()
    yield
    ss.reset()


# ---------------------------------------------------------------------------
# 1. Default global scope — allowed everywhere
# ---------------------------------------------------------------------------


def test_global_allowed_no_context():
    ss.register_scope("my_skill")
    assert ss.is_allowed("my_skill") is True


def test_global_allowed_with_chat():
    ss.register_scope("my_skill")
    assert ss.is_allowed("my_skill", chat_id=999) is True


def test_global_allowed_with_team():
    ss.register_scope("my_skill")
    assert ss.is_allowed("my_skill", team="traders") is True


# ---------------------------------------------------------------------------
# 2. Chat scope
# ---------------------------------------------------------------------------


def test_chat_scope_allowed_in_registered_chat():
    ss.register_scope("beta", scope="chat", chat_ids=[100, 200])
    assert ss.is_allowed("beta", chat_id=100) is True
    assert ss.is_allowed("beta", chat_id=200) is True


def test_chat_scope_denied_outside_registered_chats():
    ss.register_scope("beta", scope="chat", chat_ids=[100])
    assert ss.is_allowed("beta", chat_id=999) is False


def test_chat_scope_denied_no_chat_id():
    ss.register_scope("beta", scope="chat", chat_ids=[100])
    assert ss.is_allowed("beta") is False


# ---------------------------------------------------------------------------
# 3. Team scope
# ---------------------------------------------------------------------------


def test_team_scope_allowed_for_registered_team():
    ss.register_scope("alpha_tool", scope="team", team="coders")
    assert ss.is_allowed("alpha_tool", team="coders") is True


def test_team_scope_denied_other_team():
    ss.register_scope("alpha_tool", scope="team", team="coders")
    assert ss.is_allowed("alpha_tool", team="traders") is False


def test_team_scope_denied_no_team():
    ss.register_scope("alpha_tool", scope="team", team="coders")
    assert ss.is_allowed("alpha_tool") is False


# ---------------------------------------------------------------------------
# 4. Disabled scope
# ---------------------------------------------------------------------------


def test_disabled_never_allowed():
    ss.register_scope("dead_skill", scope="disabled")
    assert ss.is_allowed("dead_skill") is False
    assert ss.is_allowed("dead_skill", chat_id=42) is False
    assert ss.is_allowed("dead_skill", team="creative") is False


# ---------------------------------------------------------------------------
# 5. Unknown skill — allowed by default (no gate)
# ---------------------------------------------------------------------------


def test_unknown_skill_allowed():
    assert ss.is_allowed("nonexistent_skill") is True
    assert ss.is_allowed("nonexistent_skill", chat_id=1) is True
    assert ss.is_allowed("nonexistent_skill", team="analysts") is True


# ---------------------------------------------------------------------------
# 6. list_for_scope
# ---------------------------------------------------------------------------


def test_list_for_scope_global():
    ss.register_scope("s1")
    ss.register_scope("s2", scope="disabled")
    ss.register_scope("s3", scope="chat", chat_ids=[7])
    result = ss.list_for_scope()
    assert "s1" in result
    assert "s2" not in result  # disabled
    assert "s3" not in result  # chat scope, no chat_id given


def test_list_for_scope_with_chat():
    ss.register_scope("s1")
    ss.register_scope("s3", scope="chat", chat_ids=[7])
    result = ss.list_for_scope(chat_id=7)
    assert "s1" in result
    assert "s3" in result


def test_list_for_scope_with_team():
    ss.register_scope("s1")
    ss.register_scope("t1", scope="team", team="analysts")
    result = ss.list_for_scope(team="analysts")
    assert "t1" in result
    assert "s1" in result


def test_list_for_scope_sorted():
    ss.register_scope("z_skill")
    ss.register_scope("a_skill")
    result = ss.list_for_scope()
    assert result == sorted(result)


# ---------------------------------------------------------------------------
# 7. Persist: write + reload round-trip
# ---------------------------------------------------------------------------


def test_persist_and_reload(tmp_path, monkeypatch):
    path = tmp_path / "skill_scopes.json"
    monkeypatch.setattr(ss, "_PERSIST_PATH", path)
    ss.reset()

    ss.register_scope("feat_a", scope="chat", chat_ids=[10, 20])
    ss.register_scope("feat_b", scope="team", team="traders")
    ss.register_scope("feat_c", scope="disabled")

    # Simulate fresh load: clear in-memory state but keep persist file
    ss._registry = {}
    ss._loaded = False

    assert ss.is_allowed("feat_a", chat_id=10) is True
    assert ss.is_allowed("feat_a", chat_id=99) is False
    assert ss.is_allowed("feat_b", team="traders") is True
    assert ss.is_allowed("feat_c") is False


def test_persist_file_is_valid_json(tmp_path, monkeypatch):
    path = tmp_path / "skill_scopes.json"
    monkeypatch.setattr(ss, "_PERSIST_PATH", path)
    ss.reset()

    ss.register_scope("x", scope="chat", chat_ids=[1, 2, 3])
    data = json.loads(path.read_text())
    assert isinstance(data, list)
    assert data[0]["skill_name"] == "x"
    assert data[0]["scope"] == "chat"
    assert set(data[0]["chat_ids"]) == {1, 2, 3}


# ---------------------------------------------------------------------------
# 8. Thread safety
# ---------------------------------------------------------------------------


def test_thread_safe_concurrent_register():
    errors: list[Exception] = []

    def worker(n: int) -> None:
        try:
            for i in range(20):
                ss.register_scope(f"skill_{n}_{i}", scope="global")
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    # All registered skills should be globally allowed
    for n in range(5):
        for i in range(20):
            assert ss.is_allowed(f"skill_{n}_{i}") is True


def test_thread_safe_concurrent_read_write():
    ss.register_scope("shared", scope="chat", chat_ids=[1])
    errors: list[Exception] = []

    def reader() -> None:
        try:
            for _ in range(50):
                ss.is_allowed("shared", chat_id=1)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    def writer() -> None:
        try:
            for i in range(10):
                ss.register_scope("shared", scope="chat", chat_ids=[i])
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=reader) for _ in range(4)]
    threads.append(threading.Thread(target=writer))
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors


# ---------------------------------------------------------------------------
# 9. reset() clears everything
# ---------------------------------------------------------------------------


def test_reset_clears_registry():
    ss.register_scope("s1", scope="disabled")
    ss.reset()
    # After reset, unknown → allowed
    assert ss.is_allowed("s1") is True


def test_reset_then_reregister():
    ss.register_scope("s1", scope="disabled")
    ss.reset()
    ss.register_scope("s1", scope="global")
    assert ss.is_allowed("s1") is True


# ---------------------------------------------------------------------------
# 10. register_scope overwrite (idempotent update)
# ---------------------------------------------------------------------------


def test_overwrite_scope():
    ss.register_scope("feat", scope="global")
    assert ss.is_allowed("feat", chat_id=42) is True

    ss.register_scope("feat", scope="disabled")
    assert ss.is_allowed("feat", chat_id=42) is False
