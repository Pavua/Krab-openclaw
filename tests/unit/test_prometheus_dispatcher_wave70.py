# -*- coding: utf-8 -*-
"""Wave 70 tests: Prometheus collector + alert rules для dispatcher/swarm/guard.

Покрывают:
    * register_userbot_for_metrics + weakref behavior;
    * krab_main_dispatcher_tick_ago_seconds присутствует в collect_metrics();
    * krab_swarm_probe_ago_seconds{team=...} per-team gauge;
    * krab_paid_gemini_guard_mode enum mapping (block=1/warn=0/off=-1);
    * placeholder поведение когда userbot ещё не зарегистрирован;
    * alert rule yaml парсится валидным синтаксисом.
"""

from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.core.prometheus_metrics import (
    _get_userbot_for_metrics,
    collect_metrics,
    register_userbot_for_metrics,
)


@pytest.fixture(autouse=True)
def _reset_userbot_ref() -> None:
    """Изолируем тесты: сбрасываем weakref после каждого."""
    register_userbot_for_metrics(None)
    yield
    register_userbot_for_metrics(None)


class _FakeUserbot:
    """Минимальный stub с Wave 63 атрибутами (weakref-friendly)."""

    def __init__(
        self,
        *,
        tick_ago_sec: float | None = 5.0,
        event_ago_sec: float | None = 10.0,
        swarm: dict | None = None,
    ) -> None:
        now = time.time()
        self._dispatcher_tick_count = 42
        self._last_dispatcher_tick_ts = (
            now - tick_ago_sec if tick_ago_sec is not None else None
        )
        self._last_telegram_event_ts = (
            now - event_ago_sec if event_ago_sec is not None else None
        )
        self._last_seen_update_id = 999
        self._last_swarm_pts = swarm if swarm is not None else {}


def test_register_userbot_stores_weakref() -> None:
    """register_userbot_for_metrics хранит weakref, не strong reference."""
    bot = _FakeUserbot()
    register_userbot_for_metrics(bot)
    assert _get_userbot_for_metrics() is bot

    # weakref → после del объект собирается GC.
    del bot
    import gc

    gc.collect()
    assert _get_userbot_for_metrics() is None


def test_main_dispatcher_metric_when_no_userbot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Без userbot метрика = -1 (placeholder, не -no data-)."""
    monkeypatch.setenv("KRAB_BLOCK_PAID_GEMINI_AI_STUDIO", "1")
    text = collect_metrics()
    assert "krab_main_dispatcher_tick_ago_seconds -1" in text
    # Plus guard mode = block=1.
    assert 'krab_paid_gemini_guard_mode{mode="block"} 1' in text


def test_main_dispatcher_metric_with_userbot() -> None:
    """Wave 63-C tick_ts exposed как krab_main_dispatcher_tick_ago_seconds."""
    bot = _FakeUserbot(tick_ago_sec=42.5)
    register_userbot_for_metrics(bot)
    text = collect_metrics()
    # Точное значение зависит от time.time(); проверяем что < 60 и > 30.
    lines = [
        line
        for line in text.splitlines()
        if line.startswith("krab_main_dispatcher_tick_ago_seconds ")
    ]
    assert lines, "metric krab_main_dispatcher_tick_ago_seconds отсутствует"
    value = float(lines[0].split()[-1])
    assert 30.0 < value < 60.0


def test_swarm_probe_per_team_labels() -> None:
    """krab_swarm_probe_ago_seconds{team=...} per-team gauge."""
    now = time.time()
    swarm = {
        "traders": {"pts": 100, "qts": 1, "seq": 2, "date": 0, "ts": now - 3.0},
        "coders": {"pts": 55, "qts": 0, "seq": 1, "date": 0, "ts": now - 12.0},
    }
    bot = _FakeUserbot(swarm=swarm)
    register_userbot_for_metrics(bot)
    text = collect_metrics()
    assert 'krab_swarm_probe_ago_seconds{team="traders"}' in text
    assert 'krab_swarm_probe_ago_seconds{team="coders"}' in text
    # Placeholder "none" не должен присутствовать когда есть реальные команды.
    assert 'krab_swarm_probe_ago_seconds{team="none"}' not in text


def test_swarm_probe_placeholder_when_empty() -> None:
    """Пустой swarm → placeholder 'none' чтобы alert не считался no-data."""
    bot = _FakeUserbot(swarm={})
    register_userbot_for_metrics(bot)
    text = collect_metrics()
    assert 'krab_swarm_probe_ago_seconds{team="none"} 0' in text


@pytest.mark.parametrize(
    "env_value,expected_mode,expected_value",
    [
        ("1", "block", 1),
        ("warn", "warn", 0),
        ("0", "off", -1),
        ("off", "off", -1),
    ],
)
def test_paid_gemini_guard_mode_enum(
    monkeypatch: pytest.MonkeyPatch,
    env_value: str,
    expected_mode: str,
    expected_value: int,
) -> None:
    """krab_paid_gemini_guard_mode корректно мапит env → numeric enum."""
    monkeypatch.setenv("KRAB_BLOCK_PAID_GEMINI_AI_STUDIO", env_value)
    text = collect_metrics()
    needle = f'krab_paid_gemini_guard_mode{{mode="{expected_mode}"}} {expected_value}'
    assert needle in text, f"expected {needle} в выводе collect_metrics()"


def test_alert_rules_yaml_syntax_valid() -> None:
    """Wave 70 alert правила парсятся как валидный yaml + содержат 3 alerts."""
    yaml = pytest.importorskip("yaml")
    repo_root = Path(__file__).resolve().parents[2]
    rules_path = repo_root / "deploy" / "monitoring" / "rules" / "krab_alerts.yml"
    assert rules_path.exists(), f"alert rules не найден: {rules_path}"

    data = yaml.safe_load(rules_path.read_text(encoding="utf-8"))
    assert "groups" in data

    # Находим Wave 70 группу.
    wave70_group = None
    for group in data["groups"]:
        if group.get("name") == "krab_wave70_probes":
            wave70_group = group
            break
    assert wave70_group is not None, "krab_wave70_probes group отсутствует"

    alert_names = {rule["alert"] for rule in wave70_group["rules"]}
    assert alert_names == {
        "MainDispatcherStarved",
        "SwarmTeamProbeStale",
        "PaidGeminiGuardDisabled",
    }

    # MainDispatcherStarved threshold = 600s (10 min).
    starved = next(
        r for r in wave70_group["rules"] if r["alert"] == "MainDispatcherStarved"
    )
    assert "600" in starved["expr"]
    assert starved["for"] == "2m"
    assert starved["labels"]["severity"] == "warning"
