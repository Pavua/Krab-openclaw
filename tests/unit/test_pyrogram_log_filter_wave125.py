"""Wave 125: фильтр шумных pyrofork deprecation warnings."""

from __future__ import annotations

import logging

import pytest

from src.core.logging_filters import (
    PyrogramDeprecatedFilter,
    install_pyrogram_depr_filter,
    is_pyrogram_depr_filter_enabled,
)


def _make_record(msg: str) -> logging.LogRecord:
    return logging.LogRecord(
        name="pyrogram.types.messages_and_media.message",
        level=logging.WARNING,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=(),
        exc_info=None,
    )


def test_drops_forward_from_deprecation() -> None:
    flt = PyrogramDeprecatedFilter()
    record = _make_record(
        "message.forward_from is deprecated, use forward_origin instead"
    )
    assert flt.filter(record) is False


def test_drops_forward_sender_name_deprecation() -> None:
    flt = PyrogramDeprecatedFilter()
    record = _make_record(
        "message.forward_sender_name is deprecated, use forward_origin instead"
    )
    assert flt.filter(record) is False


def test_drops_forward_from_chat_property_deprecation() -> None:
    flt = PyrogramDeprecatedFilter()
    record = _make_record(
        "message.forward_from_chat property is deprecated since v2"
    )
    assert flt.filter(record) is False


def test_keeps_unrelated_pyrogram_warning() -> None:
    flt = PyrogramDeprecatedFilter()
    record = _make_record("Connection lost, retrying in 5s")
    assert flt.filter(record) is True


def test_keeps_other_deprecation_warning() -> None:
    flt = PyrogramDeprecatedFilter()
    record = _make_record("message.text is deprecated, use caption instead")
    # Не наш паттерн (text вместо forward_*) — должно пройти.
    assert flt.filter(record) is True


def test_env_gate_default_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KRAB_PYROGRAM_DEPR_FILTER_ENABLED", raising=False)
    assert is_pyrogram_depr_filter_enabled() is True


@pytest.mark.parametrize("value", ["0", "false", "no", "off", "FALSE", ""])
def test_env_gate_off(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("KRAB_PYROGRAM_DEPR_FILTER_ENABLED", value)
    assert is_pyrogram_depr_filter_enabled() is False


def test_install_returns_none_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KRAB_PYROGRAM_DEPR_FILTER_ENABLED", "0")
    pyro_logger = logging.getLogger("pyrogram")
    before = list(pyro_logger.filters)
    try:
        result = install_pyrogram_depr_filter()
        assert result is None
        assert list(pyro_logger.filters) == before
    finally:
        # очистка на случай если что-то добавилось
        for f in list(pyro_logger.filters):
            if f not in before:
                pyro_logger.removeFilter(f)


def test_install_attaches_filter_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KRAB_PYROGRAM_DEPR_FILTER_ENABLED", "1")
    pyro_logger = logging.getLogger("pyrogram")
    before = list(pyro_logger.filters)
    flt = install_pyrogram_depr_filter()
    try:
        assert isinstance(flt, PyrogramDeprecatedFilter)
        assert flt in pyro_logger.filters
    finally:
        if flt is not None:
            pyro_logger.removeFilter(flt)
        assert list(pyro_logger.filters) == before
