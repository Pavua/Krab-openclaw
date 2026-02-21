import pytest
from src.core.telegram_chat_resolver import TelegramChatResolver

def test_normalize_target_hardening():
    """Проверка устойчивости нормализации таргета (R8)."""
    # 1. Лишние пробелы и кавычки
    assert TelegramChatResolver.normalize_target('  "@group"  ') == "@group"
    assert TelegramChatResolver.normalize_target("  '12345'  ") == "12345"
    
    # 2. Множественные префиксы @
    assert TelegramChatResolver.normalize_target("@@@username") == "@username"
    assert TelegramChatResolver.normalize_target("@username") == "@username"
    
    # 3. Улучшенный t.me (с протоколом и без, со слэшем и без)
    assert TelegramChatResolver.normalize_target("https://t.me/p0lrd") == "@p0lrd"
    assert TelegramChatResolver.normalize_target("t.me/p0lrd/") == "@p0lrd"
    assert TelegramChatResolver.normalize_target("http://t.me/joinchat/123") == "@joinchat" # Упрощенно, по регулярке
    
    # 4. Пустой ввод
    assert TelegramChatResolver.normalize_target("") == ""
    assert TelegramChatResolver.normalize_target(None) == ""
    
    # 5. Цифровые ID
    assert TelegramChatResolver.normalize_target("-100123456789") == "-100123456789"
    assert TelegramChatResolver.normalize_target("123456") == "123456"
