# -*- coding: utf-8 -*-
"""
Тесты для src/core/ecosystem_health.py.

Покрывает: EcosystemHealthService.collect(), _check_client_health(),
_check_local_health(), _check_krab_ear_health(), _collect_resource_metrics(),
деградационные сценарии, edge-cases с timeout/error.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.ecosystem_health import EcosystemHealthService

# ---------------------------------------------------------------------------
# Вспомогательные объекты
# ---------------------------------------------------------------------------


def _make_router(health_status: str = "healthy") -> MagicMock:
    """Минимальный mock роутера с health_check."""
    router = MagicMock()
    router.health_check = AsyncMock(return_value={"status": health_status})
    router.task_queue = None
    router.cost_analytics = None
    router.cost_engine = None
    return router


def _make_client(ok: bool = True) -> MagicMock:
    """Mock внешнего клиента (openclaw / voice gateway)."""
    client = MagicMock()
    client.health_check = AsyncMock(return_value=ok)
    client.get_token_info = MagicMock(return_value={"is_configured": True, "masked_key": "sk-***"})
    return client


# ---------------------------------------------------------------------------
# Фикстуры
# ---------------------------------------------------------------------------


@pytest.fixture
def healthy_service() -> EcosystemHealthService:
    """Сервис с доступными компонентами и local_health_override."""
    router = _make_router("healthy")
    return EcosystemHealthService(
        router=router,
        openclaw_client=_make_client(ok=True),
        voice_gateway_client=_make_client(ok=True),
        local_health_override={"ok": True, "status": "ok", "latency_ms": 5},
        krab_ear_backend_url="http://127.0.0.1:5005",
        timeout_sec=2.5,
    )


@pytest.fixture
def no_cloud_service() -> EcosystemHealthService:
    """Сервис без облака, только local fallback."""
    router = _make_router("healthy")
    return EcosystemHealthService(
        router=router,
        openclaw_client=_make_client(ok=False),
        voice_gateway_client=_make_client(ok=False),
        local_health_override={"ok": True, "status": "ok", "latency_ms": 10},
        krab_ear_backend_url="http://127.0.0.1:5005",
    )


@pytest.fixture
def critical_service() -> EcosystemHealthService:
    """Сервис без AI вообще (ни cloud, ни local)."""
    router = _make_router("unhealthy")
    router.health_check = AsyncMock(return_value={"status": "unhealthy"})
    return EcosystemHealthService(
        router=router,
        openclaw_client=_make_client(ok=False),
        local_health_override={"ok": False, "status": "unavailable", "latency_ms": 0},
        krab_ear_backend_url="http://127.0.0.1:5005",
    )


# ---------------------------------------------------------------------------
# Базовые проверки структуры ответа
# ---------------------------------------------------------------------------


class TestCollectStructure:
    """Структура ответа collect() — все обязательные ключи присутствуют."""

    @pytest.mark.asyncio
    async def test_required_top_level_keys(self, healthy_service):
        """collect() возвращает все обязательные top-level ключи."""
        with patch("httpx.AsyncClient") as mock_httpx:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_httpx.return_value.__aenter__ = AsyncMock(return_value=mock_httpx.return_value)
            mock_httpx.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_httpx.return_value.get = AsyncMock(return_value=mock_response)

            result = await healthy_service.collect()

        for key in (
            "generated_at",
            "status",
            "risk_level",
            "degradation",
            "checks",
            "chain",
            "resources",
            "queue",
            "budget",
            "recommendations",
            "_diagnostics",
        ):
            assert key in result, f"Отсутствует ключ: {key}"

    @pytest.mark.asyncio
    async def test_checks_subsection_keys(self, healthy_service):
        """checks содержит все 4 источника."""
        with patch("httpx.AsyncClient") as mock_httpx:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_httpx.return_value.__aenter__ = AsyncMock(return_value=mock_httpx.return_value)
            mock_httpx.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_httpx.return_value.get = AsyncMock(return_value=mock_response)

            result = await healthy_service.collect()

        checks = result["checks"]
        for src in ("openclaw", "local_lm", "voice_gateway", "krab_ear"):
            assert src in checks, f"Отсутствует источник: {src}"

    @pytest.mark.asyncio
    async def test_diagnostics_latency_summary_present(self, healthy_service):
        """_diagnostics содержит latency_summary по всем источникам."""
        with patch("httpx.AsyncClient") as mock_httpx:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_httpx.return_value.__aenter__ = AsyncMock(return_value=mock_httpx.return_value)
            mock_httpx.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_httpx.return_value.get = AsyncMock(return_value=mock_response)

            result = await healthy_service.collect()

        diag = result["_diagnostics"]
        assert "latency_summary" in diag
        assert "slowest_source" in diag
        assert "total_collect_ms" in diag


# ---------------------------------------------------------------------------
# Деградационные сценарии
# ---------------------------------------------------------------------------


class TestDegradation:
    """Уровни деградации AI-цепочки."""

    @pytest.mark.asyncio
    async def test_normal_when_cloud_ok(self, healthy_service):
        """При доступном OpenClaw — degradation=normal."""
        with patch("httpx.AsyncClient") as mock_httpx:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_httpx.return_value.__aenter__ = AsyncMock(return_value=mock_httpx.return_value)
            mock_httpx.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_httpx.return_value.get = AsyncMock(return_value=mock_response)

            result = await healthy_service.collect()

        assert result["degradation"] == "normal"
        assert result["chain"]["active_ai_channel"] == "cloud"

    @pytest.mark.asyncio
    async def test_degraded_to_local_when_no_cloud(self, no_cloud_service):
        """Нет OpenClaw + есть local → degraded_to_local_fallback."""
        with patch("httpx.AsyncClient") as mock_httpx:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_httpx.return_value.__aenter__ = AsyncMock(return_value=mock_httpx.return_value)
            mock_httpx.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_httpx.return_value.get = AsyncMock(return_value=mock_response)

            result = await no_cloud_service.collect()

        assert result["degradation"] == "degraded_to_local_fallback"
        assert result["chain"]["active_ai_channel"] == "local_fallback"

    @pytest.mark.asyncio
    async def test_critical_when_no_ai(self, critical_service):
        """Нет ни cloud, ни local → critical_no_ai_backend."""
        with patch("httpx.AsyncClient") as mock_httpx:
            mock_response = MagicMock()
            mock_response.status_code = 503
            mock_httpx.return_value.__aenter__ = AsyncMock(return_value=mock_httpx.return_value)
            mock_httpx.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_httpx.return_value.get = AsyncMock(return_value=mock_response)

            result = await critical_service.collect()

        assert result["degradation"] == "critical_no_ai_backend"
        assert result["risk_level"] == "high"
        assert result["status"] == "critical"

    @pytest.mark.asyncio
    async def test_risk_medium_when_degraded_to_local(self, no_cloud_service):
        """Деградация в local дает risk=medium."""
        with patch("httpx.AsyncClient") as mock_httpx:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_httpx.return_value.__aenter__ = AsyncMock(return_value=mock_httpx.return_value)
            mock_httpx.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_httpx.return_value.get = AsyncMock(return_value=mock_response)

            result = await no_cloud_service.collect()

        assert result["risk_level"] == "medium"


# ---------------------------------------------------------------------------
# _check_client_health
# ---------------------------------------------------------------------------


class TestCheckClientHealth:
    """Проверка внешнего клиента через _check_client_health()."""

    @pytest.mark.asyncio
    async def test_returns_ok_when_client_healthy(self):
        """Клиент вернул True → ok=True."""
        svc = EcosystemHealthService(router=_make_router())
        client = _make_client(ok=True)
        result = await svc._check_client_health(client, "test_source")
        assert result["ok"] is True
        assert result["status"] == "ok"

    @pytest.mark.asyncio
    async def test_returns_not_ok_when_client_unhealthy(self):
        """Клиент вернул False → ok=False."""
        svc = EcosystemHealthService(router=_make_router())
        client = _make_client(ok=False)
        result = await svc._check_client_health(client, "test_source")
        assert result["ok"] is False

    @pytest.mark.asyncio
    async def test_returns_not_configured_when_no_client(self):
        """Клиент None → not_configured."""
        svc = EcosystemHealthService(router=_make_router())
        result = await svc._check_client_health(None, "missing")
        assert result["ok"] is False
        assert result["status"] == "not_configured"

    @pytest.mark.asyncio
    async def test_returns_error_on_exception(self):
        """Исключение в health_check → ok=False, status содержит 'error'."""
        svc = EcosystemHealthService(router=_make_router())
        client = MagicMock()
        client.health_check = AsyncMock(side_effect=ConnectionError("refused"))
        result = await svc._check_client_health(client, "errored")
        assert result["ok"] is False
        assert "error" in result["status"]

    @pytest.mark.asyncio
    async def test_latency_ms_is_non_negative(self):
        """latency_ms всегда >= 0."""
        svc = EcosystemHealthService(router=_make_router())
        client = _make_client(ok=True)
        result = await svc._check_client_health(client, "src")
        assert result["latency_ms"] >= 0


# ---------------------------------------------------------------------------
# _check_local_health
# ---------------------------------------------------------------------------


class TestCheckLocalHealth:
    """Проверка локального AI через router.health_check()."""

    @pytest.mark.asyncio
    async def test_healthy_router_returns_ok(self):
        """router.health_check() → {'status': 'healthy'} даёт ok=True."""
        svc = EcosystemHealthService(router=_make_router("healthy"))
        result = await svc._check_local_health()
        assert result["ok"] is True

    @pytest.mark.asyncio
    async def test_unhealthy_router_returns_not_ok(self):
        """router.health_check() → {'status': 'unhealthy'} даёт ok=False."""
        svc = EcosystemHealthService(router=_make_router("unhealthy"))
        result = await svc._check_local_health()
        assert result["ok"] is False

    @pytest.mark.asyncio
    async def test_router_exception_returns_error(self):
        """Исключение в router.health_check() → ok=False с описанием ошибки."""
        router = MagicMock()
        router.health_check = AsyncMock(side_effect=RuntimeError("boom"))
        svc = EcosystemHealthService(router=router)
        result = await svc._check_local_health()
        assert result["ok"] is False
        assert "boom" in result["status"]


# ---------------------------------------------------------------------------
# _check_krab_ear_health (HTTP fallback)
# ---------------------------------------------------------------------------


class TestCheckKrabEarHealth:
    """Проверка Krab Ear через HTTP /health."""

    @pytest.mark.asyncio
    async def test_ok_when_http_200(self):
        """HTTP 200 → ok=True."""
        svc = EcosystemHealthService(
            router=_make_router(),
            krab_ear_backend_url="http://127.0.0.1:5005",
        )
        with patch("httpx.AsyncClient") as mock_httpx:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_httpx.return_value.__aenter__ = AsyncMock(return_value=mock_httpx.return_value)
            mock_httpx.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_httpx.return_value.get = AsyncMock(return_value=mock_response)

            result = await svc._check_krab_ear_health()

        assert result["ok"] is True
        assert result["status"] == "ok"

    @pytest.mark.asyncio
    async def test_not_ok_when_http_503(self):
        """HTTP 503 → ok=False, status='http_503'."""
        svc = EcosystemHealthService(
            router=_make_router(),
            krab_ear_backend_url="http://127.0.0.1:5005",
        )
        with patch("httpx.AsyncClient") as mock_httpx:
            mock_response = MagicMock()
            mock_response.status_code = 503
            mock_httpx.return_value.__aenter__ = AsyncMock(return_value=mock_httpx.return_value)
            mock_httpx.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_httpx.return_value.get = AsyncMock(return_value=mock_response)

            result = await svc._check_krab_ear_health()

        assert result["ok"] is False
        assert "503" in result["status"]

    @pytest.mark.asyncio
    async def test_error_on_connection_failure(self):
        """ConnectError → ok=False, status содержит 'error'."""
        svc = EcosystemHealthService(
            router=_make_router(),
            krab_ear_backend_url="http://127.0.0.1:5005",
        )
        with patch("httpx.AsyncClient") as mock_httpx:
            mock_httpx.return_value.__aenter__ = AsyncMock(return_value=mock_httpx.return_value)
            mock_httpx.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_httpx.return_value.get = AsyncMock(side_effect=Exception("Connection refused"))

            result = await svc._check_krab_ear_health()

        assert result["ok"] is False
        assert "error" in result["status"]

    @pytest.mark.asyncio
    async def test_uses_krab_ear_client_when_present(self):
        """Если krab_ear_client задан — использует его, а не httpx."""
        ear_client = _make_client(ok=True)
        svc = EcosystemHealthService(
            router=_make_router(),
            krab_ear_client=ear_client,
        )
        result = await svc._check_krab_ear_health()
        assert result["ok"] is True
        ear_client.health_check.assert_called_once()


# ---------------------------------------------------------------------------
# Timeout guard (_safe_run)
# ---------------------------------------------------------------------------


class TestTimeoutGuard:
    """Per-source timeout: зависший источник не роняет всё collect()."""

    @pytest.mark.asyncio
    async def test_collect_survives_slow_source(self):
        """Зависший openclaw_client не мешает получить ответ от остальных."""
        slow_client = MagicMock()

        async def _hang():
            await asyncio.sleep(10)
            return True

        slow_client.health_check = _hang
        slow_client.get_token_info = MagicMock(
            return_value={"is_configured": False, "masked_key": None}
        )

        svc = EcosystemHealthService(
            router=_make_router("healthy"),
            openclaw_client=slow_client,
            local_health_override={"ok": True, "status": "ok", "latency_ms": 1},
            krab_ear_backend_url="http://127.0.0.1:5005",
            timeout_sec=0.1,  # очень короткий таймаут
        )

        with patch("httpx.AsyncClient") as mock_httpx:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_httpx.return_value.__aenter__ = AsyncMock(return_value=mock_httpx.return_value)
            mock_httpx.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_httpx.return_value.get = AsyncMock(return_value=mock_response)

            result = await svc.collect()

        # Openclaw деградировал по timeout
        assert result["checks"]["openclaw"]["ok"] is False
        assert result["checks"]["openclaw"].get("degraded") is True
        # Остальное доступно
        assert result["checks"]["local_lm"]["ok"] is True


# ---------------------------------------------------------------------------
# local_health_override
# ---------------------------------------------------------------------------


class TestLocalHealthOverride:
    """Проверка что local_health_override не вызывает router.health_check."""

    @pytest.mark.asyncio
    async def test_override_bypasses_router(self):
        """При local_health_override — router.health_check() не вызывается."""
        router = _make_router("healthy")
        svc = EcosystemHealthService(
            router=router,
            openclaw_client=_make_client(ok=True),
            local_health_override={"ok": True, "status": "ok", "latency_ms": 0},
            krab_ear_backend_url="http://127.0.0.1:5005",
        )

        with patch("httpx.AsyncClient") as mock_httpx:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_httpx.return_value.__aenter__ = AsyncMock(return_value=mock_httpx.return_value)
            mock_httpx.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_httpx.return_value.get = AsyncMock(return_value=mock_response)

            await svc.collect()

        router.health_check.assert_not_called()


# ---------------------------------------------------------------------------
# Минимальный таймаут
# ---------------------------------------------------------------------------


class TestTimeoutClamping:
    """timeout_sec не может быть ниже 0.5."""

    def test_tiny_timeout_clamped_to_half_second(self):
        svc = EcosystemHealthService(router=_make_router(), timeout_sec=0.001)
        assert svc.timeout_sec == 0.5

    def test_normal_timeout_preserved(self):
        svc = EcosystemHealthService(router=_make_router(), timeout_sec=3.0)
        assert svc.timeout_sec == 3.0


# ---------------------------------------------------------------------------
# Recommendations
# ---------------------------------------------------------------------------


class TestRecommendations:
    """Правильность рекомендаций при различных состояниях."""

    @pytest.mark.asyncio
    async def test_recommendations_not_empty(self, healthy_service):
        """Список рекомендаций всегда непустой."""
        with patch("httpx.AsyncClient") as mock_httpx:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_httpx.return_value.__aenter__ = AsyncMock(return_value=mock_httpx.return_value)
            mock_httpx.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_httpx.return_value.get = AsyncMock(return_value=mock_response)

            result = await healthy_service.collect()

        assert len(result["recommendations"]) >= 1

    @pytest.mark.asyncio
    async def test_recommendations_limited_to_8(self, critical_service):
        """Список рекомендаций ограничен 8 элементами."""
        with patch("httpx.AsyncClient") as mock_httpx:
            mock_response = MagicMock()
            mock_response.status_code = 503
            mock_httpx.return_value.__aenter__ = AsyncMock(return_value=mock_httpx.return_value)
            mock_httpx.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_httpx.return_value.get = AsyncMock(return_value=mock_response)

            result = await critical_service.collect()

        assert len(result["recommendations"]) <= 8

    @pytest.mark.asyncio
    async def test_critical_recommendation_mentions_openclaw(self, critical_service):
        """При critical — рекомендация упоминает OpenClaw."""
        with patch("httpx.AsyncClient") as mock_httpx:
            mock_response = MagicMock()
            mock_response.status_code = 503
            mock_httpx.return_value.__aenter__ = AsyncMock(return_value=mock_httpx.return_value)
            mock_httpx.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_httpx.return_value.get = AsyncMock(return_value=mock_response)

            result = await critical_service.collect()

        all_recs = " ".join(result["recommendations"])
        assert "OpenClaw" in all_recs or "AI backend" in all_recs


# ---------------------------------------------------------------------------
# _collect_resource_metrics
# ---------------------------------------------------------------------------


class TestCollectResourceMetrics:
    """Метрики ресурсов процессора и памяти."""

    def test_returns_cpu_and_ram(self):
        """_collect_resource_metrics возвращает cpu и ram поля."""
        svc = EcosystemHealthService(router=_make_router())
        metrics = svc._collect_resource_metrics()
        assert "cpu_percent" in metrics
        assert "ram_percent" in metrics
        assert "ram_available_gb" in metrics

    def test_returns_error_dict_on_failure(self):
        """При ошибке psutil возвращает словарь с ключом 'error', не исключение."""
        svc = EcosystemHealthService(router=_make_router())
        with patch("psutil.cpu_percent", side_effect=RuntimeError("no psutil")):
            metrics = svc._collect_resource_metrics()
        assert "error" in metrics
