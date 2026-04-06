# -*- coding: utf-8 -*-
"""Тесты фильтрации спама/рассылок — _is_notification_sender и _is_bulk_sender."""
import pytest
from types import SimpleNamespace


def _make_user(**kwargs):
    """Создаёт mock-объект пользователя для тестов фильтрации."""
    defaults = {
        "id": 12345,
        "username": None,
        "phone": None,
        "first_name": None,
        "is_verified": False,
        "is_scam": False,
        "is_fake": False,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


# Импортируем функции напрямую, если возможно
try:
    from src.core.spam_filter import is_notification_sender, is_bulk_sender
except ImportError:
    pytest.skip("src.core.spam_filter not available", allow_module_level=True)


class TestIsNotificationSender:
    """Shortcode detection (≤5 digit senders)."""

    def test_shortcode_username(self):
        assert is_notification_sender(_make_user(username="12345"))

    def test_shortcode_phone(self):
        assert is_notification_sender(_make_user(phone="+1234"))

    def test_normal_username(self):
        assert not is_notification_sender(_make_user(username="john_doe"))

    def test_long_phone(self):
        assert not is_notification_sender(_make_user(phone="+34612345678"))

    def test_empty_user(self):
        assert not is_notification_sender(_make_user())

    def test_six_digit_not_shortcode(self):
        assert not is_notification_sender(_make_user(username="123456"))

    def test_shortcode_with_plus(self):
        assert is_notification_sender(_make_user(phone="+999"))

    def test_shortcode_with_spaces(self):
        assert is_notification_sender(_make_user(phone="1 2 3 4"))


class TestIsBulkSender:
    """Фильтрация массовых рассылок, сервисных аккаунтов, OTP."""

    def test_verified_no_username(self):
        assert is_bulk_sender(_make_user(is_verified=True, username=None, first_name="Banco Santander"))

    def test_verified_with_username(self):
        # Verified + username = скорее реальный пользователь
        assert not is_bulk_sender(_make_user(is_verified=True, username="real_user"))

    def test_otp_first_name(self):
        assert is_bulk_sender(_make_user(first_name="Verification Code"))

    def test_otp_first_name_otp(self):
        assert is_bulk_sender(_make_user(first_name="OTP Service"))

    def test_scam_flag(self):
        assert is_bulk_sender(_make_user(is_scam=True))

    def test_fake_flag(self):
        assert is_bulk_sender(_make_user(is_fake=True))

    def test_normal_user(self):
        assert not is_bulk_sender(_make_user(username="alice", first_name="Alice"))

    def test_delivery_pattern(self):
        assert is_bulk_sender(_make_user(first_name="SMS Notification"))
