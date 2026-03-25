
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
    assert len(parts) >= 2
    assert all(len(part) <= 100 for part in parts)
    assert parts[0].startswith("[Часть 1/")
    assert parts[-1].startswith(f"[Часть {len(parts)}/")

    # Test 3: Multiple splits
    text = "a" * 250
    parts = bot._split_message(text, limit=100)
    assert len(parts) >= 3
    assert all(len(part) <= 100 for part in parts)
    assert parts[0].startswith("[Часть 1/")
    assert parts[-1].startswith(f"[Часть {len(parts)}/")


def test_split_message_preserves_multiline_structure():
    bot = MockUserbot()
    text = "Заголовок\n\n" + ("Строка с полезным содержимым.\n" * 30)

    parts = bot._split_message(text, limit=140)

    assert len(parts) >= 2
    assert parts[0].startswith("[Часть 1/")
    assert parts[-1].startswith(f"[Часть {len(parts)}/")
    assert any("Строка с полезным содержимым." in part for part in parts)
