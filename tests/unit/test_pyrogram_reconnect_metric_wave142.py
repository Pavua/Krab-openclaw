# -*- coding: utf-8 -*-
"""Wave 142: тесты для PyrogramReconnectMetricFilter + counter + alert.

Сценарии:
  1. Filter инкрементит counter при "Disconnected" log record от
     pyrogram.connection.connection и пропускает запись дальше (return True).
  2. Filter игнорирует записи от других logger-ов (pyrogram.session.*),
     даже если в message есть слово "Disconnected".
  3. Filter не реагирует на не-Disconnected сообщения от pyrogram.connection.
  4. set_pyrogram_session_label корректно меняет label, get_pyrogram_session_label читает.
  5. inc_pyrogram_disconnect многократно агрегирует counter под одной session.
  6. collect_metrics() выводит pre-registered HELP/TYPE даже при пустом counter
     и multi-session breakdown при наличии данных.
  7. Env-флаг KRAB_PYROGRAM_RECONNECT_METRIC_ENABLED=0 отключает install.
  8. YAML alert rules содержат PyrogramReconnectStorm в правильной группе с
     правильным severity / threshold.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest
import yaml

from src.core import prometheus_metrics
from src.core.logging_filters import (
    PyrogramReconnectMetricFilter,
    install_pyrogram_reconnect_metric_filter,
    is_pyrogram_reconnect_metric_enabled,
)


@pytest.fixture(autouse=True)
def _reset_counter_state(monkeypatch):
    """Чистим in-memory counter и session label перед каждым тестом.

    Wave 142 хранит state в module-level dict / list, поэтому между тестами
    нужна явная очистка чтобы не было cross-talk.
    """
    prometheus_metrics._PYROGRAM_DISCONNECTS_COUNTER.clear()
    prometheus_metrics._PYROGRAM_SESSION_LABEL[0] = "unknown"
    yield
    prometheus_metrics._PYROGRAM_DISCONNECTS_COUNTER.clear()
    prometheus_metrics._PYROGRAM_SESSION_LABEL[0] = "unknown"


def _make_record(
    name: str = "pyrogram.connection.connection",
    msg: str = "Disconnected",
    level: int = logging.INFO,
) -> logging.LogRecord:
    """Создаёт LogRecord с минимальным набором полей."""
    return logging.LogRecord(
        name=name,
        level=level,
        pathname=__file__,
        lineno=0,
        msg=msg,
        args=None,
        exc_info=None,
    )


# --------------------------------------------------------------------------- #
# Filter behaviour                                                             #
# --------------------------------------------------------------------------- #


def test_filter_increments_counter_on_disconnected_from_connection_logger():
    """Wave 142: запись 'Disconnected' от pyrogram.connection.connection → +1 counter."""
    flt = PyrogramReconnectMetricFilter()
    prometheus_metrics.set_pyrogram_session_label("kraab")

    record = _make_record(name="pyrogram.connection.connection", msg="Disconnected")
    result = flt.filter(record)

    # Filter должен пропустить запись (return True) — это observability,
    # не drop-фильтр.
    assert result is True
    assert prometheus_metrics._PYROGRAM_DISCONNECTS_COUNTER.get("kraab") == 1


def test_filter_ignores_other_pyrogram_loggers_even_with_disconnected_message():
    """Wave 142: запись от pyrogram.session.* с тем же текстом не считается.

    Узкая проверка по record.name защищает от двойного счёта если pyrogram
    переименует logger или подмешает 'Disconnected' в другой контекст.
    """
    flt = PyrogramReconnectMetricFilter()
    prometheus_metrics.set_pyrogram_session_label("kraab")

    record = _make_record(name="pyrogram.session.session", msg="Disconnected")
    result = flt.filter(record)

    assert result is True
    assert "kraab" not in prometheus_metrics._PYROGRAM_DISCONNECTS_COUNTER


def test_filter_skips_non_disconnected_messages_from_connection_logger():
    """Wave 142: 'Connecting...' / 'Connected!' и т.д. не инкрементят counter."""
    flt = PyrogramReconnectMetricFilter()
    prometheus_metrics.set_pyrogram_session_label("kraab")

    for msg in ("Connecting...", "Connected! Production DC5 - IPv4", "NetworkTask started"):
        record = _make_record(name="pyrogram.connection.connection", msg=msg)
        assert flt.filter(record) is True

    assert prometheus_metrics._PYROGRAM_DISCONNECTS_COUNTER == {}


# --------------------------------------------------------------------------- #
# Counter / session label API                                                  #
# --------------------------------------------------------------------------- #


def test_set_and_get_session_label_round_trip():
    """Wave 142: set_pyrogram_session_label обновляет, get_ читает."""
    prometheus_metrics.set_pyrogram_session_label("swarm_traders")
    assert prometheus_metrics.get_pyrogram_session_label() == "swarm_traders"

    prometheus_metrics.set_pyrogram_session_label("")  # empty → unknown
    assert prometheus_metrics.get_pyrogram_session_label() == "unknown"

    long_name = "x" * 100
    prometheus_metrics.set_pyrogram_session_label(long_name)
    # Сanitизация до 60 символов чтобы не было cardinality bomb.
    assert len(prometheus_metrics.get_pyrogram_session_label()) == 60


def test_inc_pyrogram_disconnect_aggregates_per_session():
    """Wave 142: множественные inc под одной session накапливаются."""
    prometheus_metrics.set_pyrogram_session_label("kraab")
    for _ in range(13):  # 13 — observed regression count из user report
        prometheus_metrics.inc_pyrogram_disconnect()

    assert prometheus_metrics._PYROGRAM_DISCONNECTS_COUNTER.get("kraab") == 13

    # Explicit session аргумент перебивает registry.
    prometheus_metrics.inc_pyrogram_disconnect(session="swarm_coders")
    assert prometheus_metrics._PYROGRAM_DISCONNECTS_COUNTER.get("kraab") == 13
    assert prometheus_metrics._PYROGRAM_DISCONNECTS_COUNTER.get("swarm_coders") == 1


# --------------------------------------------------------------------------- #
# Text render via collect_metrics                                              #
# --------------------------------------------------------------------------- #


def test_collect_metrics_renders_help_type_even_when_empty():
    """Wave 142: empty counter → HELP/TYPE + session='none' 0 (alert not 'no data')."""
    text = prometheus_metrics.collect_metrics()
    assert "# HELP krab_pyrogram_disconnects_total" in text
    assert "# TYPE krab_pyrogram_disconnects_total counter" in text
    assert 'krab_pyrogram_disconnects_total{session="none"} 0' in text


def test_collect_metrics_renders_session_breakdown():
    """Wave 142: после inc — text render выводит per-session value."""
    prometheus_metrics.inc_pyrogram_disconnect(session="kraab")
    prometheus_metrics.inc_pyrogram_disconnect(session="kraab")
    prometheus_metrics.inc_pyrogram_disconnect(session="swarm_traders")

    text = prometheus_metrics.collect_metrics()

    assert 'krab_pyrogram_disconnects_total{session="kraab"} 2' in text
    assert 'krab_pyrogram_disconnects_total{session="swarm_traders"} 1' in text
    # При наличии данных placeholder session="none" больше не выводится.
    assert 'krab_pyrogram_disconnects_total{session="none"} 0' not in text


# --------------------------------------------------------------------------- #
# Install / env gate                                                           #
# --------------------------------------------------------------------------- #


def test_install_returns_none_when_env_disabled(monkeypatch):
    """Wave 142: KRAB_PYROGRAM_RECONNECT_METRIC_ENABLED=0 → install no-op."""
    monkeypatch.setenv("KRAB_PYROGRAM_RECONNECT_METRIC_ENABLED", "0")
    assert is_pyrogram_reconnect_metric_enabled() is False

    pyrogram_logger = logging.getLogger("pyrogram")
    filters_before = list(pyrogram_logger.filters)
    try:
        result = install_pyrogram_reconnect_metric_filter()
        assert result is None
        # Фильтр НЕ навешан — длина filters не изменилась.
        assert pyrogram_logger.filters == filters_before
    finally:
        # Если что-то навешалось — снимаем чтобы не загрязнить других тестов.
        for f in list(pyrogram_logger.filters):
            if isinstance(f, PyrogramReconnectMetricFilter):
                pyrogram_logger.removeFilter(f)


def test_install_attaches_filter_to_pyrogram_logger(monkeypatch):
    """Wave 142: install с default ON → фильтр навешивается на 'pyrogram'."""
    monkeypatch.delenv("KRAB_PYROGRAM_RECONNECT_METRIC_ENABLED", raising=False)
    assert is_pyrogram_reconnect_metric_enabled() is True

    pyrogram_logger = logging.getLogger("pyrogram")
    try:
        result = install_pyrogram_reconnect_metric_filter()
        assert isinstance(result, PyrogramReconnectMetricFilter)
        assert any(
            isinstance(f, PyrogramReconnectMetricFilter) for f in pyrogram_logger.filters
        )
    finally:
        for f in list(pyrogram_logger.filters):
            if isinstance(f, PyrogramReconnectMetricFilter):
                pyrogram_logger.removeFilter(f)


# --------------------------------------------------------------------------- #
# Alert YAML validation                                                        #
# --------------------------------------------------------------------------- #


_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEPLOY_RULES = _REPO_ROOT / "deploy" / "monitoring" / "rules" / "krab_alerts.yml"


def test_alert_rule_exists_in_deploy_yaml():
    """Wave 142: PyrogramReconnectStorm зарегистрирован в krab_wave142_pyrogram_reconnect."""
    assert _DEPLOY_RULES.exists(), f"alert rules missing: {_DEPLOY_RULES}"
    with _DEPLOY_RULES.open(encoding="utf-8") as fh:
        doc = yaml.safe_load(fh)

    target_group = next(
        (g for g in doc.get("groups", []) if g.get("name") == "krab_wave142_pyrogram_reconnect"),
        None,
    )
    assert target_group is not None, "krab_wave142_pyrogram_reconnect group missing"

    alert = next(
        (r for r in target_group.get("rules", []) if r.get("alert") == "PyrogramReconnectStorm"),
        None,
    )
    assert alert is not None, "PyrogramReconnectStorm alert missing"

    expr = alert["expr"]
    assert "krab_pyrogram_disconnects_total" in expr
    assert "rate(" in expr
    assert "[5m]" in expr
    assert "> 0.1" in expr
    # 5m for window — per task spec.
    assert alert.get("for") == "5m"
    # Severity warning, не critical: storm заметен, но Krab сам по себе
    # имеет reconnect logic — это не fatal.
    assert alert["labels"]["severity"] == "warning"


def test_alert_rule_present_in_all_three_yaml_files():
    """Wave 142: alert также есть в ops/prometheus/ и docs/ копиях rules.

    Три файла исторически держат одни и те же правила (CLAUDE.md backlog) —
    регрессионный тест что новый alert не забыли продублировать.
    """
    paths = [
        _REPO_ROOT / "deploy" / "monitoring" / "rules" / "krab_alerts.yml",
        _REPO_ROOT / "ops" / "prometheus" / "krab_alerts.yml",
        _REPO_ROOT / "docs" / "krab_alerts.yml",
    ]
    for path in paths:
        assert path.exists(), f"alert file missing: {path}"
        with path.open(encoding="utf-8") as fh:
            doc = yaml.safe_load(fh)
        # Уплощаем все rules из всех групп — формат файлов разный.
        all_alerts: list[dict] = []
        for grp in doc.get("groups", []):
            all_alerts.extend(grp.get("rules", []))
        assert any(
            r.get("alert") == "PyrogramReconnectStorm" for r in all_alerts
        ), f"PyrogramReconnectStorm missing in {path}"
