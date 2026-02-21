# -*- coding: utf-8 -*-
import pytest
from unittest.mock import MagicMock, AsyncMock
from src.core.ecosystem_health import EcosystemHealthService

@pytest.fixture
def health_service():
    router = MagicMock()
    # Mock CostEngine
    router.cost_engine = MagicMock()
    
    # Mock router health checks
    router.check_local_health = AsyncMock(return_value=True)
    
    service = EcosystemHealthService(router=router)
    return service

@pytest.mark.asyncio
async def test_health_budget_integration_economy(health_service):
    """Проверка интеграции режима экономии в рекомендации."""
    health_service.router.cost_engine.get_budget_status.return_value = {
        "monthly_budget": 25.0,
        "monthly_spent": 21.0,
        "usage_percent": 84.0,
        "is_economy_mode": True,
        "month_progress_percent": 50.0,
        "runway_days": 10.0
    }
    
    # Mock other checks to be fast/OK
    health_service._check_client_health = AsyncMock(return_value={"ok": True, "status": "ok"})
    health_service._check_krab_ear_health = AsyncMock(return_value={"ok": True, "status": "ok"})
    
    report = await health_service.collect()
    
    recs = report["recommendations"]
    assert any("РЕЖИМ ЭКОНОМИИ" in r for r in recs)
    assert report["budget"]["is_economy_mode"] is True

@pytest.mark.asyncio
async def test_health_budget_integration_critical_runway(health_service):
    """Проверка предупреждения о критическом остатке дней (runway)."""
    health_service.router.cost_engine.get_budget_status.return_value = {
        "monthly_budget": 25.0,
        "monthly_spent": 24.5,
        "usage_percent": 98.0,
        "is_economy_mode": True,
        "month_progress_percent": 90.0,
        "runway_days": 1.5
    }
    
    # Mock other checks
    health_service._check_client_health = AsyncMock(return_value={"ok": True, "status": "ok"})
    health_service._check_krab_ear_health = AsyncMock(return_value={"ok": True, "status": "ok"})

    report = await health_service.collect()
    
    recs = report["recommendations"]
    assert any("КРИТИЧЕСКИЙ БЮДЖЕТ" in r for r in recs)
    assert any("1.5 дн" in r for r in recs)

if __name__ == "__main__":
    import sys
    import pytest
    sys.exit(pytest.main([__file__]))
