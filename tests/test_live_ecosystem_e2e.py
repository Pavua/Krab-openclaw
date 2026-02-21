# -*- coding: utf-8 -*-
"""Тесты live_ecosystem_e2e: базовая нормализация URL."""

from scripts.live_ecosystem_e2e import _normalize_lm_models_url


def test_normalize_lm_models_url_with_plain_base() -> None:
    assert _normalize_lm_models_url("http://127.0.0.1:1234") == "http://127.0.0.1:1234/v1/models"


def test_normalize_lm_models_url_with_v1_base() -> None:
    assert _normalize_lm_models_url("http://127.0.0.1:1234/v1") == "http://127.0.0.1:1234/v1/models"
