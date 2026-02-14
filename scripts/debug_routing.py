
import asyncio
import os
import structlog
from dotenv import load_dotenv
from src.core.model_manager import ModelRouter

# Настройка логгера для теста
structlog.configure(
    processors=[structlog.processors.JSONRenderer()],
)

async def test_routing():
    load_dotenv(override=True)
    router = ModelRouter(config=os.environ)
    
    # Имитируем отсутствие локальных моделей
    router.is_local_available = False
    router.openclaw_client.base_url = "http://localhost:9999" # Несуществующий порт для быстрого отказа
    
    print("\n=== Initial State (Mocked Failure) ===")
    print(f"Force Mode: {router.force_mode}")
    print(f"Local Available: {router.is_local_available}")
    print(f"Active Local Model: {router.active_local_model}")
    print(f"Last Cloud Error: {router.last_cloud_error}")
    
    print("\n=== Testing Auto Route (Normal Request) ===")
    # Тестируем метод route_query
    # Мы хотим увидеть, куда он пойдет, если облако выдает ошибку, а локалка пуста или наоборот
    try:
        # Пробуем вызвать route_query_stream (так как ai.py использует его)
        print("Starting stream routing...")
        found_any = False
        async for chunk in router.route_query_stream("Привет, как дела?", task_type="chat"):
            print(f"Chunk received: {chunk[:50]}...")
            found_any = True
        
        if not found_any:
            print("❌ No response from stream!")
    except Exception as e:
        print(f"❌ Routing Exception: {e}")

if __name__ == "__main__":
    asyncio.run(test_routing())
