# -*- coding: utf-8 -*-
"""Unit tests for src/core/chat_response_policy.py (Smart Routing Phase 1)."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from src.core.chat_response_policy import (
    ChatMode,
    ChatResponsePolicy,
    ChatResponsePolicyStore,
)


@pytest.fixture
def store_path(tmp_path: Path) -> Path:
    return tmp_path / "chat_response_policies.json"


@pytest.fixture
def store(store_path: Path) -> ChatResponsePolicyStore:
    return ChatResponsePolicyStore(path=store_path)


# ── ChatMode ────────────────────────────────────────────────


def test_chatmode_default_is_normal():
    assert ChatMode.default() is ChatMode.NORMAL


def test_chatmode_default_thresholds():
    assert ChatMode.SILENT.default_threshold() == 1.1
    assert ChatMode.CAUTIOUS.default_threshold() == 0.7
    assert ChatMode.NORMAL.default_threshold() == 0.5
    assert ChatMode.CHATTY.default_threshold() == 0.3


def test_silent_threshold_above_max_score():
    # silent должен быть выше любого realistic score (0..1)
    assert ChatMode.SILENT.default_threshold() > 1.0


# ── ChatResponsePolicy dataclass ────────────────────────────


def test_policy_effective_threshold_uses_mode_default():
    p = ChatResponsePolicy(chat_id="1", mode=ChatMode.CAUTIOUS)
    assert p.effective_threshold() == 0.7


def test_policy_effective_threshold_uses_override():
    p = ChatResponsePolicy(chat_id="1", mode=ChatMode.CAUTIOUS, threshold_override=0.42)
    assert p.effective_threshold() == 0.42


def test_policy_to_dict_from_dict_roundtrip():
    p = ChatResponsePolicy(
        chat_id="-100123",
        mode=ChatMode.CHATTY,
        threshold_override=0.25,
        negative_signals=2,
        positive_signals=7,
        last_negative_ts=1000.0,
        last_positive_ts=2000.0,
        auto_adjust_enabled=False,
        blocked_topics=["politics", "crypto"],
        notes="test",
    )
    data = p.to_dict()
    assert data["mode"] == "chatty"
    p2 = ChatResponsePolicy.from_dict(data)
    assert p2.chat_id == "-100123"
    assert p2.mode is ChatMode.CHATTY
    assert p2.threshold_override == 0.25
    assert p2.negative_signals == 2
    assert p2.positive_signals == 7
    assert p2.blocked_topics == ["politics", "crypto"]
    assert p2.auto_adjust_enabled is False
    assert p2.notes == "test"


def test_policy_from_dict_invalid_mode_falls_back_to_normal():
    p = ChatResponsePolicy.from_dict({"chat_id": "5", "mode": "absurd"})
    assert p.mode is ChatMode.NORMAL


# ── Store CRUD ──────────────────────────────────────────────


def test_get_policy_returns_default_for_unknown(store: ChatResponsePolicyStore):
    p = store.get_policy(42)
    assert p.chat_id == "42"
    assert p.mode is ChatMode.NORMAL
    assert p.negative_signals == 0
    # default не должен персиститься
    assert store.list_all() == []


def test_update_policy_persists(store: ChatResponsePolicyStore, store_path: Path):
    store.update_policy(123, mode=ChatMode.CAUTIOUS, notes="quiet chat")
    assert store_path.exists()
    raw = json.loads(store_path.read_text())
    assert "123" in raw
    assert raw["123"]["mode"] == "cautious"
    assert raw["123"]["notes"] == "quiet chat"


def test_update_policy_accepts_string_mode(store: ChatResponsePolicyStore):
    p = store.update_policy("9", mode="chatty")
    assert p.mode is ChatMode.CHATTY


def test_update_policy_invalid_mode_silently_skipped(store: ChatResponsePolicyStore):
    p = store.update_policy("9", mode="bogus", notes="ok")
    assert p.mode is ChatMode.NORMAL  # default kept
    assert p.notes == "ok"


def test_load_existing_file(store_path: Path):
    s1 = ChatResponsePolicyStore(path=store_path)
    s1.update_policy("777", mode=ChatMode.CHATTY, threshold_override=0.2)
    s2 = ChatResponsePolicyStore(path=store_path)
    p = s2.get_policy("777")
    assert p.mode is ChatMode.CHATTY
    assert p.threshold_override == 0.2


def test_reset_policy_removes(store: ChatResponsePolicyStore):
    store.update_policy("5", mode=ChatMode.SILENT)
    assert store.reset_policy("5") is True
    assert store.list_all() == []
    assert store.reset_policy("5") is False  # idempotent


def test_list_all_sorted_by_chat_id(store: ChatResponsePolicyStore):
    store.update_policy("3", mode=ChatMode.CHATTY)
    store.update_policy("1", mode=ChatMode.CAUTIOUS)
    store.update_policy("2", mode=ChatMode.NORMAL)
    ids = [p.chat_id for p in store.list_all()]
    assert ids == ["1", "2", "3"]


# ── Signals ─────────────────────────────────────────────────


def test_record_negative_signal_increments(store: ChatResponsePolicyStore):
    p = store.record_negative_signal(10, reason="user complained")
    assert p.negative_signals == 1
    assert p.last_negative_ts is not None
    p2 = store.record_negative_signal(10)
    assert p2.negative_signals == 2


def test_record_positive_signal_increments(store: ChatResponsePolicyStore):
    p = store.record_positive_signal(10, reason="thanks")
    assert p.positive_signals == 1
    assert p.last_positive_ts is not None


# ── Auto-adjust ─────────────────────────────────────────────


def test_auto_adjust_downshift_normal_to_cautious(store: ChatResponsePolicyStore):
    # 6 negatives → mode сдвигается NORMAL → CAUTIOUS
    for _ in range(6):
        store.record_negative_signal("100")
    p = store.get_policy("100")
    assert p.mode is ChatMode.CAUTIOUS
    assert p.last_auto_adjust_ts is not None


def test_auto_adjust_downshift_chatty_to_normal(store: ChatResponsePolicyStore):
    store.update_policy("200", mode=ChatMode.CHATTY)
    for _ in range(6):
        store.record_negative_signal("200")
    assert store.get_policy("200").mode is ChatMode.NORMAL


def test_auto_adjust_upshift_normal_to_chatty(store: ChatResponsePolicyStore):
    # 11 positives + 0 negatives → NORMAL → CHATTY
    for _ in range(11):
        store.record_positive_signal("300")
    p = store.get_policy("300")
    assert p.mode is ChatMode.CHATTY


def test_auto_adjust_upshift_cautious_to_normal(store: ChatResponsePolicyStore):
    store.update_policy("301", mode=ChatMode.CAUTIOUS)
    for _ in range(11):
        store.record_positive_signal("301")
    assert store.get_policy("301").mode is ChatMode.NORMAL


def test_auto_adjust_no_upshift_when_recent_negatives(store: ChatResponsePolicyStore):
    # 1 negative, потом 11 positives — upshift не должен сработать
    store.record_negative_signal("400")
    for _ in range(11):
        store.record_positive_signal("400")
    assert store.get_policy("400").mode is ChatMode.NORMAL


def test_auto_adjust_silent_locked(store: ChatResponsePolicyStore):
    # SILENT не должен двигаться авто
    store.update_policy("500", mode=ChatMode.SILENT)
    for _ in range(20):
        store.record_negative_signal("500")
    assert store.get_policy("500").mode is ChatMode.SILENT


def test_auto_adjust_rate_limited_within_6h(store: ChatResponsePolicyStore):
    # Первый downshift NORMAL → CAUTIOUS
    for _ in range(6):
        store.record_negative_signal("600")
    p = store.get_policy("600")
    assert p.mode is ChatMode.CAUTIOUS
    first_adjust_ts = p.last_auto_adjust_ts

    # Ещё 6 negatives сразу — повторного перехода НЕ должно быть (rate limit 6h),
    # т.к. CAUTIOUS → ... отсутствует в downshift map для негативов;
    # проверим, что при попытке upshift в течение 6h тоже rate-limit.
    # Для надёжной проверки rate-limit вручную сбросим mode но оставим last_auto_adjust_ts
    store.update_policy("600", mode=ChatMode.NORMAL)
    # Сбросим счётчики — они не влияют на rate-limit
    p2 = store.get_policy("600")
    p2.last_auto_adjust_ts = first_adjust_ts  # имитируем "недавнюю" подстройку
    # Принудительно через API-обновление сохранить
    store.update_policy("600", last_auto_adjust_ts=first_adjust_ts)

    for _ in range(11):
        store.record_positive_signal("600")
    # Должен остаться NORMAL — rate-limit не дал upshift
    assert store.get_policy("600").mode is ChatMode.NORMAL


def test_auto_adjust_disabled(store: ChatResponsePolicyStore):
    store.update_policy("700", auto_adjust_enabled=False)
    for _ in range(20):
        store.record_negative_signal("700")
    assert store.get_policy("700").mode is ChatMode.NORMAL


def test_auto_adjust_old_negatives_outside_window(store: ChatResponsePolicyStore):
    # 6 negatives, но их last_negative_ts искусственно состарим > 24h
    for _ in range(6):
        store.record_negative_signal("800")
    # На этот момент уже произошёл downshift; сбросим обратно и состарим
    store.update_policy(
        "800",
        mode=ChatMode.NORMAL,
        last_negative_ts=time.time() - (25 * 3600),
        last_auto_adjust_ts=None,
    )
    # Триггерим _maybe_auto_adjust через positive signal (он тоже вызывает adjust)
    # — но negative_ts вне окна, и positives мало → ничего не меняется
    p = store.record_positive_signal("800")
    assert p.mode is ChatMode.NORMAL


# ── Persistence / file integrity ────────────────────────────


def test_persist_atomic_no_tmp_left(store: ChatResponsePolicyStore, store_path: Path):
    store.update_policy("1", mode=ChatMode.CHATTY)
    tmp = store_path.with_suffix(store_path.suffix + ".tmp")
    assert not tmp.exists()
    assert store_path.exists()


def test_load_corrupted_file_does_not_crash(tmp_path: Path):
    p = tmp_path / "broken.json"
    p.write_text("{this is not json")
    s = ChatResponsePolicyStore(path=p)
    assert s.list_all() == []
    # Запись должна работать после восстановления
    s.update_policy("1", mode=ChatMode.NORMAL)
    assert s.get_policy("1").mode is ChatMode.NORMAL


# ── Thread-safety ───────────────────────────────────────────


def test_concurrent_writes_dont_corrupt(tmp_path: Path):
    p = tmp_path / "concurrent.json"
    s = ChatResponsePolicyStore(path=p)

    def worker(chat_id: int, signals: int):
        for _ in range(signals):
            s.record_positive_signal(chat_id)

    threads = [threading.Thread(target=worker, args=(i, 20)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Файл должен быть валидным JSON, и каждый чат должен иметь 20 positives
    raw = json.loads(p.read_text())
    assert len(raw) == 5
    for cid in range(5):
        assert raw[str(cid)]["positive_signals"] == 20
