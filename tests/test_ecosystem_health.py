# -*- coding: utf-8 -*-
"""
Тесты EcosystemHealthService: деградация, сводный статус и R20 timeout-robustness.

Связано с: src/core/ecosystem_health.py
R20: добавлены тесты на per-source timeout guard, явный degraded-флаг,
     latency_summary в _diagnostics, частичный report при деградации одного источника.
"""

from __future__ import annotations

import asyncio
import aiohttp
import pytest

from src.core.ecosystem_health import EcosystemHealthService


# ---------------------------------------------------------------------------
# Заглушки
# ---------------------------------------------------------------------------

class _Router:
    """Минимальный mock ModelRouter."""

    def __init__(self, local_ok: bool, delay: float = 0.0):
        self._local_ok = local_ok
        self._delay = delay

    async def check_local_health(self):
        if self._delay:
            await asyncio.sleep(self._delay)
        return self._local_ok


class _Client:
    """Mock внешнего клиента (OpenClaw / Voice Gateway / Krab Ear)."""

    def __init__(self, ok: bool, delay: float = 0.0):
        self._ok = ok
        self._delay = delay

    async def health_check(self):
        if self._delay:
            await asyncio.sleep(self._delay)
        return self._ok


# ---------------------------------------------------------------------------
# Базовые сценарии (не трогаем существующий контракт)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ecosystem_health_normal_cloud() -> None:
    """Все источники живые, cloud доступен — статус normal/ok."""
    service = EcosystemHealthService(
        router=_Router(local_ok=True),
        openclaw_client=_Client(ok=True),
        voice_gateway_client=_Client(ok=True),
        krab_ear_client=_Client(ok=True),
    )
    payload = await service.collect()
    assert payload["degradation"] == "normal"
    assert payload["chain"]["active_ai_channel"] == "cloud"
    assert payload["status"] == "ok"
    assert payload["checks"]["openclaw"]["ok"] is True


@pytest.mark.asyncio
async def test_ecosystem_health_fallback_when_cloud_offline() -> None:
    """Cloud недоступен, локальный fallback работает — деградированный режим."""
    service = EcosystemHealthService(
        router=_Router(local_ok=True),
        openclaw_client=_Client(ok=False),
        voice_gateway_client=_Client(ok=False),
        krab_ear_client=_Client(ok=False),
    )
    payload = await service.collect()
    assert payload["degradation"] == "degraded_to_local_fallback"
    assert payload["chain"]["active_ai_channel"] == "local_fallback"
    assert payload["status"] == "degraded"
    assert payload["risk_level"] in {"medium", "high"}


@pytest.mark.asyncio
async def test_ecosystem_health_critical_when_all_ai_offline() -> None:
    """Оба AI-канала недоступны — критический уровень."""
    service = EcosystemHealthService(
        router=_Router(local_ok=False),
        openclaw_client=_Client(ok=False),
        voice_gateway_client=_Client(ok=False),
        krab_ear_client=_Client(ok=False),
    )
    payload = await service.collect()
    assert payload["degradation"] == "critical_no_ai_backend"
    assert payload["chain"]["active_ai_channel"] == "none"
    assert payload["status"] == "critical"
    assert payload["risk_level"] == "high"


@pytest.mark.asyncio
async def test_krab_ear_http_error_does_not_use_process_fallback(monkeypatch) -> None:
    """Krab Ear HTTP-ошибка не должна подключать process_probe как fallback."""

    class _BrokenSession:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def get(self, _url):
            raise aiohttp.ClientError("boom")

    monkeypatch.setattr("src.core.ecosystem_health.aiohttp.ClientSession", _BrokenSession)

    service = EcosystemHealthService(
        router=_Router(local_ok=True),
        openclaw_client=_Client(ok=True),
        voice_gateway_client=_Client(ok=True),
        krab_ear_client=None,
        krab_ear_backend_url="http://127.0.0.1:59999",
    )
    payload = await service.collect()

    krab_ear = payload["checks"]["krab_ear"]
    assert krab_ear["ok"] is False
    assert "process_probe" not in krab_ear


# ---------------------------------------------------------------------------
# [R20] Новые тесты: timeout-robustness и частичная деградация
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_single_source_timeout_does_not_crash_endpoint() -> None:
    """
    [R20] Если один источник завис (delay > timeout) — endpoint не падает,
    возвращает частичный report. Остальные источники отвечают нормально.
    """
    # voice_gateway будет зависать на 10 секунд, timeout = 0.2
    service = EcosystemHealthService(
        router=_Router(local_ok=True),
        openclaw_client=_Client(ok=True),
        voice_gateway_client=_Client(ok=True, delay=10.0),  # зависает
        krab_ear_client=_Client(ok=True),
        timeout_sec=0.2,
    )
    payload = await service.collect()

    # Endpoint вернул ответ (не висит) — это главное
    assert "status" in payload
    assert "checks" in payload

    # voice_gateway должен быть помечен как degraded с timeout
    vg = payload["checks"]["voice_gateway"]
    assert vg["ok"] is False
    assert vg["status"] == "timeout"
    assert vg.get("degraded") is True   # [R20] явный флаг

    # Другие источники ответили нормально
    assert payload["checks"]["openclaw"]["ok"] is True
    assert payload["checks"]["local_lm"]["ok"] is True
    assert payload["checks"]["krab_ear"]["ok"] is True


@pytest.mark.asyncio
async def test_all_sources_timeout_returns_critical_partial_report() -> None:
    """
    [R20] Если все источники зависают — endpoint всё равно возвращает
    критический report с degraded=True для каждого источника.
    """
    service = EcosystemHealthService(
        router=_Router(local_ok=True, delay=10.0),
        openclaw_client=_Client(ok=True, delay=10.0),
        voice_gateway_client=_Client(ok=True, delay=10.0),
        krab_ear_client=_Client(ok=True, delay=10.0),
        timeout_sec=0.15,
    )
    payload = await service.collect()

    # Endpoint вернул ответ без исключений
    assert "status" in payload
    # Все источники помечены как деградированные
    for key in ("openclaw", "local_lm", "voice_gateway", "krab_ear"):
        check = payload["checks"][key]
        assert check["ok"] is False, f"Ожидали ok=False для {key}"
        assert check.get("degraded") is True, f"Ожидали degraded=True для {key}"
    # Деградация критическая: оба AI-канала недоступны
    assert payload["degradation"] == "critical_no_ai_backend"


@pytest.mark.asyncio
async def test_diagnostics_latency_summary_present() -> None:
    """
    [R20] Поле _diagnostics.latency_summary присутствует в ответе
    и содержит latency по всем 4 источникам.
    """
    service = EcosystemHealthService(
        router=_Router(local_ok=True),
        openclaw_client=_Client(ok=True),
        voice_gateway_client=_Client(ok=True),
        krab_ear_client=_Client(ok=True),
    )
    payload = await service.collect()

    diag = payload.get("_diagnostics")
    assert diag is not None, "_diagnostics отсутствует в ответе"

    summary = diag.get("latency_summary")
    assert summary is not None, "latency_summary отсутствует в _diagnostics"
    assert set(summary.keys()) == {"openclaw", "local_lm", "voice_gateway", "krab_ear"}

    # Все latency — неотрицательные числа
    for name, ms in summary.items():
        assert isinstance(ms, int) and ms >= 0, f"latency_ms для {name} должно быть int >= 0"

    # Slowest source присутствует
    assert diag.get("slowest_source") in summary
    assert isinstance(diag.get("total_collect_ms"), int)
    assert diag.get("timeout_budget_sec") > 0


@pytest.mark.asyncio
async def test_degraded_flag_false_when_source_ok() -> None:
    """
    [R20] Для исправного источника degraded=False в checks.
    """
    service = EcosystemHealthService(
        router=_Router(local_ok=True),
        openclaw_client=_Client(ok=True),
        voice_gateway_client=_Client(ok=True),
        krab_ear_client=_Client(ok=True),
    )
    payload = await service.collect()
    # local_lm всегда проходит через _safe_run → degraded должен быть False
    local = payload["checks"]["local_lm"]
    assert local.get("degraded") is False


@pytest.mark.asyncio
async def test_timeout_recommendation_appears_in_report() -> None:
    """
    [R20] При timeout источника в recommendations появляется сообщение о timeout.
    """
    service = EcosystemHealthService(
        router=_Router(local_ok=True),
        openclaw_client=_Client(ok=True),
        voice_gateway_client=_Client(ok=True, delay=5.0),  # зависает
        krab_ear_client=_Client(ok=True),
        timeout_sec=0.1,
    )
    payload = await service.collect()

    recs = payload.get("recommendations", [])
    timeout_mentioned = any("timeout" in r.lower() or "⏱" in r for r in recs)
    assert timeout_mentioned, f"Ожидали упоминание timeout в recommendations: {recs}"
