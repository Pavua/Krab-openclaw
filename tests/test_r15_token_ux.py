import pytest
from src.core.openclaw_client import OpenClawClient

def test_token_info_masking():
    # 1. Текстовый ключ
    client = OpenClawClient(api_key="sk-ant-1234567890abcdef")
    info = client.get_token_info()
    assert info["is_configured"] is True
    assert info["masked_key"] == "sk-ant...cdef"
    
    # 2. Короткий ключ
    client = OpenClawClient(api_key="123")
    info = client.get_token_info()
    assert info["is_configured"] is True
    assert info["masked_key"] == "****"
    
    # 3. Отсутствие ключа
    client = OpenClawClient(api_key=None)
    info = client.get_token_info()
    assert info["is_configured"] is False
    assert info["masked_key"] is None

def test_token_info_not_in_logs(caplog):
    import logging
    logger = logging.getLogger("src.core.openclaw_client")
    client = OpenClawClient(api_key="secret-key-that-should-not-be-logged")
    
    # Вызов метода не должен ничего логировать
    client.get_token_info()
    
    for record in caplog.records:
        assert "secret-key" not in record.message
