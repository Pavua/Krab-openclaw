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


# ---------------------------------------------------------------------------
# [Session 10] Статистика новых подсистем в `collect()`
# ---------------------------------------------------------------------------


class TestSession10Block:
    """[Session 10] Проверка нового блока session_10 в ответе collect()."""

    @pytest.mark.asyncio
    async def test_collect_includes_session_10_block(self, healthy_service):
        """collect() возвращает ключ session_10 на верхнем уровне."""
        with patch("httpx.AsyncClient") as mock_httpx:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_httpx.return_value.__aenter__ = AsyncMock(return_value=mock_httpx.return_value)
            mock_httpx.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_httpx.return_value.get = AsyncMock(return_value=mock_response)

            result = await healthy_service.collect()

        assert "session_10" in result
        s10 = result["session_10"]
        for key in (
            "memory_validator",
            "memory_archive",
            "dedicated_chrome",
            "auto_restart",
            "gemini_nonce",
        ):
            assert key in s10, f"session_10 missing key: {key}"

    @pytest.mark.asyncio
    async def test_memory_validator_stats_fields(self, healthy_service):
        """memory_validator stats имеет все нужные int-поля."""
        with patch("httpx.AsyncClient") as mock_httpx:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_httpx.return_value.__aenter__ = AsyncMock(return_value=mock_httpx.return_value)
            mock_httpx.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_httpx.return_value.get = AsyncMock(return_value=mock_response)

            result = await healthy_service.collect()

        mv = result["session_10"]["memory_validator"]
        for key in (
            "safe_total",
            "injection_blocked_total",
            "confirmed_total",
            "confirm_failed_total",
            "pending_count",
        ):
            assert key in mv, f"memory_validator missing: {key}"
            assert isinstance(mv[key], int), f"memory_validator.{key} not int"

    @pytest.mark.asyncio
    async def test_memory_archive_stats_fields(self, healthy_service):
        """memory_archive содержит exists + счётчики."""
        with patch("httpx.AsyncClient") as mock_httpx:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_httpx.return_value.__aenter__ = AsyncMock(return_value=mock_httpx.return_value)
            mock_httpx.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_httpx.return_value.get = AsyncMock(return_value=mock_response)

            result = await healthy_service.collect()

        ma = result["session_10"]["memory_archive"]
        assert "exists" in ma
        # Если archive.db присутствует — счётчики должны быть int
        if ma["exists"]:
            assert ma["message_count"] >= 0
            assert ma["chats_count"] >= 0
            assert ma["chunks_count"] >= 0
            assert ma["size_bytes"] >= 0

    @pytest.mark.asyncio
    async def test_dedicated_chrome_fields(self, healthy_service):
        """dedicated_chrome содержит enabled/running/port."""
        with patch("httpx.AsyncClient") as mock_httpx:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_httpx.return_value.__aenter__ = AsyncMock(return_value=mock_httpx.return_value)
            mock_httpx.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_httpx.return_value.get = AsyncMock(return_value=mock_response)

            result = await healthy_service.collect()

        dc = result["session_10"]["dedicated_chrome"]
        assert "enabled" in dc
        assert "running" in dc
        assert "port" in dc
        assert isinstance(dc["enabled"], bool)
        assert isinstance(dc["running"], bool)
        assert isinstance(dc["port"], int)

    @pytest.mark.asyncio
    async def test_auto_restart_fields(self, healthy_service):
        """auto_restart содержит enabled/services_tracked/total_attempts."""
        with patch("httpx.AsyncClient") as mock_httpx:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_httpx.return_value.__aenter__ = AsyncMock(return_value=mock_httpx.return_value)
            mock_httpx.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_httpx.return_value.get = AsyncMock(return_value=mock_response)

            result = await healthy_service.collect()

        ar = result["session_10"]["auto_restart"]
        assert "enabled" in ar
        assert "services_tracked" in ar
        assert "total_attempts_last_hour" in ar
        assert isinstance(ar["enabled"], bool)
        assert isinstance(ar["services_tracked"], list)
        assert isinstance(ar["total_attempts_last_hour"], int)

    @pytest.mark.asyncio
    async def test_gemini_nonce_fields(self, healthy_service):
        """gemini_nonce содержит tracked_chats (int)."""
        with patch("httpx.AsyncClient") as mock_httpx:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_httpx.return_value.__aenter__ = AsyncMock(return_value=mock_httpx.return_value)
            mock_httpx.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_httpx.return_value.get = AsyncMock(return_value=mock_response)

            result = await healthy_service.collect()

        gn = result["session_10"]["gemini_nonce"]
        assert "tracked_chats" in gn
        assert isinstance(gn["tracked_chats"], int)


class TestSession10MemoryValidator:
    """[Session 10] Прямые вызовы _session_10_memory_validator() с моками."""

    def test_returns_default_when_module_missing(self, monkeypatch):
        """Если memory_validator.py отсутствует — возвращаем 'available'=False.

        Симулируем отсутствие модуля: sys.modules[...] = None заставит
        Python бросить ImportError при `from src.core import memory_validator`.
        Также удаляем атрибут с parent-пакета, т.к. submodule уже мог быть
        импортирован в других тестах и закэширован как `src.core.memory_validator`.
        """
        import sys

        import src.core as _pkg

        # Эмулируем отсутствие модуля для lazy import внутри функции
        monkeypatch.setitem(sys.modules, "src.core.memory_validator", None)
        monkeypatch.delattr(_pkg, "memory_validator", raising=False)

        result = EcosystemHealthService._session_10_memory_validator()
        assert "safe_total" in result
        assert "injection_blocked_total" in result
        assert result["safe_total"] == 0
        assert result["injection_blocked_total"] == 0
        # Модуль отсутствует → available=False
        assert result.get("available") is False

    def test_reads_stats_from_mocked_module(self, monkeypatch):
        """При наличии memory_validator читает stats + list_pending.

        Python cache nuance: `from src.core import memory_validator` резолвит
        атрибут на parent-пакете `src.core`. Если модуль уже был импортирован
        (напр. в другом тесте), замена только sys.modules недостаточна —
        нужно обновить и `src.core.memory_validator` attribute.
        """
        import sys
        import types

        import src.core as _pkg

        fake_module = types.ModuleType("src.core.memory_validator")
        fake_module.stats = {
            "safe_total": 42,
            "injection_blocked_total": 7,
            "confirmed_total": 3,
            "confirm_failed_total": 1,
        }
        fake_module.list_pending = lambda: ["req1", "req2"]

        monkeypatch.setitem(sys.modules, "src.core.memory_validator", fake_module)
        monkeypatch.setattr(_pkg, "memory_validator", fake_module, raising=False)

        result = EcosystemHealthService._session_10_memory_validator()
        assert result["available"] is True
        assert result["safe_total"] == 42
        assert result["injection_blocked_total"] == 7
        assert result["confirmed_total"] == 3
        assert result["confirm_failed_total"] == 1
        assert result["pending_count"] == 2


class TestSession10MemoryArchive:
    """[Session 10] Проверка чтения archive.db."""

    def test_returns_not_exists_when_db_missing(self, tmp_path, monkeypatch):
        """Если DB не существует — exists=False + нулевые счётчики."""
        # Подменяем HOME на временную директорию — DB гарантированно нет
        monkeypatch.setenv("HOME", str(tmp_path))
        result = EcosystemHealthService._session_10_memory_archive()
        assert result["exists"] is False
        assert result["size_bytes"] == 0
        assert result["message_count"] == 0

    def test_reads_counters_from_real_db(self, tmp_path, monkeypatch):
        """Если DB есть и схема валидна — читает счётчики через read-only."""
        import sqlite3

        # Создаём тестовую DB со схемой (минимальные таблицы)
        fake_home = tmp_path
        db_dir = fake_home / ".openclaw" / "krab_memory"
        db_dir.mkdir(parents=True, exist_ok=True)
        db_path = db_dir / "archive.db"

        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY)")
        conn.execute("CREATE TABLE chats (id INTEGER PRIMARY KEY)")
        conn.execute("CREATE TABLE chunks (id INTEGER PRIMARY KEY)")
        conn.executemany("INSERT INTO messages VALUES (?)", [(i,) for i in range(5)])
        conn.executemany("INSERT INTO chats VALUES (?)", [(i,) for i in range(2)])
        conn.executemany("INSERT INTO chunks VALUES (?)", [(i,) for i in range(3)])
        conn.commit()
        conn.close()

        monkeypatch.setenv("HOME", str(fake_home))
        result = EcosystemHealthService._session_10_memory_archive()
        assert result["exists"] is True
        assert result["size_bytes"] > 0
        assert result["message_count"] == 5
        assert result["chats_count"] == 2
        assert result["chunks_count"] == 3


class TestSession10DedicatedChrome:
    """[Session 10] Проверка _session_10_dedicated_chrome."""

    def test_default_disabled(self, monkeypatch):
        """Без ENV — enabled=False, port=9222."""
        monkeypatch.delenv("DEDICATED_CHROME_ENABLED", raising=False)
        monkeypatch.delenv("DEDICATED_CHROME_PORT", raising=False)
        result = EcosystemHealthService._session_10_dedicated_chrome()
        assert result["enabled"] is False
        assert result["port"] == 9222
        assert result["running"] is False

    def test_enabled_via_env(self, monkeypatch):
        """DEDICATED_CHROME_ENABLED=true → enabled=True."""
        monkeypatch.setenv("DEDICATED_CHROME_ENABLED", "true")
        monkeypatch.setenv("DEDICATED_CHROME_PORT", "9333")
        result = EcosystemHealthService._session_10_dedicated_chrome()
        assert result["enabled"] is True
        assert result["port"] == 9333

    def test_invalid_port_falls_back_to_default(self, monkeypatch):
        """Невалидный port → fallback 9222."""
        monkeypatch.setenv("DEDICATED_CHROME_PORT", "not-a-number")
        result = EcosystemHealthService._session_10_dedicated_chrome()
        assert result["port"] == 9222


class TestSession10AutoRestart:
    """[Session 10] Проверка _session_10_auto_restart."""

    def test_default_disabled(self, monkeypatch):
        """Без ENV — enabled=False, пустой список."""
        monkeypatch.delenv("AUTO_RESTART_ENABLED", raising=False)
        result = EcosystemHealthService._session_10_auto_restart()
        assert result["enabled"] is False
        assert result["services_tracked"] == []
        assert result["total_attempts_last_hour"] == 0

    def test_reads_states_from_mocked_module(self, monkeypatch):
        """Подменяем auto_restart_manager — читаем services + attempts."""
        import sys
        import types

        fake_module = types.ModuleType("src.core.auto_restart_manager")

        class _FakeState:
            def __init__(self, attempts_count: int):
                self.attempts = list(range(attempts_count))

        fake_module._states = {
            "openclaw": _FakeState(3),
            "krab_ear": _FakeState(1),
        }
        sys.modules["src.core.auto_restart_manager"] = fake_module
        try:
            monkeypatch.setenv("AUTO_RESTART_ENABLED", "1")
            result = EcosystemHealthService._session_10_auto_restart()
            assert result["enabled"] is True
            assert set(result["services_tracked"]) == {"openclaw", "krab_ear"}
            assert result["total_attempts_last_hour"] == 4
        finally:
            sys.modules.pop("src.core.auto_restart_manager", None)


class TestSession10GeminiNonce:
    """[Session 10] Проверка _session_10_gemini_nonce."""

    def test_default_zero_when_module_missing(self, monkeypatch):
        """Модуль gemini_cache_nonce отсутствует → tracked_chats=0.

        См. TestSession10MemoryValidator: чтобы lazy import реально провалился,
        помечаем модуль как None в sys.modules + удаляем атрибут parent-пакета.
        """
        import sys

        import src.core as _pkg

        monkeypatch.setitem(sys.modules, "src.core.gemini_cache_nonce", None)
        monkeypatch.delattr(_pkg, "gemini_cache_nonce", raising=False)

        result = EcosystemHealthService._session_10_gemini_nonce()
        assert result == {"tracked_chats": 0}

    def test_reads_map_from_mocked_module(self, monkeypatch):
        """Подменяем модуль — читаем _GEMINI_NONCE_MAP.

        См. TestSession10MemoryValidator: нужно патчить оба — sys.modules и
        атрибут parent-пакета `src.core.gemini_cache_nonce`.
        """
        import sys
        import types

        import src.core as _pkg

        fake_module = types.ModuleType("src.core.gemini_cache_nonce")
        fake_module._GEMINI_NONCE_MAP = {1: "nonceA", 2: "nonceB", 3: "nonceC"}

        monkeypatch.setitem(sys.modules, "src.core.gemini_cache_nonce", fake_module)
        monkeypatch.setattr(_pkg, "gemini_cache_nonce", fake_module, raising=False)

        result = EcosystemHealthService._session_10_gemini_nonce()
        assert result["tracked_chats"] == 3
