"""Тесты для ChatWindowManager."""

import time
from unittest import mock

from src.core.chat_window_manager import (
    CAPACITY,
    IDLE_EVICTION_SEC,
    MESSAGE_CAP_PER_WINDOW,
    ChatWindow,
    ChatWindowManager,
)


class TestChatWindow:
    """Тесты класса ChatWindow."""

    def test_append_message(self):
        """Должно добавлять сообщение и обновлять last_activity_at."""
        win = ChatWindow(chat_id="c1")
        initial_activity = win.last_activity_at

        time.sleep(0.01)  # гарантируем разницу во времени
        win.append_message("user", "Hello")

        assert len(win.messages) == 1
        assert win.messages[0].role == "user"
        assert win.messages[0].content == "Hello"
        assert win.last_activity_at > initial_activity

    def test_message_cap(self):
        """Должно обрезать до MESSAGE_CAP_PER_WINDOW."""
        with mock.patch("src.core.chat_window_manager.MESSAGE_CAP_PER_WINDOW", 5):
            win = ChatWindow(chat_id="c1")
            for i in range(10):
                win.append_message("user", f"msg{i}")

            assert len(win.messages) == 5
            # Должны быть последние 5
            assert win.messages[0].content == "msg5"
            assert win.messages[-1].content == "msg9"

    def test_to_dict(self):
        """Должно сериализоваться в dict."""
        win = ChatWindow(chat_id="c1")
        win.append_message("user", "Hello")

        data = win.to_dict()
        assert data["chat_id"] == "c1"
        assert data["message_count"] == 1
        assert "created_at" in data
        assert "idle_sec" in data


class TestChatWindowManager:
    """Тесты ChatWindowManager."""

    def test_get_or_create(self):
        """Должно создавать новое окно."""
        mgr = ChatWindowManager(capacity=10)
        win = mgr.get_or_create("chat1")

        assert win.chat_id == "chat1"
        assert len(win.messages) == 0
        # Второй вызов должен вернуть то же окно
        win2 = mgr.get_or_create("chat1")
        assert win is win2

    def test_peek(self):
        """Должно возвращать окно без создания."""
        mgr = ChatWindowManager(capacity=10)
        assert mgr.peek("chat1") is None

        mgr.get_or_create("chat1")
        assert mgr.peek("chat1") is not None

    def test_capacity_overflow(self):
        """Должно выгонять старые окна при переполнении."""
        mgr = ChatWindowManager(capacity=3)

        # Создаём 4 окна
        _ = mgr.get_or_create("c1")
        time.sleep(0.01)
        _ = mgr.get_or_create("c2")
        time.sleep(0.01)
        _ = mgr.get_or_create("c3")
        time.sleep(0.01)
        _ = mgr.get_or_create("c4")  # Должно выгнать c1

        assert mgr.peek("c1") is None  # c1 выгнано (самое старое)
        assert mgr.peek("c2") is not None
        assert mgr.peek("c3") is not None
        assert mgr.peek("c4") is not None

    def test_evict_idle(self):
        """Должно выгонять неактивные окна."""
        mgr = ChatWindowManager(capacity=10)

        w1 = mgr.get_or_create("c1")
        w1.last_activity_at = time.time() - 7200  # 2 часа назад

        _ = mgr.get_or_create("c2")
        # c2 актуально (только что создано)

        removed = mgr.evict_idle(timeout_sec=3600)
        assert removed == 1
        assert mgr.peek("c1") is None
        assert mgr.peek("c2") is not None

    def test_evict_idle_default_timeout(self):
        """Должно использовать IDLE_EVICTION_SEC по умолчанию."""
        mgr = ChatWindowManager(capacity=10)

        w1 = mgr.get_or_create("c1")
        # Имитируем очень старое окно
        w1.last_activity_at = time.time() - (IDLE_EVICTION_SEC + 100)

        # Не указываем timeout — должен использовать default
        removed = mgr.evict_idle()
        assert removed == 1

    def test_list_windows(self):
        """Должно возвращать список всех окон."""
        mgr = ChatWindowManager(capacity=10)

        mgr.get_or_create("c1")
        mgr.get_or_create("c2")

        windows = mgr.list_windows()
        assert len(windows) == 2
        chat_ids = {w["chat_id"] for w in windows}
        assert chat_ids == {"c1", "c2"}

    def test_clear_all(self):
        """Должно очищать все окна."""
        mgr = ChatWindowManager(capacity=10)

        mgr.get_or_create("c1")
        mgr.get_or_create("c2")
        mgr.get_or_create("c3")

        count = mgr.clear_all()
        assert count == 3
        assert len(mgr.list_windows()) == 0


class TestEnvConfiguration:
    """Тесты env-конфигурации."""

    def test_default_env_values(self):
        """Должны быть корректные defaults."""
        assert CAPACITY == 100
        assert MESSAGE_CAP_PER_WINDOW == 50
        assert IDLE_EVICTION_SEC == 3600

    def test_capacity_env_override(self, monkeypatch):
        """Должно читать CHAT_WINDOW_CAPACITY из окружения."""
        monkeypatch.setenv("CHAT_WINDOW_CAPACITY", "50")
        # Нужно перезагрузить модуль, чтобы env был прочитан
        import importlib

        import src.core.chat_window_manager as cwm

        importlib.reload(cwm)
        assert cwm.CAPACITY == 50
        mgr = cwm.ChatWindowManager()
        assert mgr._capacity == 50

        # Восстанавливаем оригинальное значение для других тестов
        importlib.reload(cwm)

    def test_message_cap_env_override(self, monkeypatch):
        """Должно читать CHAT_WINDOW_MESSAGE_CAP из окружения."""
        monkeypatch.setenv("CHAT_WINDOW_MESSAGE_CAP", "10")
        import importlib

        import src.core.chat_window_manager as cwm

        importlib.reload(cwm)
        assert cwm.MESSAGE_CAP_PER_WINDOW == 10

        # Восстанавливаем
        importlib.reload(cwm)
