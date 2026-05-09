# -*- coding: utf-8 -*-
"""Wave 50-E: проверки YAML-структуры алертов для Wave 47-49 features.

Реальные пороги/severity описаны декларативно в
``deploy/monitoring/rules/krab_alerts.yml``. Тесты валидируют:

* alert присутствует в группе ``krab_wave_47_49``;
* severity соответствует ожидаемому (info / warning / critical);
* expression содержит ожидаемое окно/порог;
* числовые threshold-параметры точные (граничный + sub-граничный кейсы
  моделируем как PromQL-substring matches: ``> 5`` vs ``> 4``).

Реальные ``increase()/rate()`` Prometheus в unit-тестах не считаем —
для этого нужен Prometheus runtime; здесь же — config-validity тесты.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

# --------------------------------------------------------------------------- #
# Fixtures                                                                     #
# --------------------------------------------------------------------------- #

RULES_PATH = (
    Path(__file__).resolve().parents[2] / "deploy" / "monitoring" / "rules" / "krab_alerts.yml"
)


@pytest.fixture(scope="module")
def alert_rules() -> dict:
    """Загружает YAML с правилами и возвращает dict."""
    assert RULES_PATH.exists(), f"Rules file missing: {RULES_PATH}"
    with RULES_PATH.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


@pytest.fixture(scope="module")
def wave_47_49_group(alert_rules: dict) -> dict:
    """Возвращает группу ``krab_wave_47_49`` (Wave 50-E target)."""
    groups = alert_rules.get("groups", [])
    target = next((g for g in groups if g.get("name") == "krab_wave_47_49"), None)
    assert target is not None, "krab_wave_47_49 group missing"
    return target


def _find_alert(group: dict, name: str) -> dict:
    for rule in group.get("rules", []):
        if rule.get("alert") == name:
            return rule
    raise AssertionError(f"alert {name} not found in group {group.get('name')}")


# --------------------------------------------------------------------------- #
# Tests                                                                        #
# --------------------------------------------------------------------------- #


def test_fallback_chain_exhausted_alert_threshold(wave_47_49_group: dict) -> None:
    """5 событий = trigger (>5 ложь, ровно 5 не triggers, 6 triggers)."""
    alert = _find_alert(wave_47_49_group, "FallbackChainExhaustedAlert")
    expr = alert["expr"]
    # Threshold ровно 5 за окно 1h
    assert "[1h]" in expr
    assert "> 5" in expr
    # 4 не должно сработать — sanity-check на substring (нет "> 4" в expr)
    assert "> 4" not in expr
    # Severity warning, не critical (chain advancement сам по себе не
    # катастрофа — fallback работает корректно).
    assert alert["labels"]["severity"] == "warning"


def test_codex_quota_alert_severity(wave_47_49_group: dict) -> None:
    """codex_disabled_transition — informational, не critical."""
    alert = _find_alert(wave_47_49_group, "CodexQuotaExhaustedAlert")
    assert alert["labels"]["severity"] == "info"
    # 24h window с порогом > 0 (single occurrence triggers)
    assert "[24h]" in alert["expr"]
    assert "> 0" in alert["expr"]


def test_multi_chat_catchup_failed_alert_window(wave_47_49_group: dict) -> None:
    """Окно — 1h, порог — > 3 (4-й fail triggers)."""
    alert = _find_alert(wave_47_49_group, "MultiChatCatchupFailedAlert")
    expr = alert["expr"]
    assert "[1h]" in expr
    assert "> 3" in expr
    # Severity warning — не critical: одиночные network glitches возможны.
    assert alert["labels"]["severity"] == "warning"


def test_state_snapshot_failed_alert_severity(wave_47_49_group: dict) -> None:
    """Снэпшот state — critical (потеря context при рестарте)."""
    alert = _find_alert(wave_47_49_group, "StateSnapshotFailedAlert")
    assert alert["labels"]["severity"] == "critical"
    # Single occurrence → > 0 за 24h
    assert "> 0" in alert["expr"]
    assert "[24h]" in alert["expr"]


def test_provider_timeout_rate_alert(wave_47_49_group: dict) -> None:
    """Порог 10 событий/мин = (10 / 60) per-second rate за 5m окно."""
    alert = _find_alert(wave_47_49_group, "ProviderTimeoutHighAlert")
    expr = alert["expr"]
    # Используем rate() а не increase() — PromQL для частоты.
    assert "rate(" in expr
    assert "[5m]" in expr
    # 10/min = 10/60 = 0.166...; expr должен содержать (10 / 60)
    assert "(10 / 60)" in expr
    # for: 5m чтобы избежать кратковременных всплесков
    assert alert.get("for") == "5m"
    assert alert["labels"]["severity"] == "warning"


def test_route_switch_alert_anomaly_detection(wave_47_49_group: dict) -> None:
    """Routing flapping: > 20 switches/h."""
    alert = _find_alert(wave_47_49_group, "RouteSwitchHighFrequencyAlert")
    expr = alert["expr"]
    assert "[1h]" in expr
    assert "> 20" in expr
    # Wave-метка для traceability
    assert alert["labels"].get("wave") == "48-B"


def test_all_wave_47_49_alerts_have_annotations(wave_47_49_group: dict) -> None:
    """Каждый alert обязан иметь summary + description (Grafana / Telegram)."""
    expected = {
        "FallbackChainExhaustedAlert",
        "CodexQuotaExhaustedAlert",
        "MultiChatCatchupFailedAlert",
        "StateSnapshotFailedAlert",
        "ProviderTimeoutHighAlert",
        "RouteSwitchHighFrequencyAlert",
    }
    found = {r["alert"] for r in wave_47_49_group["rules"]}
    assert expected.issubset(found), f"missing alerts: {expected - found}"
    for rule in wave_47_49_group["rules"]:
        if rule["alert"] in expected:
            ann = rule.get("annotations", {})
            assert ann.get("summary"), f"{rule['alert']} missing summary"
            assert ann.get("description"), f"{rule['alert']} missing description"


def test_yaml_structure_valid(alert_rules: dict) -> None:
    """Top-level YAML валиден и содержит все 4 группы."""
    groups = {g["name"] for g in alert_rules["groups"]}
    expected = {"krab_critical", "krab_capacity", "krab_engagement", "krab_wave_47_49"}
    assert expected.issubset(groups), f"missing groups: {expected - groups}"
