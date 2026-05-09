"""Wave 53-G: tests for quota recovery probe hardening.

Покрывает:
- jitter bounds
- exponential backoff doubling / cap
- backoff reset on success
- per-account failure independence
- probe skipped when in cooldown
- timeout handling (mocked subprocess)
- telemetry events (structured log)
- state persistence across restarts
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.integrations.codex_quota_probe_state import (
    BASE_INTERVAL_SEC,
    JITTER_FACTOR,
    MAX_INTERVAL_SEC,
    compute_next_interval,
    get_probe_stats,
    is_account_in_cooldown,
    record_probe_attempt,
    record_probe_failure,
    record_probe_success,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_probe_state(tmp_path, monkeypatch):
    """Перенаправляет PROBE_STATE_FILE во временный каталог."""
    probe_file = tmp_path / "codex_quota_probe_state.json"
    import src.integrations.codex_quota_probe_state as mod

    monkeypatch.setattr(mod, "PROBE_STATE_FILE", probe_file)
    yield probe_file


# ---------------------------------------------------------------------------
# test_jitter_within_bounds
# ---------------------------------------------------------------------------


class TestJitterWithinBounds:
    """Jitter должен находиться в пределах ±20% от base interval."""

    def test_jitter_within_bounds_zero_failures(self):
        """При failures=0 результат должен быть в [base*0.8, base*1.2]."""
        base = BASE_INTERVAL_SEC
        results = [compute_next_interval(failures=0, base_sec=base) for _ in range(200)]
        lower = base * (1 - JITTER_FACTOR)
        upper = base * (1 + JITTER_FACTOR)
        assert all(lower <= r <= upper for r in results), (
            f"Значения вне диапазона [{lower}, {upper}]: "
            f"min={min(results):.1f}, max={max(results):.1f}"
        )

    def test_jitter_has_randomness(self):
        """Значения не должны быть идентичными (есть случайность)."""
        results = {compute_next_interval(failures=0) for _ in range(50)}
        # При 50 попытках должно быть как минимум 2 разных значения
        assert len(results) > 1, "Jitter не случаен — все значения одинаковы"

    def test_jitter_at_max_backoff_within_bounds(self):
        """При failures≥10 (cap) jitter должен быть в пределах MAX_INTERVAL ±20%."""
        results = [compute_next_interval(failures=20) for _ in range(100)]
        lower = MAX_INTERVAL_SEC * (1 - JITTER_FACTOR)
        upper = MAX_INTERVAL_SEC * (1 + JITTER_FACTOR)
        assert all(lower <= r <= upper for r in results)

    def test_result_never_below_60_seconds(self):
        """Интервал не должен опускаться ниже 60 секунд."""
        results = [compute_next_interval(failures=0, base_sec=60) for _ in range(100)]
        assert all(r >= 60.0 for r in results)


# ---------------------------------------------------------------------------
# test_backoff_doubles_on_failure
# ---------------------------------------------------------------------------


class TestBackoffDoublesOnFailure:
    """Backoff должен удваиваться при каждой неудаче."""

    def test_backoff_doubles_first_two_failures(self):
        """failures=1 → 2h base, failures=2 → 4h base (до jitter)."""
        # Убираем jitter: используем seed через monkeypatch.
        # Проверяем сырое значение до jitter через large sample mean.
        n = 1000
        mean_f1 = sum(compute_next_interval(failures=1) for _ in range(n)) / n
        mean_f2 = sum(compute_next_interval(failures=2) for _ in range(n)) / n
        # mean_f1 ≈ 2h, mean_f2 ≈ 4h — соотношение ~2x
        ratio = mean_f2 / mean_f1
        assert 1.8 <= ratio <= 2.2, f"Ожидали удвоение, получили ratio={ratio:.2f}"

    def test_backoff_capped_at_24h(self):
        """Backoff не должен превышать MAX_INTERVAL_SEC * (1 + JITTER_FACTOR)."""
        upper = MAX_INTERVAL_SEC * (1 + JITTER_FACTOR)
        results = [compute_next_interval(failures=30) for _ in range(50)]
        assert all(r <= upper for r in results), f"Превышен cap: max={max(results):.1f}"

    def test_backoff_progressive(self):
        """Каждый следующий уровень failures → больший interval (в среднем)."""
        n = 500
        means = [sum(compute_next_interval(failures=f) for _ in range(n)) / n for f in range(5)]
        for i in range(1, len(means)):
            assert means[i] > means[i - 1], (
                f"failures={i} mean ({means[i]:.1f}) не больше failures={i - 1} ({means[i - 1]:.1f})"
            )


# ---------------------------------------------------------------------------
# test_backoff_resets_on_success
# ---------------------------------------------------------------------------


class TestBackoffResetsOnSuccess:
    """record_probe_success сбрасывает failures и устанавливает базовый interval."""

    def test_failures_reset_to_zero(self, tmp_probe_state):
        """После success failures должны быть 0."""
        # Имитируем несколько неудач
        record_probe_failure("primary")
        record_probe_failure("primary")
        record_probe_failure("primary")

        record_probe_success("primary")

        state = get_probe_stats()
        assert state["accounts"]["primary"]["failures"] == 0

    def test_next_probe_ts_set_after_success(self, tmp_probe_state):
        """После success next_probe_ts должен быть установлен (будущее время)."""
        record_probe_success("primary")
        state = get_probe_stats()
        next_ts = state["accounts"]["primary"].get("next_probe_ts")
        assert next_ts is not None
        next_dt = datetime.fromisoformat(next_ts)
        assert next_dt > datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# test_per_account_failure_independence
# ---------------------------------------------------------------------------


class TestPerAccountFailureIndependence:
    """Неудача одного аккаунта не должна влиять на другой."""

    def test_primary_failure_does_not_affect_secondary(self, tmp_probe_state):
        """primary failures=3, secondary failures должен оставаться 0."""
        record_probe_failure("primary")
        record_probe_failure("primary")
        record_probe_failure("primary")

        state = get_probe_stats()
        accts = state["accounts"]
        assert accts["primary"]["failures"] == 3
        # secondary ещё не трогали — не должно быть в state или failures=0
        secondary_failures = accts.get("secondary", {}).get("failures", 0)
        assert secondary_failures == 0

    def test_secondary_success_does_not_reset_primary_failures(self, tmp_probe_state):
        """Успех secondary не меняет failures primary."""
        record_probe_failure("primary")
        record_probe_failure("primary")
        record_probe_success("secondary")

        state = get_probe_stats()
        assert state["accounts"]["primary"]["failures"] == 2
        assert state["accounts"]["secondary"]["failures"] == 0

    def test_cooldown_per_account_independent(self, tmp_probe_state):
        """Cooldown primary не влияет на secondary."""
        # Создаём состояние: primary в cooldown (next_probe_ts в будущем)
        import src.integrations.codex_quota_probe_state as mod

        raw = {
            "accounts": {
                "primary": {
                    "failures": 2,
                    "next_probe_ts": (
                        datetime.now(timezone.utc) + timedelta(hours=3)
                    ).isoformat(),
                    "last_probe_ts": datetime.now(timezone.utc).isoformat(),
                },
                "secondary": {
                    "failures": 0,
                    "next_probe_ts": (
                        datetime.now(timezone.utc) - timedelta(minutes=1)
                    ).isoformat(),
                    "last_probe_ts": datetime.now(timezone.utc).isoformat(),
                },
            },
            "global_stats": {"total_probes": 4, "successes": 1, "failures": 3},
        }
        mod.PROBE_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        mod.PROBE_STATE_FILE.write_text(json.dumps(raw), encoding="utf-8")

        assert is_account_in_cooldown("primary") is True
        assert is_account_in_cooldown("secondary") is False


# ---------------------------------------------------------------------------
# test_probe_skipped_when_account_in_cooldown
# ---------------------------------------------------------------------------


class TestProbeSkippedWhenInCooldown:
    """Проба должна быть пропущена когда аккаунт в cooldown."""

    def test_skipped_when_next_probe_in_future(self, tmp_probe_state):
        """is_account_in_cooldown=True если next_probe_ts > now."""
        import src.integrations.codex_quota_probe_state as mod

        future_ts = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
        raw = {
            "accounts": {"primary": {"failures": 1, "next_probe_ts": future_ts}},
            "global_stats": {"total_probes": 1, "successes": 0, "failures": 1},
        }
        mod.PROBE_STATE_FILE.write_text(json.dumps(raw), encoding="utf-8")

        assert is_account_in_cooldown("primary") is True

    def test_not_skipped_when_next_probe_in_past(self, tmp_probe_state):
        """is_account_in_cooldown=False если next_probe_ts < now."""
        import src.integrations.codex_quota_probe_state as mod

        past_ts = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        raw = {
            "accounts": {"primary": {"failures": 0, "next_probe_ts": past_ts}},
            "global_stats": {"total_probes": 1, "successes": 1, "failures": 0},
        }
        mod.PROBE_STATE_FILE.write_text(json.dumps(raw), encoding="utf-8")

        assert is_account_in_cooldown("primary") is False

    def test_not_in_cooldown_when_no_state(self, tmp_probe_state):
        """Новый аккаунт без state не в cooldown."""
        assert is_account_in_cooldown("brand_new_account") is False


# ---------------------------------------------------------------------------
# test_probe_timeout_kills_hung_subprocess
# ---------------------------------------------------------------------------


class TestProbeTimeoutKillsHungSubprocess:
    """Probe с таймаутом 30s должна прерываться и засчитываться как failure."""

    @pytest.mark.asyncio
    async def test_timeout_results_in_failure(self, tmp_probe_state):
        """asyncio.wait_for c timeout=30 должен поднять TimeoutError → failure."""
        from src.userbot.proactive_watch import ProactiveWatchMixin

        mixin = ProactiveWatchMixin()
        mixin._send_proactive_watch_alert = AsyncMock()

        failures_recorded = []

        async def hanging_probe(_account):
            # Имитируем бесконечно висящую операцию
            await asyncio.sleep(9999)

        with (
            patch(
                "src.userbot.proactive_watch.ProactiveWatchMixin._probe_single_codex_account",
                side_effect=hanging_probe,
            ),
            patch("src.integrations.codex_quota_probe_state.PROBE_STATE_FILE", tmp_probe_state),
        ):
            # Уменьшаем timeout для теста
            original_method = ProactiveWatchMixin._codex_quota_probe_all_accounts.__wrapped__ if hasattr(
                ProactiveWatchMixin._codex_quota_probe_all_accounts, "__wrapped__"
            ) else None

            accounts = [{"name": "primary", "logged_in": True, "available": False}]

            with (
                patch(
                    "src.integrations.codex_account_rotator.list_accounts",
                    return_value=accounts,
                ),
                patch(
                    "src.integrations.codex_quota_state.is_codex_disabled",
                    return_value=True,
                ),
                patch(
                    "src.integrations.codex_quota_probe_state.is_account_in_cooldown",
                    return_value=False,
                ),
                patch(
                    "src.integrations.codex_quota_probe_state.record_probe_attempt"
                ) as mock_attempt,
                patch(
                    "src.integrations.codex_quota_probe_state.record_probe_failure"
                ) as mock_failure,
            ):
                # Мокируем wait_for чтобы симулировать timeout
                async def fake_wait_for(coro, timeout):
                    coro.close()
                    raise asyncio.TimeoutError()

                with patch("asyncio.wait_for", side_effect=fake_wait_for):
                    await mixin._codex_quota_probe_all_accounts(
                        is_account_in_cooldown=lambda _: False,
                        record_probe_attempt=mock_attempt,
                        record_probe_success=MagicMock(),
                        record_probe_failure=mock_failure,
                    )

                mock_attempt.assert_called_once_with("primary")
                mock_failure.assert_called_once_with("primary")


# ---------------------------------------------------------------------------
# test_telemetry_events_emitted
# ---------------------------------------------------------------------------


class TestTelemetryEventsEmitted:
    """Structured log events должны эмититься при probe attempt/success/failure."""

    def test_attempt_event_logged(self, tmp_probe_state, caplog):
        """record_probe_attempt должен логировать quota_probe_attempt."""
        import logging

        import structlog

        events = []

        def capture_event(logger, method, event_dict):
            events.append(event_dict)
            return event_dict

        with patch(
            "src.integrations.codex_quota_probe_state.logger"
        ) as mock_logger:
            record_probe_attempt("primary")
            mock_logger.info.assert_called_once()
            call_kwargs = mock_logger.info.call_args
            assert "quota_probe_attempt" in call_kwargs[0]

    def test_success_event_logged(self, tmp_probe_state):
        """record_probe_success должен логировать quota_probe_success."""
        with patch("src.integrations.codex_quota_probe_state.logger") as mock_logger:
            record_probe_success("primary")
            mock_logger.info.assert_called_once()
            assert "quota_probe_success" in mock_logger.info.call_args[0]

    def test_failure_event_logged(self, tmp_probe_state):
        """record_probe_failure должен логировать quota_probe_failure_backoff."""
        with patch("src.integrations.codex_quota_probe_state.logger") as mock_logger:
            record_probe_failure("primary")
            mock_logger.warning.assert_called_once()
            assert "quota_probe_failure_backoff" in mock_logger.warning.call_args[0]

    def test_failure_includes_consecutive_count(self, tmp_probe_state):
        """Telemetry failure должна включать consecutive_failures count."""
        with patch("src.integrations.codex_quota_probe_state.logger") as mock_logger:
            record_probe_failure("primary")
            record_probe_failure("primary")
            # Второй вызов
            call_kwargs = mock_logger.warning.call_args[1]
            assert call_kwargs.get("consecutive_failures") == 2


# ---------------------------------------------------------------------------
# test_state_persists_across_restarts
# ---------------------------------------------------------------------------


class TestStatePersistsAcrossRestarts:
    """State должен корректно загружаться после перезапуска."""

    def test_failures_persist_in_file(self, tmp_probe_state):
        """failures записываются в файл и читаются при следующей загрузке."""
        record_probe_failure("primary")
        record_probe_failure("primary")

        # Симулируем перезапуск — читаем state напрямую из файла
        raw = json.loads(tmp_probe_state.read_text())
        assert raw["accounts"]["primary"]["failures"] == 2

    def test_global_stats_accumulate(self, tmp_probe_state):
        """global_stats.total_probes накапливаются между вызовами."""
        record_probe_attempt("primary")
        record_probe_attempt("primary")
        record_probe_attempt("secondary")

        state = get_probe_stats()
        assert state["global_stats"]["total_probes"] == 3

    def test_corrupted_state_file_returns_empty(self, tmp_probe_state):
        """Повреждённый файл не роняет probe — возвращает чистый state."""
        tmp_probe_state.write_text("{broken json", encoding="utf-8")
        # Должен вернуть дефолтный state без исключений
        assert not is_account_in_cooldown("primary")
        state = get_probe_stats()
        assert state["global_stats"]["total_probes"] == 0

    def test_next_probe_ts_persisted_after_failure(self, tmp_probe_state):
        """next_probe_ts сохраняется в файл после failure."""
        record_probe_failure("primary")
        raw = json.loads(tmp_probe_state.read_text())
        assert raw["accounts"]["primary"].get("next_probe_ts") is not None

    def test_success_resets_failures_in_file(self, tmp_probe_state):
        """record_probe_success обнуляет failures в файле."""
        record_probe_failure("primary")
        record_probe_failure("primary")
        record_probe_success("primary")

        raw = json.loads(tmp_probe_state.read_text())
        assert raw["accounts"]["primary"]["failures"] == 0
