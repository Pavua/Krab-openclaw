
import pytest
from src.userbot_bridge import KraabUserbot

class MockUserbot(KraabUserbot):
    def __init__(self):
        # Bypass super().__init__ to avoid client creation
        pass

def test_split_message():
    bot = MockUserbot()
    
    # Test 1: Short message
    text = "Short message"
    parts = bot._split_message(text, limit=100)
    assert len(parts) == 1
    assert parts[0] == text
    
    # Test 2: Long message
    text = "a" * 150
    parts = bot._split_message(text, limit=100)
    assert len(parts) == 2
    assert len(parts[0]) == 100
    assert len(parts[1]) == 50
    
    # Test 3: Multiple splits
    text = "a" * 250
    parts = bot._split_message(text, limit=100)
    assert len(parts) == 3
