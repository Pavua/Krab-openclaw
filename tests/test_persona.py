# -*- coding: utf-8 -*-
import pytest
from unittest.mock import MagicMock
from src.core.persona_manager import PersonaManager

def test_persona_switching():
    cfg = MagicMock()
    bb = MagicMock()
    
    manager = PersonaManager(cfg, bb)
    
    # Дефолтная роль
    assert "krab v7.5" in manager.get_current_prompt().lower()
    
    # Переключение на пирата
    success = manager.set_persona("pirate")
    assert success is True
    assert "капитан краб" in manager.get_current_prompt().lower()
    
    # Проверка сохранения в конфиг
    cfg.set.assert_called_with("personality.active_persona", "pirate")

def test_add_custom_persona():
    cfg = MagicMock()
    bb = MagicMock()
    manager = PersonaManager(cfg, bb)
    
    manager.add_custom_persona("test_id", "Test Name", "Test Prompt", "Test Desc")
    assert "test_id" in manager.personas
    assert manager.personas["test_id"]["name"] == "Test Name"
    assert manager.personas["test_id"]["prompt"] == "Test Prompt"

def test_persona_invalid():
    manager = PersonaManager(MagicMock(), MagicMock())
    assert manager.set_persona("non_existent") is False
