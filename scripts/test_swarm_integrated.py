"""
scripts/test_swarm_integrated.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Интеграционный тест для проверки нативного SwarmManager с доменными командами.
"""

import asyncio
import os
import sys
from dotenv import load_dotenv

# Добавляем корень проекта в путь
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.core.model_manager import ModelRouter

async def test_trading_swarm():
    print("\n🚀 Starting Trading Swarm Integration Test...")
    load_dotenv(override=True)
    
    # Инициализация роутера и роя
    config = os.environ.copy()
    router = ModelRouter(config=config)
    
    market_prompt = "BTC/USDT, цена 65000, RSI 75 (перекупленность), MACD медвежий разворот."
    
    print(f"📊 Market Context: {market_prompt}")
    
    # Запуск торговой команды
    print("⏳ Running Trading Team (Sequential)...")
    result = await router.swarm.run_team("trading", market_prompt)
    
    print("\n=== SWARM EXECUTION RESULT ===")
    print(result)
    print("==============================\n")

if __name__ == "__main__":
    asyncio.run(test_trading_swarm())
