# -*- coding: utf-8 -*-
"""Unit tests для Wave 51-A: Prometheus counter exporters
для Wave 44-V/47/48-A/49-F observability.

Тестируем:
1. 5 counters зарегистрированы в default REGISTRY
2. record_model_fallback_engaged инкрементирует с правильными лейблами
3. record_codex_disabled_transition с kind=weekly/transient
4. record_startup_catchup_chat_failed с chat_id label
5. record_state_snapshot_failed с reason label
6. record_provider_timeout с provider/model labels
7. /metrics endpoint exposes их (через generate_latest)
8. helper'ы fail-safe — не бросают даже без prometheus_client
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Вспомогательные утилиты
# ---------------------------------------------------------------------------


def _counter_value(counter, **labels) -> float:
    """Вернуть текущее значение Counter для лейблов."""
    return counter.labels(**labels)._value.get()


# ---------------------------------------------------------------------------
# 1. Все 5 counters зарегистрированы в default REGISTRY
# ---------------------------------------------------------------------------


def test_counter_registered_in_collector_registry():
    """Все 5 counters имеют корректные имена и присутствуют в REGISTRY."""
    from prometheus_client import REGISTRY

    from src.core.prometheus_metrics import (
        krab_codex_disabled_transition_total,
        krab_model_fallback_engaged_total,
        krab_provider_timeout_total,
        krab_startup_catchup_chat_failed_total,
        krab_state_snapshot_failed_total,
    )

    if krab_model_fallback_engaged_total is None:
        # prometheus_client недоступен в среде — skip без падения.
        return

    expected_names = {
        "krab_model_fallback_engaged_total",
        "krab_codex_disabled_transition_total",
        "krab_startup_catchup_chat_failed_total",
        "krab_state_snapshot_failed_total",
        "krab_provider_timeout_total",
    }

    # collect() возвращает Metric objects с публичным `name` attribute.
    # Counter с suffix `_total` exposed как metric с `_total` тоже —
    # внутреннее имя без _total, но `_total` суффикс есть в samples.
    registered: set[str] = set()
    for collector in list(REGISTRY._collector_to_names.keys()):  # type: ignore[attr-defined]
        try:
            for metric in collector.collect():
                # metric.name без _total в новых prom_client; samples содержит полное.
                base = getattr(metric, "name", "") or ""
                registered.add(base)
                if not base.endswith("_total"):
                    registered.add(base + "_total")
        except Exception:  # noqa: BLE001
            continue

    assert expected_names <= registered, f"Missing: {expected_names - registered}"

    # Sanity: модули реально присутствуют
    assert krab_codex_disabled_transition_total is not None
    assert krab_provider_timeout_total is not None
    assert krab_startup_catchup_chat_failed_total is not None
    assert krab_state_snapshot_failed_total is not None


# ---------------------------------------------------------------------------
# 2. model_fallback counter увеличивается при event
# ---------------------------------------------------------------------------


def test_model_fallback_counter_increments_on_event():
    """record_model_fallback_engaged увеличивает counter на 1 для лейблов."""
    from src.core.prometheus_metrics import (
        krab_model_fallback_engaged_total,
        record_model_fallback_engaged,
    )

    if krab_model_fallback_engaged_total is None:
        return

    before = _counter_value(
        krab_model_fallback_engaged_total,
        from_model="codex-cli/gpt-5.5",
        to_model="google/gemini-3-pro-preview",
        reason="quota",
    )
    record_model_fallback_engaged(
        from_model="codex-cli/gpt-5.5",
        to_model="google/gemini-3-pro-preview",
        reason="quota",
    )
    after = _counter_value(
        krab_model_fallback_engaged_total,
        from_model="codex-cli/gpt-5.5",
        to_model="google/gemini-3-pro-preview",
        reason="quota",
    )
    assert after == before + 1.0


# ---------------------------------------------------------------------------
# 3. codex quota counter — kind label
# ---------------------------------------------------------------------------


def test_codex_quota_counter_kind_label_correct():
    """record_codex_disabled_transition correctly разделяет kind=weekly vs transient."""
    from src.core.prometheus_metrics import (
        krab_codex_disabled_transition_total,
        record_codex_disabled_transition,
    )

    if krab_codex_disabled_transition_total is None:
        return

    before_weekly = _counter_value(krab_codex_disabled_transition_total, kind="weekly")
    before_transient = _counter_value(krab_codex_disabled_transition_total, kind="transient")

    record_codex_disabled_transition(kind="weekly")
    record_codex_disabled_transition(kind="weekly")
    record_codex_disabled_transition(kind="transient")

    assert _counter_value(krab_codex_disabled_transition_total, kind="weekly") == before_weekly + 2
    assert (
        _counter_value(krab_codex_disabled_transition_total, kind="transient")
        == before_transient + 1
    )


# ---------------------------------------------------------------------------
# 4. startup_catchup counter
# ---------------------------------------------------------------------------


def test_startup_catchup_failed_counter():
    """record_startup_catchup_chat_failed инкрементирует per-chat counter."""
    from src.core.prometheus_metrics import (
        krab_startup_catchup_chat_failed_total,
        record_startup_catchup_chat_failed,
    )

    if krab_startup_catchup_chat_failed_total is None:
        return

    chat_id = -1001234567890
    before = _counter_value(krab_startup_catchup_chat_failed_total, chat_id=str(chat_id))
    record_startup_catchup_chat_failed(chat_id=chat_id)
    after = _counter_value(krab_startup_catchup_chat_failed_total, chat_id=str(chat_id))
    assert after == before + 1.0


# ---------------------------------------------------------------------------
# 5. state_snapshot_failed counter
# ---------------------------------------------------------------------------


def test_state_snapshot_failed_counter():
    """record_state_snapshot_failed разделяет reasons (copy/restore/list)."""
    from src.core.prometheus_metrics import (
        krab_state_snapshot_failed_total,
        record_state_snapshot_failed,
    )

    if krab_state_snapshot_failed_total is None:
        return

    before_copy = _counter_value(krab_state_snapshot_failed_total, reason="copy_failed")
    before_restore = _counter_value(krab_state_snapshot_failed_total, reason="restore_failed")

    record_state_snapshot_failed(reason="copy_failed")
    record_state_snapshot_failed(reason="restore_failed")
    record_state_snapshot_failed(reason="copy_failed")

    assert _counter_value(krab_state_snapshot_failed_total, reason="copy_failed") == before_copy + 2
    assert (
        _counter_value(krab_state_snapshot_failed_total, reason="restore_failed")
        == before_restore + 1
    )


# ---------------------------------------------------------------------------
# 6. provider_timeout counter
# ---------------------------------------------------------------------------


def test_provider_timeout_counter():
    """record_provider_timeout правильно разносит provider/model."""
    from src.core.prometheus_metrics import (
        krab_provider_timeout_total,
        record_provider_timeout,
    )

    if krab_provider_timeout_total is None:
        return

    before = _counter_value(
        krab_provider_timeout_total,
        provider="google",
        model="google/gemini-3-pro-preview",
    )
    record_provider_timeout(
        provider="google",
        model="google/gemini-3-pro-preview",
    )
    after = _counter_value(
        krab_provider_timeout_total,
        provider="google",
        model="google/gemini-3-pro-preview",
    )
    assert after == before + 1.0


# ---------------------------------------------------------------------------
# 7. /metrics endpoint exposes counters (через generate_latest)
# ---------------------------------------------------------------------------


def test_counters_exposed_in_metrics_endpoint():
    """generate_latest включает все 5 counter имён."""
    try:
        from prometheus_client import REGISTRY, generate_latest
    except ImportError:
        return

    # Импорт модуля гарантирует регистрацию в REGISTRY
    from src.core.prometheus_metrics import (
        krab_model_fallback_engaged_total,
        record_codex_disabled_transition,
        record_model_fallback_engaged,
        record_provider_timeout,
        record_startup_catchup_chat_failed,
        record_state_snapshot_failed,
    )

    if krab_model_fallback_engaged_total is None:
        return

    # Чтобы серии появились в выводе, нужен хотя бы один inc()
    record_model_fallback_engaged(from_model="m1", to_model="m2", reason="provider_timeout")
    record_codex_disabled_transition(kind="weekly")
    record_startup_catchup_chat_failed(chat_id=42)
    record_state_snapshot_failed(reason="copy_failed")
    record_provider_timeout(provider="google", model="g1")

    text = generate_latest(REGISTRY).decode("utf-8")
    for name in (
        "krab_model_fallback_engaged_total",
        "krab_codex_disabled_transition_total",
        "krab_startup_catchup_chat_failed_total",
        "krab_state_snapshot_failed_total",
        "krab_provider_timeout_total",
    ):
        assert name in text, f"Counter '{name}' missing from /metrics output"


# ---------------------------------------------------------------------------
# 8. helper'ы fail-safe
# ---------------------------------------------------------------------------


def test_helpers_fail_safe_when_counters_none(monkeypatch):
    """Все record_* функции no-op если counter = None (нет prometheus_client)."""
    from src.core import prometheus_metrics as pm

    # Симулируем отсутствие prometheus_client
    monkeypatch.setattr(pm, "krab_model_fallback_engaged_total", None)
    monkeypatch.setattr(pm, "krab_codex_disabled_transition_total", None)
    monkeypatch.setattr(pm, "krab_startup_catchup_chat_failed_total", None)
    monkeypatch.setattr(pm, "krab_state_snapshot_failed_total", None)
    monkeypatch.setattr(pm, "krab_provider_timeout_total", None)

    # Ничего не должно бросить
    pm.record_model_fallback_engaged(from_model="a", to_model="b", reason="quota")
    pm.record_codex_disabled_transition(kind="weekly")
    pm.record_startup_catchup_chat_failed(chat_id=1)
    pm.record_state_snapshot_failed(reason="copy_failed")
    pm.record_provider_timeout(provider="google", model="m")


# ---------------------------------------------------------------------------
# 9. helpers truncate долгие лейблы (label cardinality protection)
# ---------------------------------------------------------------------------


def test_helpers_truncate_long_labels():
    """Длинные значения лейблов обрезаются (защита от cardinality explosion)."""
    from src.core.prometheus_metrics import (
        krab_model_fallback_engaged_total,
        record_model_fallback_engaged,
    )

    if krab_model_fallback_engaged_total is None:
        return

    long_model = "google/" + "x" * 500
    # Не должно бросить и не должно создать монструозные labels
    record_model_fallback_engaged(from_model=long_model, to_model=long_model, reason="quota")
    # Sanity: counter обновился с обрезанным значением
    truncated = long_model[:80]
    val = _counter_value(
        krab_model_fallback_engaged_total,
        from_model=truncated,
        to_model=truncated,
        reason="quota",
    )
    assert val >= 1.0
