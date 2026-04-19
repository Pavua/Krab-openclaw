# -*- coding: utf-8 -*-
"""Тесты anti-flakiness: пропуск restart при высокой нагрузке CPU."""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ─── helpers ──────────────────────────────────────────────────────────────────


def _make_policy(enabled: bool = True):
    """Создаём AutoRestartPolicy с AUTO_RESTART_ENABLED=true."""
    from src.core.auto_restart_policy import AutoRestartPolicy

    env = {"AUTO_RESTART_ENABLED": "true" if enabled else "false"}
    with patch.dict(os.environ, env, clear=False):
        policy = AutoRestartPolicy()
    return policy, env


# ─── _system_load_too_high ────────────────────────────────────────────────────


def test_system_load_too_high_returns_true_when_starved():
    """load_1m > 3 × cpu_count → True."""
    from src.core import auto_restart_policy as m

    # cpu_count=4, multiplier=3.0 → threshold=12.0; load=50 → True
    with patch("os.getloadavg", return_value=(50.0, 40.0, 30.0)), patch(
        "os.cpu_count", return_value=4
    ):
        assert m._system_load_too_high() is True


def test_system_load_too_high_returns_false_when_ok():
    """load_1m < 3 × cpu_count → False."""
    from src.core import auto_restart_policy as m

    # cpu_count=8, multiplier=3.0 → threshold=24.0; load=5 → False
    with patch("os.getloadavg", return_value=(5.0, 4.0, 3.0)), patch(
        "os.cpu_count", return_value=8
    ):
        assert m._system_load_too_high() is False


def test_system_load_too_high_getloadavg_raises_returns_false():
    """Если getloadavg бросает — возвращаем False (allow restart)."""
    from src.core import auto_restart_policy as m

    with patch("os.getloadavg", side_effect=OSError("not supported")):
        assert m._system_load_too_high() is False


def test_load_multiplier_env_override():
    """AUTO_RESTART_LOAD_MULTIPLIER=10 → порог в 10× cpu_count."""
    from src.core import auto_restart_policy as m

    with patch.dict(os.environ, {"AUTO_RESTART_LOAD_MULTIPLIER": "10.0"}, clear=False):
        multiplier = m._load_multiplier()
    assert multiplier == pytest.approx(10.0)


def test_load_multiplier_env_override_applied_in_check():
    """С AUTO_RESTART_LOAD_MULTIPLIER=20 load=50, cpu=4 → threshold=80 → False (не перегружен)."""
    from src.core import auto_restart_policy as m

    with (
        patch.dict(
            os.environ, {"AUTO_RESTART_LOAD_MULTIPLIER": "20.0"}, clear=False
        ),
        patch("os.getloadavg", return_value=(50.0, 40.0, 30.0)),
        patch("os.cpu_count", return_value=4),
    ):
        assert m._system_load_too_high() is False


def test_load_multiplier_bad_env_defaults_to_3():
    """Невалидное значение AUTO_RESTART_LOAD_MULTIPLIER → default 3.0."""
    from src.core import auto_restart_policy as m

    with patch.dict(os.environ, {"AUTO_RESTART_LOAD_MULTIPLIER": "not_a_float"}, clear=False):
        assert m._load_multiplier() == pytest.approx(3.0)


# ─── attempt_restart integration ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_attempt_restart_skips_when_high_load():
    """attempt_restart возвращает (False, 'high_load') при высокой нагрузке."""
    from src.core.auto_restart_policy import AutoRestartPolicy

    policy = AutoRestartPolicy()
    with (
        patch.dict(os.environ, {"AUTO_RESTART_ENABLED": "true"}, clear=False),
        patch("os.getloadavg", return_value=(200.0, 150.0, 100.0)),
        patch("os.cpu_count", return_value=4),
    ):
        ok, reason = await policy.attempt_restart("openclaw_gateway")

    assert ok is False
    assert reason == "high_load"


def _make_to_thread_side_effect():
    """side_effect для asyncio.to_thread — возвращает правильный тип по первому аргументу."""
    import subprocess as _sp

    from src.core.auto_restart_policy import bootstrap_service_if_unloaded as _bsu

    call_count = [0]

    async def _side_effect(fn, *args, **kwargs):
        call_count[0] += 1
        if fn is _bsu:
            # bootstrap probe → уже загружен
            return (False, "already_loaded")
        # subprocess.run → успешный результат
        return MagicMock(returncode=0, stderr="")

    return _side_effect


@pytest.mark.asyncio
async def test_attempt_restart_proceeds_when_load_ok():
    """attempt_restart продолжает при нормальной нагрузке (не возвращает high_load)."""
    from src.core.auto_restart_policy import AutoRestartPolicy

    policy = AutoRestartPolicy()
    side_effect = _make_to_thread_side_effect()

    with (
        patch.dict(os.environ, {"AUTO_RESTART_ENABLED": "true"}, clear=False),
        patch("os.getloadavg", return_value=(1.0, 0.8, 0.7)),
        patch("os.cpu_count", return_value=8),
        patch("asyncio.to_thread", side_effect=side_effect),
    ):
        ok, reason = await policy.attempt_restart("openclaw_gateway")

    # Не должен быть "high_load" — прошёл дальше
    assert reason != "high_load"


@pytest.mark.asyncio
async def test_attempt_restart_skipped_high_load_with_custom_multiplier():
    """С AUTO_RESTART_LOAD_MULTIPLIER=50 при load=199.9, cpu=4 → threshold=200 → не starved."""
    from src.core.auto_restart_policy import AutoRestartPolicy

    policy = AutoRestartPolicy()
    side_effect = _make_to_thread_side_effect()

    # 50 × 4 = 200; load_1m=199.9 < 200 → не starved → рестарт пройдёт дальше
    with (
        patch.dict(
            os.environ,
            {"AUTO_RESTART_ENABLED": "true", "AUTO_RESTART_LOAD_MULTIPLIER": "50.0"},
            clear=False,
        ),
        patch("os.getloadavg", return_value=(199.9, 100.0, 80.0)),
        patch("os.cpu_count", return_value=4),
        patch("asyncio.to_thread", side_effect=side_effect),
    ):
        ok, reason = await policy.attempt_restart("openclaw_gateway")

    assert reason != "high_load"
