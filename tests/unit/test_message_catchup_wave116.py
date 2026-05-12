# -*- coding: utf-8 -*-
"""Wave 116: Prometheus instrumentation для startup catchup.

Покрытие:
- catchup_message_processed Counter: processed/skipped/error
- catchup_age_seconds Histogram: observed на replay'е
- startup_catchup_completed_ts Gauge: set после multi-chat run
- catchup_failures_total Counter: fetch/replay/chat/unexpected stages
- метрики no-op safe при prometheus_client missing (import работает без exc)
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from src.core.metrics import catchup as metrics_catchup
from src.userbot.message_catchup import MessageCatchupMixin


class _Host(MessageCatchupMixin):
    """Минимальный host для unit-теста mixin'а."""

    def __init__(self, state_path: Path):
        self._state_path = state_path
        self.client = MagicMock()
        self.me = None
        self._owner_notify_target: int | str = 12345
        self._processed: list[Any] = []
        self._should_fail_msg_id: int | None = None

    def _last_seen_state_path(self) -> Path:
        return self._state_path

    async def _process_message(self, message):
        # Симулируем replay failure для specific msg_id.
        if (
            self._should_fail_msg_id is not None
            and getattr(message, "id", 0) == self._should_fail_msg_id
        ):
            raise RuntimeError("replay boom")
        self._processed.append(message)


def _mk_msg(
    msg_id: int,
    *,
    outgoing: bool = False,
    from_self: bool = False,
    age_seconds: float | None = None,
) -> MagicMock:
    m = MagicMock()
    m.id = msg_id
    m.outgoing = outgoing
    if from_self:
        from_user = MagicMock()
        from_user.is_self = True
        m.from_user = from_user
    else:
        m.from_user = None
    if age_seconds is not None:
        m.date = datetime.fromtimestamp(
            time.time() - age_seconds, tz=timezone.utc
        )
    else:
        m.date = None
    return m


def _make_history(msgs: list[Any]):
    async def _gen(*_args, **_kwargs):
        for m in msgs:
            yield m

    return _gen


def _counter_value(counter, labels: dict[str, str]) -> float:
    """Снимок значения Counter с указанными labels."""
    if counter is None:
        return 0.0
    try:
        return counter.labels(**labels)._value.get()  # type: ignore[attr-defined]
    except Exception:
        return 0.0


def _histogram_sample_count(histogram, labels: dict[str, str]) -> float:
    if histogram is None:
        return 0.0
    try:
        return histogram.labels(**labels)._sum.get()  # type: ignore[attr-defined]
    except Exception:
        return 0.0


@pytest.fixture
def host(tmp_path: Path) -> _Host:
    return _Host(tmp_path / "last_seen.json")


@pytest.mark.asyncio
async def test_catchup_records_processed_counter(host: _Host) -> None:
    """processed messages инкрементят Counter с status=processed."""
    msgs = [_mk_msg(1, age_seconds=10.0), _mk_msg(2, age_seconds=20.0)]
    host.client.get_chat_history = _make_history(msgs)

    before = _counter_value(
        metrics_catchup.krab_catchup_message_processed_total,
        {"chat_id": "12345", "status": "processed"},
    )
    res = await host._catchup_chat_history(12345)
    after = _counter_value(
        metrics_catchup.krab_catchup_message_processed_total,
        {"chat_id": "12345", "status": "processed"},
    )

    assert res["caught_up"] == 2
    # 2 processed messages дали +2 (если prometheus_client доступен).
    if metrics_catchup.krab_catchup_message_processed_total is not None:
        assert after - before == pytest.approx(2.0)


@pytest.mark.asyncio
async def test_catchup_records_skipped_self_counter(host: _Host) -> None:
    """Self-сообщения учитываются как status=skipped."""
    msgs = [_mk_msg(10, from_self=True), _mk_msg(11, outgoing=True)]
    host.client.get_chat_history = _make_history(msgs)

    before = _counter_value(
        metrics_catchup.krab_catchup_message_processed_total,
        {"chat_id": "12345", "status": "skipped"},
    )
    await host._catchup_chat_history(12345)
    after = _counter_value(
        metrics_catchup.krab_catchup_message_processed_total,
        {"chat_id": "12345", "status": "skipped"},
    )

    if metrics_catchup.krab_catchup_message_processed_total is not None:
        assert after - before == pytest.approx(2.0)


@pytest.mark.asyncio
async def test_catchup_records_error_counter_and_failure(host: _Host) -> None:
    """replay failure → status=error counter + failures_total{stage=replay}."""
    host._should_fail_msg_id = 5
    msgs = [_mk_msg(5, age_seconds=5.0), _mk_msg(6, age_seconds=6.0)]
    host.client.get_chat_history = _make_history(msgs)

    err_before = _counter_value(
        metrics_catchup.krab_catchup_message_processed_total,
        {"chat_id": "12345", "status": "error"},
    )
    fail_before = _counter_value(
        metrics_catchup.krab_startup_catchup_failures_total,
        {"stage": "replay"},
    )
    await host._catchup_chat_history(12345)
    err_after = _counter_value(
        metrics_catchup.krab_catchup_message_processed_total,
        {"chat_id": "12345", "status": "error"},
    )
    fail_after = _counter_value(
        metrics_catchup.krab_startup_catchup_failures_total,
        {"stage": "replay"},
    )

    if metrics_catchup.krab_catchup_message_processed_total is not None:
        assert err_after - err_before == pytest.approx(1.0)
        assert fail_after - fail_before == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_catchup_records_age_histogram(host: _Host) -> None:
    """age_seconds observable для replay'ed сообщения с datetime."""
    msgs = [_mk_msg(20, age_seconds=42.0)]
    host.client.get_chat_history = _make_history(msgs)

    sum_before = _histogram_sample_count(
        metrics_catchup.krab_catchup_age_seconds, {"chat_id": "12345"}
    )
    await host._catchup_chat_history(12345)
    sum_after = _histogram_sample_count(
        metrics_catchup.krab_catchup_age_seconds, {"chat_id": "12345"}
    )

    if metrics_catchup.krab_catchup_age_seconds is not None:
        # Дельта суммы должна быть приблизительно 42s (с допуском на сон ОС).
        assert sum_after - sum_before == pytest.approx(42.0, abs=5.0)


@pytest.mark.asyncio
async def test_catchup_fetch_failure_records_metric(host: _Host) -> None:
    """get_chat_history raise → failures_total{stage=fetch} +1."""

    async def _bad_history(*_a, **_kw):
        raise ConnectionError("boom")
        yield  # pragma: no cover

    host.client.get_chat_history = _bad_history

    before = _counter_value(
        metrics_catchup.krab_startup_catchup_failures_total, {"stage": "fetch"}
    )
    res = await host._catchup_chat_history(12345)
    after = _counter_value(
        metrics_catchup.krab_startup_catchup_failures_total, {"stage": "fetch"}
    )

    assert res["caught_up"] == 0
    if metrics_catchup.krab_startup_catchup_failures_total is not None:
        assert after - before == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_multi_catchup_marks_completed_gauge(
    host: _Host, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_catchup_all_owner_chats успешный → Gauge updated с time.time()."""
    msgs = [_mk_msg(50, age_seconds=1.0)]
    host.client.get_chat_history = _make_history(msgs)
    # Один target chat — owner DM.
    monkeypatch.setenv("KRAB_STARTUP_CATCHUP_CHATS", "12345")

    before_ts = time.time()
    await host._catchup_all_owner_chats()
    after_ts = time.time()

    if metrics_catchup.krab_startup_catchup_completed_ts is not None:
        gauge_val = metrics_catchup.krab_startup_catchup_completed_ts._value.get()  # type: ignore[attr-defined]
        assert before_ts - 1.0 <= gauge_val <= after_ts + 1.0


def test_metrics_helpers_no_op_safe() -> None:
    """Все helpers безопасны для bad input + повторных вызовов."""
    # Не должно raise при некорректных значениях.
    metrics_catchup.record_catchup_message("not_a_number", "processed")
    metrics_catchup.record_catchup_message(99, "weird_status_value_that_is_long" * 10)
    metrics_catchup.record_catchup_age("xyz", -100.0)
    metrics_catchup.record_catchup_age(1, float("nan"))
    metrics_catchup.mark_catchup_completed(0.0)
    metrics_catchup.record_catchup_failure("")
    metrics_catchup.record_catchup_failure("custom_stage")
