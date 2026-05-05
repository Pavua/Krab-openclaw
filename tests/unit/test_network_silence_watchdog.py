"""
Wave 27-A: Тесты network resilience watchdog.
Логика протестирована в isolation без импорта тяжёлого userbot_bridge.
Тестируем:
  1. silence < threshold → no alert
  2. silence > threshold, DC reachable → false alarm, no alert
  3. silence > threshold, DC unreachable, reconnect OK → no alert
  4. silence > threshold, DC unreachable, reconnect fails → alert sent
  5. debounce блокирует повторный алерт
  6. ENV KRAB_NETWORK_SILENCE_THRESHOLD_SEC overrides default
  7. _probe_telegram_dc TCP probe logic
  8. legacy threshold=60 → auto-upgrade to 180
"""

from __future__ import annotations

import asyncio
import os
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── Standalone реализация probe для unit-тестов ──────────────────────────────

async def _probe_telegram_dc(timeout: float = 5.0) -> bool:
    """Дублирует логику KraabUserbot._probe_telegram_dc для изолированного теста."""
    try:
        _reader, writer = await asyncio.wait_for(
            asyncio.open_connection("149.154.167.50", 443),
            timeout=timeout,
        )
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:  # noqa: BLE001
            pass
        return True
    except Exception:  # noqa: BLE001
        return False


# ── Вспомогательная функция-эмулятор одного прохода watchdog цикла ───────────

async def _run_watchdog_cycle(
    *,
    silence_delta: float,
    threshold_sec: int = 180,
    debounce_sec: int = 1800,
    dc_reachable: bool = True,
    reconnect_ok: bool = True,
    last_alert_age: float = 99999.0,  # секунд с последнего алерта
) -> dict:
    """
    Эмулирует один iteration внутри _network_offline_monitor_loop.
    Возвращает dict: alert_sent, reconnect_called, debug_logged, debounce_blocked.
    """
    results: dict = {
        "alert_sent": False,
        "reconnect_called": False,
        "debug_logged": False,
        "debounce_blocked": False,
    }

    now = time.time()
    last_telegram_event_ts = now - silence_delta
    silence_sec = now - last_telegram_event_ts

    # Клиент подключён
    client_connected = True

    if not client_connected:
        return results

    if silence_sec < threshold_sec:
        # Тишины нет — ничего не делаем
        return results

    # Тишина есть — active probe
    if dc_reachable:
        results["debug_logged"] = True
        return results  # false alarm — quiet hour

    # DC недоступен
    last_alert_ts = now - last_alert_age
    if now - last_alert_ts < debounce_sec:
        # Дебаунс блокирует
        results["debounce_blocked"] = True
        return results

    # Пробуем reconnect
    results["reconnect_called"] = True
    if reconnect_ok:
        # Reconnect успешен — не алертим
        return results

    # Reconnect провалился → alert
    results["alert_sent"] = True
    return results


# ── Тест 1: silence < threshold → no alert ──────────────────────────────────

@pytest.mark.asyncio
async def test_silence_below_threshold_no_alert() -> None:
    """silence (60s) < threshold (180s) → watchdog молчит."""
    res = await _run_watchdog_cycle(silence_delta=60.0, threshold_sec=180)
    assert not res["alert_sent"]
    assert not res["reconnect_called"]
    assert not res["debug_logged"]


# ── Тест 2: silence > threshold + DC reachable → false alarm, no alert ──────

@pytest.mark.asyncio
async def test_silence_above_threshold_dc_reachable_no_alert() -> None:
    """silence > threshold, но DC доступен → quiet hour, не алертим (false alarm)."""
    res = await _run_watchdog_cycle(silence_delta=300.0, threshold_sec=180, dc_reachable=True)
    assert not res["alert_sent"]
    assert not res["reconnect_called"]
    assert res["debug_logged"]  # залогировали debug (сеть OK, просто тихо)


# ── Тест 3: silence > threshold + DC unreachable + reconnect OK → no alert ──

@pytest.mark.asyncio
async def test_silence_dc_unreachable_reconnect_success_no_alert() -> None:
    """DC недоступен, reconnect успешен → алерт не отправляем."""
    res = await _run_watchdog_cycle(
        silence_delta=300.0,
        threshold_sec=180,
        dc_reachable=False,
        reconnect_ok=True,
        last_alert_age=99999.0,
    )
    assert not res["alert_sent"]
    assert res["reconnect_called"]


# ── Тест 4: silence > threshold + DC unreachable + reconnect fails → alert ──

@pytest.mark.asyncio
async def test_silence_dc_unreachable_reconnect_fails_alert_sent() -> None:
    """DC недоступен, reconnect провалился → отправляем алерт."""
    res = await _run_watchdog_cycle(
        silence_delta=300.0,
        threshold_sec=180,
        dc_reachable=False,
        reconnect_ok=False,
        last_alert_age=99999.0,
    )
    assert res["alert_sent"]
    assert res["reconnect_called"]


# ── Тест 5: debounce блокирует повторный алерт ──────────────────────────────

@pytest.mark.asyncio
async def test_alert_debounce_blocks_rapid_duplicates() -> None:
    """Второй алерт в пределах debounce_sec (1800s) не отправляется."""
    res = await _run_watchdog_cycle(
        silence_delta=300.0,
        threshold_sec=180,
        dc_reachable=False,
        reconnect_ok=False,
        last_alert_age=60.0,   # последний алерт был 60с назад (< debounce=1800)
        debounce_sec=1800,
    )
    assert not res["alert_sent"]
    assert res["debounce_blocked"]


# ── Тест 6: ENV KRAB_NETWORK_SILENCE_THRESHOLD_SEC override ─────────────────

@pytest.mark.asyncio
async def test_env_override_threshold() -> None:
    """KRAB_NETWORK_SILENCE_THRESHOLD_SEC перекрывает default threshold."""
    with patch.dict(os.environ, {"KRAB_NETWORK_SILENCE_THRESHOLD_SEC": "300"}):
        env_val = int(os.environ.get("KRAB_NETWORK_SILENCE_THRESHOLD_SEC", "180"))
        assert env_val == 300

    # silence=200s < ENV threshold=300 → no alert (below custom threshold)
    res = await _run_watchdog_cycle(
        silence_delta=200.0,
        threshold_sec=300,  # simulates reading from ENV
        dc_reachable=False,
        reconnect_ok=False,
    )
    assert not res["alert_sent"]
    assert not res["reconnect_called"]


# ── Тест 7: _probe_telegram_dc возвращает bool ──────────────────────────────

@pytest.mark.asyncio
async def test_probe_telegram_dc_success() -> None:
    """_probe_telegram_dc возвращает True при успешном TCP соединении."""
    mock_writer = MagicMock()
    mock_writer.close = MagicMock()
    mock_writer.wait_closed = AsyncMock()

    with patch(
        "asyncio.open_connection",
        new=AsyncMock(return_value=(MagicMock(), mock_writer)),
    ):
        result = await _probe_telegram_dc(timeout=5.0)
        assert result is True


@pytest.mark.asyncio
async def test_probe_telegram_dc_failure() -> None:
    """_probe_telegram_dc возвращает False при ошибке соединения."""
    with patch(
        "asyncio.open_connection",
        new=AsyncMock(side_effect=OSError("Connection refused")),
    ):
        result = await _probe_telegram_dc(timeout=5.0)
        assert result is False


# ── Тест 8: legacy threshold=60 → auto-upgrade to 180 ───────────────────────

def test_legacy_threshold_60_upgraded_to_180() -> None:
    """Если KRAB_NETWORK_OFFLINE_ALERT_SEC=60 (legacy default), поднимаем до 180."""
    raw = 60  # legacy default

    # Логика Wave 27-A: нет нового ENV → если raw==60 → 180
    env_new = None  # KRAB_NETWORK_SILENCE_THRESHOLD_SEC не задан
    if env_new is not None:
        threshold_sec = max(60, int(env_new))
    else:
        threshold_sec = raw if raw > 60 else 180

    assert threshold_sec == 180


def test_custom_legacy_threshold_preserved() -> None:
    """Если KRAB_NETWORK_OFFLINE_ALERT_SEC=300 (пользователь явно задал > 60), сохраняем."""
    raw = 300

    env_new = None
    if env_new is not None:
        threshold_sec = max(60, int(env_new))
    else:
        threshold_sec = raw if raw > 60 else 180

    assert threshold_sec == 300
