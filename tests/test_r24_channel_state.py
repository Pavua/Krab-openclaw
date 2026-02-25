# -*- coding: utf-8 -*-
"""
Тесты R24: Channel State Machine (Anti-Flap).

Проверяют:
- Переходы состояний HEALTHY → DEGRADED → HEALTHY
- Блокировку через lock_channel() и cooldown
- Автовосстановление после cooldown
- Anti-flap: одна ошибка не меняет состояние

Запуск:
    python -m pytest tests/test_r24_channel_state.py -v
"""

import asyncio
import time
import pytest

from src.core.channel_state import (
    ChannelStateMachine,
    HEALTHY,
    DEGRADED,
    LOCKED,
)


def make_csm(**kwargs) -> ChannelStateMachine:
    """Хелпер: создаёт CSM с минимальным конфигом для тестов."""
    config = {
        "CHANNEL_ERR_THRESHOLD": 3,
        "CHANNEL_OK_THRESHOLD": 2,
        "CHANNEL_LOCK_COOLDOWN_SEC": 60,
    }
    config.update(kwargs)
    return ChannelStateMachine(config)


class TestChannelStateMachineTransitions:
    """Тесты переходов состояний."""

    def test_initial_state_is_healthy(self):
        """Начальное состояние всегда HEALTHY."""
        csm = make_csm()
        assert csm.get_state("local") == HEALTHY
        assert csm.get_state("cloud") == HEALTHY

    def test_healthy_to_degraded_after_err_threshold(self):
        """3 подряд ошибки → DEGRADED."""
        csm = make_csm(CHANNEL_ERR_THRESHOLD=3)
        csm.record_failure("local")
        assert csm.get_state("local") == HEALTHY  # 1 ошибка — ещё HEALTHY
        csm.record_failure("local")
        assert csm.get_state("local") == HEALTHY  # 2 ошибки — ещё HEALTHY
        csm.record_failure("local")
        assert csm.get_state("local") == DEGRADED  # 3 ошибки → DEGRADED

    def test_anti_flap_no_switch_on_single_error(self):
        """Одна ошибка не должна менять состояние (anti-flap)."""
        csm = make_csm(CHANNEL_ERR_THRESHOLD=3)
        csm.record_failure("local")
        assert csm.get_state("local") == HEALTHY

    def test_degraded_to_healthy_after_ok_threshold(self):
        """2 подряд успеха → возврат из DEGRADED в HEALTHY."""
        csm = make_csm(CHANNEL_ERR_THRESHOLD=3, CHANNEL_OK_THRESHOLD=2)
        # Переходим в DEGRADED
        for _ in range(3):
            csm.record_failure("local")
        assert csm.get_state("local") == DEGRADED

        csm.record_success("local")
        assert csm.get_state("local") == DEGRADED  # 1 успех — ещё DEGRADED

        csm.record_success("local")
        assert csm.get_state("local") == HEALTHY  # 2 успеха → HEALTHY

    def test_error_resets_ok_counter(self):
        """Ошибка прерывает накопление consecutive_ok в DEGRADED."""
        csm = make_csm(CHANNEL_ERR_THRESHOLD=3, CHANNEL_OK_THRESHOLD=2)
        for _ in range(3):
            csm.record_failure("cloud")
        assert csm.get_state("cloud") == DEGRADED

        csm.record_success("cloud")   # consecutive_ok = 1
        csm.record_failure("cloud")   # сброс consecutive_ok = 0
        csm.record_success("cloud")   # consecutive_ok = 1 (недостаточно)
        assert csm.get_state("cloud") == DEGRADED  # Всё ещё DEGRADED

    def test_success_resets_error_counter(self):
        """Успех прерывает накопление consecutive_errors."""
        csm = make_csm(CHANNEL_ERR_THRESHOLD=3)
        csm.record_failure("local")   # 1
        csm.record_failure("local")   # 2
        csm.record_success("local")   # сброс
        csm.record_failure("local")   # 1 снова
        assert csm.get_state("local") == HEALTHY  # Не достигли 3


class TestChannelLockAndCooldown:
    """Тесты LOCKED состояния и cooldown."""

    def test_lock_channel_sets_locked_state(self):
        """lock_channel() переводит канал в LOCKED."""
        csm = make_csm()
        csm.lock_channel("cloud", reason="test-manual")
        assert csm.get_state("cloud") == LOCKED

    def test_locked_channel_is_not_usable(self):
        """LOCKED канал недоступен для маршрутизации."""
        csm = make_csm(CHANNEL_LOCK_COOLDOWN_SEC=3600)  # долгий cooldown
        csm.lock_channel("local")
        assert csm.is_usable("local") is False

    def test_locked_expires_after_cooldown(self):
        """После cooldown канал автоматически восстанавливается в HEALTHY."""
        csm = make_csm(CHANNEL_LOCK_COOLDOWN_SEC=0.05)
        csm.lock_channel("local")
        assert csm.is_usable("local") is False

        asyncio.run(asyncio.sleep(0.1))  # ждём истечения cooldown
        assert csm.is_usable("local") is True
        assert csm.get_state("local") == HEALTHY

    def test_locked_to_healthy_on_ok_threshold(self):
        """В LOCKED состоянии ok_threshold успехов → HEALTHY."""
        csm = make_csm(CHANNEL_LOCK_COOLDOWN_SEC=3600, CHANNEL_OK_THRESHOLD=2)
        csm.lock_channel("cloud")
        assert csm.get_state("cloud") == LOCKED

        csm.record_success("cloud")
        assert csm.get_state("cloud") == LOCKED  # 1 успех — ещё LOCKED

        csm.record_success("cloud")
        assert csm.get_state("cloud") == HEALTHY  # 2 успеха → HEALTHY

    def test_reset_clears_all_counters(self):
        """reset() возвращает канал в HEALTHY с нулевыми счётчиками."""
        csm = make_csm()
        for _ in range(3):
            csm.record_failure("local")
        assert csm.get_state("local") == DEGRADED

        csm.reset("local")
        assert csm.get_state("local") == HEALTHY
        assert csm.is_usable("local") is True


class TestChannelDiagnostics:
    """Тесты метода get_diagnostics()."""

    def test_diagnostics_includes_both_channels(self):
        """get_diagnostics() возвращает данные для local и cloud."""
        csm = make_csm()
        diag = csm.get_diagnostics()
        assert "local" in diag
        assert "cloud" in diag

    def test_diagnostics_state_reflects_current(self):
        """Диагностика отражает актуальное состояние канала."""
        csm = make_csm(CHANNEL_ERR_THRESHOLD=2)
        csm.record_failure("local")
        csm.record_failure("local")
        diag = csm.get_diagnostics()
        assert diag["local"]["state"] == DEGRADED

    def test_diagnostics_cooldown_remaining_for_locked(self):
        """Диагностика показывает remaining_cooldown_sec для LOCKED канала."""
        csm = make_csm(CHANNEL_LOCK_COOLDOWN_SEC=3600)
        csm.lock_channel("cloud")
        diag = csm.get_diagnostics()
        assert diag["cloud"]["state"] == LOCKED
        assert diag["cloud"]["remaining_cooldown_sec"] > 0

    def test_diagnostics_has_last_transition(self):
        """Диагностика содержит last_transition для HEALTHY→DEGRADED."""
        csm = make_csm(CHANNEL_ERR_THRESHOLD=1)
        csm.record_failure("cloud")
        diag = csm.get_diagnostics()
        assert "HEALTHY→DEGRADED" in diag["cloud"]["last_transition"]
