# -*- coding: utf-8 -*-
import asyncio
import json
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

# Добавляем путь к src
sys.path.append(str(Path(__file__).parent.parent))

from src.core.model_manager import ModelRouter


async def verify_limits():
    print("🧪 Verifying LM Studio Request Payload...")

    config = {"LOCAL_LLM_URL": "http://localhost:1234/v1", "LOCAL_ENGINE": "lm-studio"}

    router = ModelRouter(config=config)
    router.active_local_model = "test-model"
    router.is_local_available = True

    # Мокаем aiohttp.ClientSession.post
    with patch("aiohttp.ClientSession.post") as mock_post:
        # Настраиваем мок ответа
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(
            return_value={"choices": [{"message": {"content": "Hello world"}}]}
        )
        mock_post.return_value.__aenter__.return_value = mock_resp

        await router._call_local_llm("Test prompt")

        # Проверяем аргументы вызова
        args, kwargs = mock_post.call_args
        payload = kwargs.get("json", {})

        print(f"📦 Sent payload: {json.dumps(payload, indent=2, ensure_ascii=False)}")

        if payload.get("max_tokens") == 2048:
            print("✅ SUCCESS: max_tokens is present and correct.")
        else:
            print(f"❌ FAILED: max_tokens is {payload.get('max_tokens')}")

        if "stop" in payload and "<|im_end|>" in payload["stop"]:
            print("✅ SUCCESS: stop sequences are present.")
        else:
            print("❌ FAILED: stop sequences are missing.")


if __name__ == "__main__":
    asyncio.run(verify_limits())
