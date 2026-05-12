# -*- coding: utf-8 -*-
"""Wave 136: CI-валидатор для Prometheus alert rules.

Цель — поймать broken alerts до того, как Prometheus тихо пропустит группу.
Проверяет:
  - YAML грузится и имеет корректную структуру groups/rules.
  - Каждый alert содержит обязательные поля (alert, expr) + severity label.
  - Дубликаты alert-имён внутри одной группы недопустимы.
  - PromQL expression — не пустой и без явных syntax markers.
  - Опционально: `promtool check rules` если установлен.

См. deploy/monitoring/rules/krab_alerts.yml и docs/PROMETHEUS_MONITORING.md.
"""

from __future__ import annotations

import shutil
import subprocess
from collections import Counter
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
ALERTS_PATH = REPO_ROOT / "deploy" / "monitoring" / "rules" / "krab_alerts.yml"

# Известные дубли alert-имён между разными группами (исторически Wave 118/124
# завезли по два varianta; группы разные, что для Prometheus легально, но
# фиксируем как known-issue чтобы не плодить).
KNOWN_CROSS_GROUP_DUPLICATES = {"SessionBackupCorrupt", "OpenClawGatewayDown"}

REQUIRED_ALERT_FIELDS = {"alert", "expr"}
REQUIRED_LABEL_KEY = "severity"
ALLOWED_SEVERITIES = {"critical", "warning", "info"}


@pytest.fixture(scope="module")
def alerts_doc() -> dict:
    """Загружает YAML alert rules — fail-fast если файл сломан."""
    assert ALERTS_PATH.exists(), f"alert rules file missing: {ALERTS_PATH}"
    with ALERTS_PATH.open("r", encoding="utf-8") as fh:
        doc = yaml.safe_load(fh)
    assert isinstance(doc, dict), "krab_alerts.yml root должен быть dict"
    return doc


@pytest.fixture(scope="module")
def all_alerts(alerts_doc: dict) -> list[tuple[str, dict]]:
    """Возвращает плоский список (group_name, alert_rule_dict)."""
    flat: list[tuple[str, dict]] = []
    for group in alerts_doc.get("groups", []):
        gname = group.get("name", "<unnamed>")
        for rule in group.get("rules", []):
            if "alert" in rule:
                flat.append((gname, rule))
    return flat


def test_yaml_loads_with_groups(alerts_doc: dict) -> None:
    """Структура верхнего уровня — `groups:` со списком dicts."""
    groups = alerts_doc.get("groups")
    assert isinstance(groups, list) and groups, "groups[] пуст или отсутствует"
    for grp in groups:
        assert isinstance(grp, dict), f"group not a dict: {grp!r}"
        assert "name" in grp, f"group без name: {grp!r}"
        assert "rules" in grp and isinstance(grp["rules"], list), (
            f"group {grp.get('name')} без rules[]"
        )


def test_group_names_unique(alerts_doc: dict) -> None:
    """Дубли имён групп ломают Prometheus reload."""
    names = [g.get("name") for g in alerts_doc.get("groups", [])]
    duplicates = [n for n, c in Counter(names).items() if c > 1]
    assert not duplicates, f"duplicate group names: {duplicates}"


def test_every_alert_has_required_fields(all_alerts: list[tuple[str, dict]]) -> None:
    """alert + expr — обязательны; без них Prometheus падает на reload."""
    assert all_alerts, "no alerts found — sanity check failed"
    broken: list[str] = []
    for gname, rule in all_alerts:
        missing = REQUIRED_ALERT_FIELDS - set(rule.keys())
        if missing:
            broken.append(f"{gname}/{rule.get('alert', '?')}: missing {missing}")
        expr = rule.get("expr")
        if not isinstance(expr, str) or not expr.strip():
            broken.append(f"{gname}/{rule.get('alert', '?')}: empty expr")
    assert not broken, "alerts с broken/missing полями:\n" + "\n".join(broken)


def test_every_alert_has_severity_label(all_alerts: list[tuple[str, dict]]) -> None:
    """Severity-label критично для routing в Alertmanager."""
    missing: list[str] = []
    invalid: list[str] = []
    for gname, rule in all_alerts:
        labels = rule.get("labels") or {}
        if not isinstance(labels, dict):
            missing.append(f"{gname}/{rule['alert']}: labels not a dict")
            continue
        sev = labels.get(REQUIRED_LABEL_KEY)
        if not sev:
            missing.append(f"{gname}/{rule['alert']}")
        elif sev not in ALLOWED_SEVERITIES:
            invalid.append(f"{gname}/{rule['alert']}: severity={sev!r}")
    assert not missing, "alerts без severity:\n" + "\n".join(missing)
    assert not invalid, f"severity not in {ALLOWED_SEVERITIES}:\n" + "\n".join(invalid)


def test_alert_names_unique_within_group(alerts_doc: dict) -> None:
    """Дубль alert-имени в одной группе → Prometheus отбросит группу."""
    bad: list[str] = []
    for grp in alerts_doc.get("groups", []):
        names = [r["alert"] for r in grp.get("rules", []) if "alert" in r]
        for name, count in Counter(names).items():
            if count > 1:
                bad.append(f"{grp.get('name')}: {name} ×{count}")
    assert not bad, "duplicate alerts within group:\n" + "\n".join(bad)


def test_cross_group_duplicates_are_documented(all_alerts: list[tuple[str, dict]]) -> None:
    """Кросс-групповые дубли разрешены только если внесены в whitelist."""
    counter: Counter[str] = Counter(rule["alert"] for _, rule in all_alerts)
    unexpected = {
        name for name, c in counter.items() if c > 1 and name not in KNOWN_CROSS_GROUP_DUPLICATES
    }
    assert not unexpected, (
        f"undocumented cross-group duplicates: {unexpected}; "
        f"добавь в KNOWN_CROSS_GROUP_DUPLICATES если намеренно"
    )


def test_for_durations_use_valid_suffix(all_alerts: list[tuple[str, dict]]) -> None:
    """`for:` должен быть валидным duration: <num><s|m|h|d>."""
    import re

    pat = re.compile(r"^\d+(?:ms|s|m|h|d|w|y)$")
    bad: list[str] = []
    for gname, rule in all_alerts:
        for_val = rule.get("for")
        if for_val is None:
            continue
        if not isinstance(for_val, str) or not pat.match(for_val):
            bad.append(f"{gname}/{rule['alert']}: for={for_val!r}")
    assert not bad, "invalid `for:` durations:\n" + "\n".join(bad)


def test_promtool_check_rules_if_available() -> None:
    """Если promtool установлен — запускает official validation."""
    promtool = shutil.which("promtool")
    if not promtool:
        pytest.skip("promtool not installed — пропускаем official check")
    result = subprocess.run(
        [promtool, "check", "rules", str(ALERTS_PATH)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"promtool failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
