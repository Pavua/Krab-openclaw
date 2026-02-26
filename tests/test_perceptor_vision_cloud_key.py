# -*- coding: utf-8 -*-
"""Тесты выбора cloud-ключа для vision и нормализации ошибок."""

from src.modules.perceptor import Perceptor


class _FakeOpenClawClient:
    def __init__(self):
        self.active_tier = "paid"
        self.gemini_tiers = {"free": "free-key", "paid": "paid-key"}

    def _resolve_provider_api_key(self, provider: str):
        if provider == "google":
            return "paid-key", "env:GEMINI_API_KEY_PAID"
        return "", "missing"


class _FakeRouter:
    def __init__(self):
        self.openclaw_client = _FakeOpenClawClient()
        self.gemini_key = "legacy-free-key"


def test_resolve_cloud_vision_api_key_prefers_openclaw_active_tier():
    perceptor = Perceptor(config={})
    key, source = perceptor._resolve_cloud_vision_api_key(_FakeRouter())
    assert key == "paid-key"
    assert "PAID" in source.upper() or "paid" in source


def test_normalize_vision_error_for_user_hides_raw_dump():
    perceptor = Perceptor(config={})
    raw = "401 UNAUTHENTICATED. {'error': {'message': 'API keys are not supported by this API'}}"
    msg = perceptor._normalize_vision_error_for_user(raw)
    assert "401" in msg
    assert "UNAUTHENTICATED" in msg
    assert "API keys are not supported" not in msg
