# -*- coding: utf-8 -*-
"""
Verification Script for Context Clipping and Persona Management
"""
import sys
import os
import asyncio

# Добавляем корень проекта в sys.path
sys.path.append(os.getcwd())

from src.core.context_manager import ContextKeeper
from src.core.persona_manager import PersonaManager

async def verify_context_and_persona():
    print("🧪 Verifying Context Clipping...")
    keeper = ContextKeeper()
    
    # 1. Тест клиппинга
    chat_id = 999
    for i in range(20):
        keeper.save_message(chat_id, {"role": "user", "text": f"Long message padding content {i}" * 10})
    
    # Пытаемся получить контекст с лимитом в 200 токенов (очень мало)
    context = keeper.get_token_aware_context(chat_id, max_tokens=200)
    print(f"✅ Clipped context length: {len(context)} messages")
    
    # Проверяем, что роли нормализованы (внутренняя проверка)
    for msg in context:
        assert msg["role"] in ["user", "assistant", "system"]
    print("✅ Role normalization verified.")

    # 2. Тест персоны
    print("\n🧪 Verifying Persona Management...")
    from src.core.config_manager import ConfigManager
    from src.utils.black_box import BlackBox
    
    cfg = ConfigManager()
    bb = BlackBox()
    persona = PersonaManager(cfg, bb)
    
    # Пытаемся загрузить из soul.md
    p_text = persona.get_current_prompt(chat_type="private", is_owner=True)
    print(f"✅ Persona/Prompt length: {len(p_text)} chars")
    if "Краб" in p_text or "Krab" in p_text:
        print("✅ Persona contains branding.")
    else:
        print("⚠️ Persona content might be generic.")

if __name__ == "__main__":
    from src.core.logger_setup import setup_logger
    setup_logger()
    asyncio.run(verify_context_and_persona())
