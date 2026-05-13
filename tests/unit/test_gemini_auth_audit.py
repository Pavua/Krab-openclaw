"""Wave 247: тесты Gemini auth audit module.

Проверяем:
- ADC detection (файл существует на диске → vertex_adc mode)
- Paid key block: paid present + flag=0 → mode НЕ ai_studio_paid
- Env override: KRAB_VERTEX_PROJECT/KRAB_GOOGLE_DIRECT_VERTEX_PREFERRED
- Suspicious detection: paid_key в env + flag=0 + guard=off → suspicious=True
- Mode resolution: vertex preferred but no ADC → fallback to free
- Mode logging: log_gemini_auth_setup() пишет structured event
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.core.gemini_auth_audit import (
    MODE_AI_STUDIO_FREE,
    MODE_AI_STUDIO_PAID,
    MODE_MISCONFIGURED,
    MODE_VERTEX_ADC,
    audit_gemini_auth,
    log_gemini_auth_setup,
)


@pytest.fixture
def _clean_env(monkeypatch):
    """Чистим все Gemini/Vertex env vars перед каждым тестом."""
    for name in (
        "GEMINI_PAID_KEY_ENABLED",
        "GEMINI_API_KEY_PAID",
        "GEMINI_API_KEY_FREE",
        "GEMINI_API_KEY",
        "KRAB_GOOGLE_DIRECT_VERTEX_PREFERRED",
        "KRAB_VERTEX_PROJECT",
        "KRAB_VERTEX_REGION",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "GOOGLE_CLOUD_PROJECT",
        "VERTEX_AI_PROJECT_ID",
        "VERTEX_AI_LOCATION",
        "KRAB_BLOCK_PAID_GEMINI_AI_STUDIO",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def _fake_adc(tmp_path: Path, monkeypatch):
    """Создаёт fake ADC creds.json и указывает GOOGLE_APPLICATION_CREDENTIALS на него."""
    adc_file = tmp_path / "application_default_credentials.json"
    adc_file.write_text(json.dumps({"refresh_token": "fake", "client_id": "fake"}))
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(adc_file))
    return adc_file


def test_vertex_adc_mode_when_adc_present(_clean_env, _fake_adc, monkeypatch):
    """ADC файл существует + vertex_preferred=1 (default) → mode=vertex_adc."""
    monkeypatch.setenv("KRAB_GOOGLE_DIRECT_VERTEX_PREFERRED", "1")
    monkeypatch.setenv("KRAB_VERTEX_PROJECT", "caramel-anvil-492816-t5")

    audit = audit_gemini_auth()

    assert audit.mode == MODE_VERTEX_ADC
    assert audit.vertex_preferred is True
    assert audit.adc_path_exists is True
    assert audit.vertex_project == "caramel-anvil-492816-t5"
    assert audit.suspicious is False


def test_paid_key_blocked_when_flag_disabled(_clean_env, monkeypatch):
    """GEMINI_API_KEY_PAID задан, но GEMINI_PAID_KEY_ENABLED=0 + no ADC → free mode."""
    monkeypatch.setenv("GEMINI_API_KEY_PAID", "AIzaSyA07paid_key_value")
    monkeypatch.setenv("GEMINI_API_KEY_FREE", "AIzaSyA07free_key_value")
    monkeypatch.setenv("GEMINI_PAID_KEY_ENABLED", "0")
    # vertex preferred=0 → не должен попасть в vertex_adc даже если бы ADC был
    monkeypatch.setenv("KRAB_GOOGLE_DIRECT_VERTEX_PREFERRED", "0")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/nonexistent/path.json")

    audit = audit_gemini_auth()

    # Не paid: флаг выключен — даже если paid key в env
    assert audit.mode != MODE_AI_STUDIO_PAID
    assert audit.mode == MODE_AI_STUDIO_FREE
    assert audit.paid_key_enabled_flag is False
    assert audit.paid_key_present_in_env is True


def test_env_override_vertex_project(_clean_env, _fake_adc, monkeypatch):
    """KRAB_VERTEX_PROJECT override честно подхватывается."""
    monkeypatch.setenv("KRAB_VERTEX_PROJECT", "my-custom-project-123")
    monkeypatch.setenv("KRAB_VERTEX_REGION", "us-central1")

    audit = audit_gemini_auth()

    assert audit.vertex_project == "my-custom-project-123"
    assert audit.vertex_location == "us-central1"


def test_suspicious_when_paid_key_present_guard_off(_clean_env, monkeypatch):
    """Paid key в env + flag=0 + guard=off → suspicious=True (нет защиты от утечки)."""
    monkeypatch.setenv("GEMINI_API_KEY_PAID", "AIzaSyA07paid_key_value")
    monkeypatch.setenv("GEMINI_API_KEY_FREE", "AIzaSyA07free_key_value")
    monkeypatch.setenv("GEMINI_PAID_KEY_ENABLED", "0")
    monkeypatch.setenv("KRAB_BLOCK_PAID_GEMINI_AI_STUDIO", "0")  # guard OFF — опасно
    monkeypatch.setenv("KRAB_GOOGLE_DIRECT_VERTEX_PREFERRED", "0")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/nonexistent.json")

    audit = audit_gemini_auth()

    assert audit.suspicious is True
    assert audit.guard_mode == "off"


def test_misconfigured_when_no_keys_no_adc(_clean_env, monkeypatch):
    """Нет ADC, нет free/paid key → mode=misconfigured."""
    monkeypatch.setenv("KRAB_GOOGLE_DIRECT_VERTEX_PREFERRED", "0")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/definitely/nonexistent.json")

    audit = audit_gemini_auth()

    assert audit.mode == MODE_MISCONFIGURED
    assert audit.adc_path_exists is False
    assert audit.paid_key_present_in_env is False


def test_log_gemini_auth_setup_returns_audit(_clean_env, _fake_adc, monkeypatch):
    """log_gemini_auth_setup() возвращает audit snapshot и не падает."""
    monkeypatch.setenv("KRAB_GOOGLE_DIRECT_VERTEX_PREFERRED", "1")

    audit = log_gemini_auth_setup()

    assert audit.mode == MODE_VERTEX_ADC
    # Сериализация работает (для /api/model/status)
    d = audit.to_dict()
    assert d["mode"] == MODE_VERTEX_ADC
    assert "vertex_project" in d
    assert "guard_mode" in d
