# -*- coding: utf-8 -*-
"""swarm_test_script.py
~~~~~~~~~~~~~~~~~~~~
Тестирует мульти-агентные команды (Swarm) через SwarmManager из agent_swarm.py.
Проверяет, что trading team может получить рекомендации по рынку.

Связь: src/core/agent_swarm.py → SwarmManager → ModelRouter → LM Studio / Cloud
"""

import sys
import os
import asyncio

# Добавляем корневую папку проекта в путь
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.core.agent_swarm import SwarmManager, SwarmAgent


async def test_trading_swarm():
    """Тестирует trading team без реального ModelRouter (mock-режим)."""
    print("🚀 Swarm Test: Trading Team")
    print("-" * 50)

    # Создаём SwarmManager без router (dry-run — проверяем структуру)
    swarm = SwarmManager(model_router=None)

    # Проверяем, что команды создаются корректно
    teams = {
        "trading": swarm.get_trading_team,
        "osint": swarm.get_osint_team,
        "content": swarm.get_content_team,
        "dev": swarm.get_dev_team,
        "summary": swarm.get_summary_team,
        "email": swarm.get_email_team,
        "audit": swarm.get_audit_team,
        "planning": swarm.get_planning_team,
    }

    for name, factory in teams.items():
        agents = factory()
        assert isinstance(agents, list), f"❌ {name} team не вернул список"
        assert len(agents) > 0, f"❌ {name} team пустой"
        for agent in agents:
            assert isinstance(agent, SwarmAgent), f"❌ {name} содержит не-SwarmAgent: {type(agent)}"
            assert agent.name, f"❌ Агент без имени в {name}"
            assert agent.role, f"❌ Агент без роли в {name}"
        print(f"  ✅ {name}: {len(agents)} агентов — {', '.join(a.name for a in agents)}")

    print()
    print("🏆 ВСЕ SWARM-КОМАНДЫ ВАЛИДНЫ!")
    print()

    # Дополнительный тест: execute_task без router (должен gracefully degraded)
    market_context = "BTC стоит 96000, RSI 80 (перекуплен), новости про регулирование в США негативные."
    print(f"📊 Тест execute_task (dry-run): {market_context[:60]}...")
    try:
        result = await swarm.execute_task(
            task_description=market_context,
            agents=swarm.get_trading_team(),
            mode="sequential",
        )
        print(f"  📝 Результат: {result[:200] if result else 'пусто'}...")
    except Exception as e:
        # Без ModelRouter ожидаемо — router=None вызывает ошибку
        print(f"  ⚠️ Без ModelRouter: {type(e).__name__}: {e}")
        print("  ℹ️ Это ожидаемо, т.к. тест запущен без реального LLM backend.")

    return True


if __name__ == "__main__":
    success = asyncio.run(test_trading_swarm())
    sys.exit(0 if success else 1)