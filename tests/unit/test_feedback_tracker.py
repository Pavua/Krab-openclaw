"""Unit-тесты для `src/core/feedback_tracker.py` (Smart Routing Phase 3)."""

from __future__ import annotations

import asyncio
import threading
import time

import pytest

from src.core.chat_response_policy import ChatResponsePolicyStore
from src.core.feedback_tracker import (
    FEEDBACK_WINDOW_SEC,
    RECENT_RESPONSES_MAX,
    FeedbackTracker,
    KrabResponse,
    get_tracker,
    reset_tracker_for_tests,
)

OWNER_ID = 12345
OTHER_ID = 99999


# ---------- fixtures ----------


@pytest.fixture
def policy_store(tmp_path) -> ChatResponsePolicyStore:
    """Чистый policy_store на изолированном tmp-файле."""
    return ChatResponsePolicyStore(path=tmp_path / "policy.json")


@pytest.fixture
def tracker(policy_store) -> FeedbackTracker:
    return FeedbackTracker(policy_store=policy_store, owner_user_id=OWNER_ID)


def _resp(chat_id="-100", message_id=1, decision_path="hard_gate", age_sec=0.0) -> KrabResponse:
    return KrabResponse(
        chat_id=str(chat_id),
        message_id=message_id,
        sent_at=time.time() - age_sec,
        decision_path=decision_path,
        confidence=1.0,
    )


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro) if False else asyncio.run(coro)


# ---------- 1: record_krab_response ----------


def test_record_krab_response_stores(tracker):
    tracker.record_krab_response(_resp(chat_id="-1001", message_id=42))
    snapshot = tracker.stats()
    assert snapshot["tracked_responses"] == 1
    assert snapshot["owner_id_set"] is True


def test_record_krab_response_multiple_distinct_keys(tracker):
    tracker.record_krab_response(_resp(message_id=1))
    tracker.record_krab_response(_resp(message_id=2))
    tracker.record_krab_response(_resp(chat_id="-2", message_id=1))
    assert tracker.stats()["tracked_responses"] == 3


# ---------- 2: on_message_deleted ----------


def test_owner_deleted_krab_reply_records_negative(tracker, policy_store):
    tracker.record_krab_response(_resp(chat_id="-100", message_id=10))
    result = run(tracker.on_message_deleted("-100", 10, deleted_by=OWNER_ID))
    assert result is True
    policy = policy_store.get_policy("-100")
    assert policy.negative_signals == 1
    assert policy.last_negative_ts is not None
    # entry должен быть удалён из tracker'а
    assert tracker.stats()["tracked_responses"] == 0


def test_non_owner_delete_ignored(tracker, policy_store):
    tracker.record_krab_response(_resp(chat_id="-100", message_id=10))
    result = run(tracker.on_message_deleted("-100", 10, deleted_by=OTHER_ID))
    assert result is False
    policy = policy_store.get_policy("-100")
    assert policy.negative_signals == 0
    # entry должен остаться (мы не делали pop)
    assert tracker.stats()["tracked_responses"] == 1


def test_delete_untracked_message_ignored(tracker, policy_store):
    result = run(tracker.on_message_deleted("-100", 999, deleted_by=OWNER_ID))
    assert result is False
    assert policy_store.get_policy("-100").negative_signals == 0


def test_delete_outside_window_ignored(tracker, policy_store):
    tracker.record_krab_response(
        _resp(chat_id="-100", message_id=10, age_sec=FEEDBACK_WINDOW_SEC + 60)
    )
    result = run(tracker.on_message_deleted("-100", 10, deleted_by=OWNER_ID))
    assert result is False
    assert policy_store.get_policy("-100").negative_signals == 0


def test_delete_with_unknown_deleted_by_uses_window(tracker, policy_store):
    """Если Pyrogram не отдаёт deleted_by (None) — считаем feedback'ом owner'а."""
    tracker.record_krab_response(_resp(chat_id="-100", message_id=10))
    result = run(tracker.on_message_deleted("-100", 10, deleted_by=None))
    assert result is True
    assert policy_store.get_policy("-100").negative_signals == 1


# ---------- 3: on_reaction_added ----------


def test_owner_negative_reaction(tracker, policy_store):
    tracker.record_krab_response(_resp(chat_id="-100", message_id=10))
    result = run(tracker.on_reaction_added("-100", 10, "👎", user_id=OWNER_ID))
    assert result is True
    assert policy_store.get_policy("-100").negative_signals == 1
    # entry остаётся (реакцию могут заменить — не pop'аем)
    assert tracker.stats()["tracked_responses"] == 1


def test_owner_positive_reaction(tracker, policy_store):
    tracker.record_krab_response(_resp(chat_id="-100", message_id=10))
    result = run(tracker.on_reaction_added("-100", 10, "👍", user_id=OWNER_ID))
    assert result is True
    policy = policy_store.get_policy("-100")
    assert policy.positive_signals == 1
    assert policy.negative_signals == 0


def test_owner_neutral_reaction_ignored(tracker, policy_store):
    tracker.record_krab_response(_resp(chat_id="-100", message_id=10))
    result = run(tracker.on_reaction_added("-100", 10, "🤔", user_id=OWNER_ID))
    assert result is False
    policy = policy_store.get_policy("-100")
    assert policy.negative_signals == 0
    assert policy.positive_signals == 0


def test_non_owner_reaction_ignored(tracker, policy_store):
    tracker.record_krab_response(_resp(chat_id="-100", message_id=10))
    result = run(tracker.on_reaction_added("-100", 10, "👎", user_id=OTHER_ID))
    assert result is False
    assert policy_store.get_policy("-100").negative_signals == 0


def test_reaction_outside_window_ignored(tracker, policy_store):
    tracker.record_krab_response(
        _resp(chat_id="-100", message_id=10, age_sec=FEEDBACK_WINDOW_SEC + 60)
    )
    result = run(tracker.on_reaction_added("-100", 10, "👎", user_id=OWNER_ID))
    assert result is False
    assert policy_store.get_policy("-100").negative_signals == 0


def test_owner_id_not_set_blocks_all(policy_store):
    tracker = FeedbackTracker(policy_store=policy_store, owner_user_id=None)
    tracker.record_krab_response(_resp(chat_id="-100", message_id=10))
    assert run(tracker.on_message_deleted("-100", 10, deleted_by=OWNER_ID)) is False
    assert run(tracker.on_reaction_added("-100", 10, "👎", user_id=OWNER_ID)) is False
    assert policy_store.get_policy("-100").negative_signals == 0


# ---------- 4: lazy owner_id setter ----------


def test_owner_id_lazy_set(policy_store):
    tracker = FeedbackTracker(policy_store=policy_store, owner_user_id=None)
    assert tracker.stats()["owner_id_set"] is False
    tracker.set_owner_id(OWNER_ID)
    assert tracker.stats()["owner_id_set"] is True

    tracker.record_krab_response(_resp(chat_id="-100", message_id=10))
    assert run(tracker.on_reaction_added("-100", 10, "👎", user_id=OWNER_ID)) is True


# ---------- 5: eviction ----------


def test_evict_old_age_based(tracker):
    # Добавляем старую запись (за пределом окна) и свежую
    tracker.record_krab_response(
        _resp(chat_id="-1", message_id=1, age_sec=FEEDBACK_WINDOW_SEC + 60)
    )
    tracker.record_krab_response(_resp(chat_id="-1", message_id=2))
    # eviction срабатывает на каждом record_krab_response
    snap = tracker.stats()
    # Старая запись должна быть выселена
    assert snap["tracked_responses"] == 1


def test_evict_count_based(policy_store, monkeypatch):
    """С искусственно низким cap проверяем count-based eviction."""
    monkeypatch.setattr("src.core.feedback_tracker.RECENT_RESPONSES_MAX", 3)
    tracker = FeedbackTracker(policy_store=policy_store, owner_user_id=OWNER_ID)
    for i in range(5):
        tracker.record_krab_response(_resp(chat_id="-1", message_id=i))
    # Cap не превышен (3 вместо 5)
    assert tracker.stats()["tracked_responses"] <= 3


# ---------- 6: thread safety ----------


def test_thread_safe_record(tracker):
    def writer(start: int):
        for i in range(50):
            tracker.record_krab_response(_resp(chat_id="-1", message_id=start * 100 + i))

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # 4 потока × 50 уникальных = 200 записей
    assert tracker.stats()["tracked_responses"] == 200


# ---------- 7: stats snapshot ----------


def test_stats_returns_correct_snapshot(tracker):
    snap = tracker.stats()
    assert snap["tracked_responses"] == 0
    assert snap["owner_id_set"] is True
    assert snap["feedback_window_sec"] == FEEDBACK_WINDOW_SEC
    assert "👎" in snap["negative_reactions"]
    assert "👍" in snap["positive_reactions"]
    # Нет пересечений между positive и negative
    assert set(snap["negative_reactions"]).isdisjoint(set(snap["positive_reactions"]))


# ---------- 8: Phase 1 integration ----------


def test_phase1_integration_persisted(tmp_path):
    """После delete signal должен быть записан и в JSON-файл (persistence)."""
    store_path = tmp_path / "policy.json"
    store = ChatResponsePolicyStore(path=store_path)
    tracker = FeedbackTracker(policy_store=store, owner_user_id=OWNER_ID)

    tracker.record_krab_response(_resp(chat_id="-555", message_id=10))
    assert run(tracker.on_message_deleted("-555", 10, deleted_by=OWNER_ID)) is True

    # Перезагружаем store с диска — данные должны сохраниться
    fresh = ChatResponsePolicyStore(path=store_path)
    policy = fresh.get_policy("-555")
    assert policy.negative_signals == 1
    assert policy.last_negative_ts is not None


# ---------- 9: singleton ----------


def test_get_tracker_singleton():
    reset_tracker_for_tests()
    t1 = get_tracker()
    t2 = get_tracker()
    assert t1 is t2
    reset_tracker_for_tests()


# ---------- 10: multiple negative reactions accumulate ----------


def test_multiple_negative_reactions_accumulate(tracker, policy_store):
    for emoji in ("👎", "🤡", "💩"):
        tracker.record_krab_response(_resp(chat_id="-100", message_id=hash(emoji) & 0xFFFF))
        run(tracker.on_reaction_added("-100", hash(emoji) & 0xFFFF, emoji, user_id=OWNER_ID))
    assert policy_store.get_policy("-100").negative_signals == 3
