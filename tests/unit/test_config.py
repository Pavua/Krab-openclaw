"""
Unit тесты для модуля конфигурации
"""
import pytest
import os
from unittest.mock import patch


class TestConfig:
    """Тесты для класса Config"""
    
    def test_config_defaults(self):
        """Тест значений по умолчанию"""
        from src.config import Config
        
        assert Config.OPENCLAW_URL == "http://127.0.0.1:18789"  # или из .env
        assert Config.MAX_RAM_GB == 24  # или из .env
        assert Config.LOG_LEVEL in ["INFO", "DEBUG", "WARNING", "ERROR"]
    
    def test_config_validate_missing_api_id(self):
        """Тест валидации при отсутствии API_ID"""
        from src.config import Config
        
        with patch.object(Config, 'TELEGRAM_API_ID', 0):
            with patch.object(Config, 'TELEGRAM_API_HASH', ""):
                errors = Config.validate()
                
                assert "TELEGRAM_API_ID не установлен" in errors
                assert "TELEGRAM_API_HASH не установлен" in errors
    
    def test_config_validate_success(self):
        """Тест успешной валидации"""
        from src.config import Config
        
        with patch.object(Config, 'TELEGRAM_API_ID', 12345678):
            with patch.object(Config, 'TELEGRAM_API_HASH', "abc123def456"):
                errors = Config.validate()
                
                assert len(errors) == 0
                assert Config.is_valid() is True
    
    def test_trigger_prefixes(self):
        """Тест триггерных префиксов"""
        from src.config import Config
        
        assert "!краб" in Config.TRIGGER_PREFIXES
        assert "@краб" in Config.TRIGGER_PREFIXES
