# -*- coding: utf-8 -*-
"""Wave 177: tests для typing indicator metrics + wiring в TypingIndicator."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from src.core.metrics.typing_indicator import (
    _chat_bucket,
    _normalize_action,
    _normalize_reason,
    record_typing_cancelled,
    record_typing_floodwait,
    record_typing_started,
)

# ---------------------------------------------------------------------------
# Fixture: подмена фасадных метрик MagicMock'ами
# ---------------------------------------------------------------------------


@pytest.fixture
def patched_facade(monkeypatch):
    """Patch prometheus_metrics facade со счётчиками/histogram'ом MagicMock."""
    import src.core.prometheus_metrics as pm

    started = MagicMock()
    cancelled = MagicMock()
    duration = MagicMock()
    floodwait = MagicMock()
    monkeypatch.setattr(pm, "krab_typing_indicator_started_total", started, raising=False)
    monkeypatch.setattr(pm, "krab_typing_indicator_cancelled_total", cancelled, raising=False)
    monkeypatch.setattr(pm, "krab_typing_indicator_duration_seconds", duration, raising=False)
    monkeypatch.setattr(pm, "krab_typing_indicator_floodwait_total", floodwait, raising=False)
    return started, cancelled, duration, floodwait


# ---------------------------------------------------------------------------
# record_typing_started
# ---------------------------------------------------------------------------


def test_record_started_increments_counter_typing(patched_facade):
    started, _cancelled, _duration, _floodwait = patched_facade
    record_typing_started("typing")
    started.labels.assert_called_once_with(action="typing")
    started.labels.return_value.inc.assert_called_once()


def test_record_started_recording_voice(patched_facade):
    started, *_ = patched_facade
    record_typing_started("recording_voice")
    started.labels.assert_called_once_with(action="recording_voice")


def test_record_started_upload_photo(patched_facade):
    started, *_ = patched_facade
    record_typing_started("upload_photo")
    started.labels.assert_called_once_with(action="upload_photo")


def test_record_started_upload_doc(patched_facade):
    started, *_ = patched_facade
    record_typing_started("upload_doc")
    started.labels.assert_called_once_with(action="upload_doc")


def test_record_started_unknown_normalized(patched_facade):
    started, *_ = patched_facade
    record_typing_started("garbage_action_value")
    started.labels.assert_called_once_with(action="unknown")


def test_record_started_none_normalized_to_unknown(patched_facade):
    started, *_ = patched_facade
    record_typing_started(None)  # type: ignore[arg-type]
    started.labels.assert_called_once_with(action="unknown")


# ---------------------------------------------------------------------------
# record_typing_cancelled — reasons + duration histogram
# ---------------------------------------------------------------------------


def test_record_cancelled_success(patched_facade):
    _started, cancelled, duration, _floodwait = patched_facade
    record_typing_cancelled("success", 2.5)
    cancelled.labels.assert_called_once_with(reason="success")
    cancelled.labels.return_value.inc.assert_called_once()
    duration.observe.assert_called_once_with(2.5)


def test_record_cancelled_error(patched_facade):
    _started, cancelled, duration, _floodwait = patched_facade
    record_typing_cancelled("error", 7.3)
    cancelled.labels.assert_called_once_with(reason="error")
    duration.observe.assert_called_once_with(7.3)


def test_record_cancelled_timeout(patched_facade):
    _started, cancelled, _duration, _floodwait = patched_facade
    record_typing_cancelled("timeout", 60.0)
    cancelled.labels.assert_called_once_with(reason="timeout")


def test_record_cancelled_floodwait(patched_facade):
    _started, cancelled, _duration, _floodwait = patched_facade
    record_typing_cancelled("floodwait", 1.0)
    cancelled.labels.assert_called_once_with(reason="floodwait")


def test_record_cancelled_invalid_reason_normalized_to_error(patched_facade):
    _started, cancelled, _duration, _floodwait = patched_facade
    record_typing_cancelled("garbage", 0.5)
    cancelled.labels.assert_called_once_with(reason="error")


def test_record_cancelled_negative_duration_clamped(patched_facade):
    _started, _cancelled, duration, _floodwait = patched_facade
    record_typing_cancelled("success", -3.0)
    duration.observe.assert_called_once_with(0.0)


# ---------------------------------------------------------------------------
# record_typing_floodwait — chat_id bucketing
# ---------------------------------------------------------------------------


def test_record_floodwait_increments_with_bucket(patched_facade):
    _started, _cancelled, _duration, floodwait = patched_facade
    record_typing_floodwait(123456789)
    assert floodwait.labels.call_count == 1
    # bucket — двузначная строка
    call_kwargs = floodwait.labels.call_args.kwargs
    bucket = call_kwargs["chat_id_bucket"]
    assert isinstance(bucket, str)
    assert len(bucket) == 2
    assert bucket.isdigit()
    floodwait.labels.return_value.inc.assert_called_once()


def test_record_floodwait_none_safe(patched_facade):
    _started, _cancelled, _duration, floodwait = patched_facade
    record_typing_floodwait(None)
    floodwait.labels.assert_called_once_with(chat_id_bucket="00")


def test_record_floodwait_string_chat_id(patched_facade):
    _started, _cancelled, _duration, floodwait = patched_facade
    record_typing_floodwait("krab_swarm")
    floodwait.labels.assert_called_once()
    bucket = floodwait.labels.call_args.kwargs["chat_id_bucket"]
    assert len(bucket) == 2 and bucket.isdigit()


def test_chat_bucket_stable_for_same_input():
    # Стабильность важна — иначе bucketing бесполезен (всё расползётся).
    # Внутри одного процесса hash() стабилен для строки.
    assert _chat_bucket(12345) == _chat_bucket(12345)
    assert _chat_bucket("foo") == _chat_bucket("foo")


def test_chat_bucket_in_range():
    for cid in (0, 1, -1, 123, 999999999, "x", "long_username_42"):
        b = _chat_bucket(cid)
        assert b.isdigit() and 0 <= int(b) <= 99


# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------


def test_normalize_action_whitelist():
    assert _normalize_action("typing") == "typing"
    assert _normalize_action("RECORDING_VOICE") == "recording_voice"
    assert _normalize_action(" upload_photo ") == "upload_photo"
    assert _normalize_action("upload_doc") == "upload_doc"
    assert _normalize_action("") == "unknown"
    assert _normalize_action(None) == "unknown"
    assert _normalize_action("hack_attempt") == "unknown"


def test_normalize_reason_whitelist():
    assert _normalize_reason("success") == "success"
    assert _normalize_reason("ERROR") == "error"
    assert _normalize_reason(" timeout") == "timeout"
    assert _normalize_reason("floodwait") == "floodwait"
    assert _normalize_reason(None) == "error"
    assert _normalize_reason("anything_else") == "error"


# ---------------------------------------------------------------------------
# Fail-safe: фасадные метрики == None (no prometheus_client)
# ---------------------------------------------------------------------------


def test_record_started_failsafe_when_facade_none(monkeypatch):
    import src.core.prometheus_metrics as pm

    monkeypatch.setattr(pm, "krab_typing_indicator_started_total", None, raising=False)
    record_typing_started("typing")  # Не должно бросить


def test_record_cancelled_failsafe_when_facade_none(monkeypatch):
    import src.core.prometheus_metrics as pm

    monkeypatch.setattr(pm, "krab_typing_indicator_cancelled_total", None, raising=False)
    monkeypatch.setattr(pm, "krab_typing_indicator_duration_seconds", None, raising=False)
    record_typing_cancelled("success", 1.0)


def test_record_floodwait_failsafe_when_facade_none(monkeypatch):
    import src.core.prometheus_metrics as pm

    monkeypatch.setattr(pm, "krab_typing_indicator_floodwait_total", None, raising=False)
    record_typing_floodwait(42)


# ---------------------------------------------------------------------------
# Facade re-exports (Wave 177 wiring в src/core/metrics/__init__.py)
# ---------------------------------------------------------------------------


def test_facade_reexports_typing_indicator_symbols():
    import src.core.prometheus_metrics as pm

    # Все четыре метрики экспортированы (None допустим — slim env).
    assert hasattr(pm, "krab_typing_indicator_started_total")
    assert hasattr(pm, "krab_typing_indicator_cancelled_total")
    assert hasattr(pm, "krab_typing_indicator_duration_seconds")
    assert hasattr(pm, "krab_typing_indicator_floodwait_total")
    # Helper'ы тоже.
    assert callable(pm.record_typing_started)
    assert callable(pm.record_typing_cancelled)
    assert callable(pm.record_typing_floodwait)


# ---------------------------------------------------------------------------
# TypingIndicator integration: start/cancel/floodwait wiring
# ---------------------------------------------------------------------------


class _FakeAction:
    """Минимальный stub для pyrogram.enums.ChatAction со свойством `name`."""

    def __init__(self, name: str) -> None:
        self.name = name


class _FakeClient:
    """Pyrogram Client stub — собираем call'ы и можем кидать FloodWait."""

    def __init__(self, *, raise_on_send: Exception | None = None) -> None:
        self.calls: list = []
        self._raise = raise_on_send

    async def send_chat_action(self, chat_id, action):
        self.calls.append((chat_id, action))
        if self._raise is not None:
            raise self._raise


@pytest.mark.asyncio
async def test_typing_indicator_records_started_and_success(monkeypatch, patched_facade):
    """Нормальный flow: __aenter__ → metric started, __aexit__(no exc) → success."""
    started, cancelled, duration, _floodwait = patched_facade
    from src.userbot.typing_indicator import TypingIndicator

    client = _FakeClient()
    action = _FakeAction("TYPING")
    async with TypingIndicator(client, 555, action=action, interval_sec=0.01, enabled=True):
        # Дать loop'у тикнуть, чтобы хотя бы один send_chat_action случился.
        await asyncio.sleep(0.02)

    started.labels.assert_called_with(action="typing")
    cancelled.labels.assert_called_with(reason="success")
    # duration observed (> 0).
    duration.observe.assert_called_once()
    observed_dur = duration.observe.call_args.args[0]
    assert observed_dur >= 0.0


@pytest.mark.asyncio
async def test_typing_indicator_records_error_on_body_exception(monkeypatch, patched_facade):
    """Тело блока бросает исключение → reason=error, exception пробрасывается."""
    _started, cancelled, _duration, _floodwait = patched_facade
    from src.userbot.typing_indicator import TypingIndicator

    client = _FakeClient()
    action = _FakeAction("TYPING")
    with pytest.raises(ValueError, match="boom"):
        async with TypingIndicator(client, 100, action=action, interval_sec=0.01, enabled=True):
            raise ValueError("boom")

    cancelled.labels.assert_called_with(reason="error")


@pytest.mark.asyncio
async def test_typing_indicator_records_floodwait_on_send_failure(monkeypatch, patched_facade):
    """send_chat_action кидает FloodWait → floodwait counter инкрементируется."""
    _started, _cancelled, _duration, floodwait = patched_facade
    from src.userbot.typing_indicator import TypingIndicator

    # Имитация FloodWait — класс должен называться "FloodWait" (_is_flood_wait чек).
    class FloodWait(Exception):  # noqa: N818 — намеренно совпадает с pyrogram.errors.FloodWait
        pass

    client = _FakeClient(raise_on_send=FloodWait("slow down"))
    action = _FakeAction("TYPING")
    async with TypingIndicator(client, 9001, action=action, interval_sec=0.01, enabled=True):
        # Дать loop'у несколько попыток — каждая send_chat_action попытка бросает FloodWait.
        await asyncio.sleep(0.05)

    # Хотя бы одна FloodWait запись.
    assert floodwait.labels.call_count >= 1
    bucket = floodwait.labels.call_args.kwargs["chat_id_bucket"]
    assert len(bucket) == 2 and bucket.isdigit()


@pytest.mark.asyncio
async def test_typing_indicator_disabled_skips_metrics(monkeypatch, patched_facade):
    """enabled=False → метрика не пишется (no-op context manager)."""
    started, cancelled, _duration, _floodwait = patched_facade
    from src.userbot.typing_indicator import TypingIndicator

    client = _FakeClient()
    action = _FakeAction("TYPING")
    async with TypingIndicator(client, 1, action=action, enabled=False):
        pass

    started.labels.assert_not_called()
    cancelled.labels.assert_not_called()


@pytest.mark.asyncio
async def test_typing_indicator_action_label_resolution(monkeypatch, patched_facade):
    """Action label корректно резолвится из ChatAction.name."""
    started, *_ = patched_facade
    from src.userbot.typing_indicator import TypingIndicator

    client = _FakeClient()
    # RECORD_AUDIO → recording_voice
    async with TypingIndicator(
        client, 1, action=_FakeAction("RECORD_AUDIO"), interval_sec=0.01, enabled=True
    ):
        await asyncio.sleep(0.005)

    started.labels.assert_called_with(action="recording_voice")
