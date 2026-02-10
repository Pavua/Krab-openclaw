# -*- coding: utf-8 -*-
from src.core.persona_manager import PersonaManager
from unittest.mock import MagicMock

def test_persona_content_integrity():
    manager = PersonaManager(MagicMock(), MagicMock())
    
    # Продвинутые личности
    manager.set_persona("coder")
    prompt = manager.get_current_prompt()
    assert "Senior Architect" in prompt
    assert "Python" in prompt
    
    manager.set_persona("waifu")
    prompt = manager.get_current_prompt()
    assert "Краб-тян" in prompt
    assert "✨" in prompt

def test_persona_info_access():
    manager = PersonaManager(MagicMock(), MagicMock())
    info = manager.get_persona_info("pirate")
    assert info['name'] == "Captain Krab"
    assert "⚓" in info['prompt']

