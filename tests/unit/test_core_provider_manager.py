# -*- coding: utf-8 -*-
"""
Тесты для src/core/provider_manager.py.

Покрывает: ProviderManager.set_provider/set_model, resolve_config_for_provider,
report_usage, to_api_dict, format_status, set_thinking_depth, set_fallback_chain,
QuotaInfo.to_dict, is_provider_available, get_fallback_chain, edge-cases.
"""
from __future__ import annotations

import json
import os
from unittest.mock import patch

import pytest

from src.core.provider_manager import (
    PROVIDER_MODELS,
    ProviderManager,
    ProviderState,
    ProviderType,
    QuotaInfo,
    ThinkingDepth,
)

# ---------------------------------------------------------------------------
# Фикстура: менеджер с изолированным файлом состояния
# ---------------------------------------------------------------------------

@pytest.fixture
def pm(tmp_path):
    """ProviderManager с временным state-файлом, без чтения ~/.openclaw."""
    state_file = str(tmp_path / "krab_provider_state.json")
    mgr = ProviderManager.__new__(ProviderManager)
    mgr._STATE_FILE = state_file  # noqa: SLF001 — подменяем путь до __init__
    mgr._state = ProviderState()  # чистое состояние
    return mgr


# ---------------------------------------------------------------------------
# QuotaInfo.to_dict
# ---------------------------------------------------------------------------

class TestQuotaInfoToDict:
    """Сериализация квот."""

    def test_zero_quota(self):
        q = QuotaInfo()
        d = q.to_dict()
        assert d["used"] == 0
        assert d["limit"] == 0
        assert d["requests"] == 0
        assert d["percentage"] == 0.0
        assert "∞" in d["label"]

    def test_partial_usage(self):
        q = QuotaInfo(used_tokens=500, limit_tokens=1000, requests_count=5)
        d = q.to_dict()
        assert d["used"] == 500
        assert d["limit"] == 1000
        assert d["percentage"] == 50.0
        assert "500/1000" in d["label"]

    def test_over_limit_capped_at_100(self):
        """Использование сверх лимита не даёт percentage > 100."""
        q = QuotaInfo(used_tokens=1500, limit_tokens=1000)
        d = q.to_dict()
        assert d["percentage"] == 100.0


# ---------------------------------------------------------------------------
# set_provider / set_model
# ---------------------------------------------------------------------------

class TestSetProvider:
    """Переключение провайдеров и моделей."""

    def test_set_provider_changes_active(self, pm):
        pm.set_provider(ProviderType.GEMINI_API)
        assert pm.active_provider == ProviderType.GEMINI_API

    def test_set_provider_with_model(self, pm):
        pm.set_provider(ProviderType.OPENAI_API, model_id="openai/gpt-4o")
        assert pm.active_provider == ProviderType.OPENAI_API
        assert pm._state.model_id == "openai/gpt-4o"

    def test_set_model_updates_state(self, pm):
        pm.set_model("gemini-2.5-flash")
        assert pm._state.model_id == "gemini-2.5-flash"

    def test_set_model_empty_string(self, pm):
        """Пустая строка допустима — вернётся дефолт провайдера."""
        pm.set_model("")
        assert pm._state.model_id == ""

    def test_active_model_id_falls_back_to_default(self, pm):
        """Если model_id пустой — возвращается default-модель провайдера."""
        pm.set_provider(ProviderType.GEMINI_API)
        pm._state.model_id = ""
        default_id = pm.get_default_model_for_provider(ProviderType.GEMINI_API)
        assert pm.active_model_id == default_id
        assert default_id != ""

    def test_set_vision_model(self, pm):
        pm.set_vision_model("my-vision-model")
        assert pm._state.vision_model_id == "my-vision-model"

    def test_active_vision_model_auto_from_provider(self, pm):
        """Если vision_model_id не задан — берётся первая vision-модель провайдера."""
        pm.set_provider(ProviderType.GEMINI_API)
        pm._state.vision_model_id = ""
        vision_id = pm.active_vision_model_id
        # GEMINI_API имеет модели с vision=True
        assert vision_id != ""


# ---------------------------------------------------------------------------
# set_thinking_depth
# ---------------------------------------------------------------------------

class TestSetThinkingDepth:
    """Установка глубины reasoning."""

    def test_set_all_depths(self, pm):
        for depth in ThinkingDepth:
            pm.set_thinking_depth(depth)
            assert pm.thinking_depth == depth

    def test_thinking_params_off(self, pm):
        pm.set_thinking_depth(ThinkingDepth.OFF)
        p = pm.thinking_params
        assert p["thinking"] is False
        assert p["budget_tokens"] == 0

    def test_thinking_params_high(self, pm):
        pm.set_thinking_depth(ThinkingDepth.HIGH)
        p = pm.thinking_params
        assert p["thinking"] is True
        assert p["budget_tokens"] == 32768


# ---------------------------------------------------------------------------
# set_fallback_chain
# ---------------------------------------------------------------------------

class TestSetFallbackChain:
    """Управление fallback-цепочками."""

    def test_set_fallback_chain_stored(self, pm):
        chain = [ProviderType.GEMINI_API, ProviderType.LM_STUDIO]
        pm.set_fallback_chain(chain)
        assert pm._state.fallback.chain == chain

    def test_set_fallback_empty_chain(self, pm):
        pm.set_fallback_chain([])
        assert pm._state.fallback.chain == []

    def test_get_fallback_chain_filters_unavailable(self, pm):
        """get_fallback_chain возвращает только доступные провайдеры."""
        pm.set_fallback_chain([ProviderType.GEMINI_API, ProviderType.LM_STUDIO])
        # Мокаем: GEMINI_API недоступен, LM_STUDIO всегда доступен
        with patch.object(pm, "get_available_providers", return_value=[ProviderType.LM_STUDIO]):
            effective = pm.get_fallback_chain()
        assert ProviderType.LM_STUDIO in effective
        assert ProviderType.GEMINI_API not in effective

    def test_get_fallback_chain_all_available(self, pm):
        pm.set_fallback_chain([ProviderType.GEMINI_API, ProviderType.LM_STUDIO])
        with patch.object(pm, "get_available_providers",
                          return_value=[ProviderType.GEMINI_API, ProviderType.LM_STUDIO]):
            effective = pm.get_fallback_chain()
        assert effective == [ProviderType.GEMINI_API, ProviderType.LM_STUDIO]


# ---------------------------------------------------------------------------
# report_usage
# ---------------------------------------------------------------------------

class TestReportUsage:
    """Учёт токенов."""

    def test_report_usage_increments(self, pm):
        pm.report_usage(ProviderType.GEMINI_API, 100)
        q = pm._state.quotas[ProviderType.GEMINI_API]
        assert q.used_tokens == 100
        assert q.requests_count == 1

    def test_report_usage_accumulates(self, pm):
        pm.report_usage(ProviderType.GEMINI_API, 100)
        pm.report_usage(ProviderType.GEMINI_API, 200)
        q = pm._state.quotas[ProviderType.GEMINI_API]
        assert q.used_tokens == 300
        assert q.requests_count == 2

    def test_report_usage_auto_noop(self, pm):
        """AUTO-провайдер игнорируется."""
        pm.report_usage(ProviderType.AUTO, 9999)
        # Нет ключа AUTO в quotas — не должно упасть
        assert ProviderType.AUTO not in pm._state.quotas


# ---------------------------------------------------------------------------
# resolve_config_for_provider
# ---------------------------------------------------------------------------

class TestResolveConfigForProvider:
    """Формирование конфига для openclaw_client."""

    def test_lm_studio_force_cloud_false(self, pm):
        cfg = pm.resolve_config_for_provider(ProviderType.LM_STUDIO)
        assert cfg["force_cloud"] is False

    def test_gemini_api_force_cloud_true(self, pm):
        cfg = pm.resolve_config_for_provider(ProviderType.GEMINI_API)
        assert cfg["force_cloud"] is True

    def test_provider_type_in_config(self, pm):
        cfg = pm.resolve_config_for_provider(ProviderType.OPENAI_API)
        assert cfg["provider_type"] == ProviderType.OPENAI_API.value

    def test_model_id_is_default_for_provider(self, pm):
        cfg = pm.resolve_config_for_provider(ProviderType.GEMINI_API)
        expected = pm.get_default_model_for_provider(ProviderType.GEMINI_API)
        assert cfg["model_id"] == expected

    def test_thinking_keys_present(self, pm):
        cfg = pm.resolve_config_for_provider(ProviderType.GEMINI_API)
        assert "thinking_enabled" in cfg
        assert "thinking_budget_tokens" in cfg

    def test_max_output_tokens_included_when_set(self, pm):
        pm.set_max_output_tokens(4096)
        cfg = pm.resolve_config_for_provider(ProviderType.GEMINI_API)
        assert cfg.get("max_output_tokens") == 4096

    def test_max_output_tokens_omitted_when_zero(self, pm):
        pm.set_max_output_tokens(0)
        cfg = pm.resolve_config_for_provider(ProviderType.GEMINI_API)
        assert "max_output_tokens" not in cfg

    def test_temperature_included_when_nonneg(self, pm):
        pm.set_temperature(0.7)
        cfg = pm.resolve_config_for_provider(ProviderType.GEMINI_API)
        assert abs(cfg["temperature"] - 0.7) < 1e-6

    def test_temperature_omitted_when_negative(self, pm):
        pm._state.temperature = -1.0
        cfg = pm.resolve_config_for_provider(ProviderType.GEMINI_API)
        assert "temperature" not in cfg


# ---------------------------------------------------------------------------
# to_api_dict
# ---------------------------------------------------------------------------

class TestToApiDict:
    """REST-сериализация состояния."""

    def test_returns_all_required_keys(self, pm):
        d = pm.to_api_dict()
        assert "active" in d
        assert "providers" in d
        assert "fallback" in d
        assert "thinking_options" in d

    def test_active_section_fields(self, pm):
        pm.set_provider(ProviderType.GEMINI_API, model_id="gemini-2.5-flash")
        d = pm.to_api_dict()
        active = d["active"]
        assert active["provider"] == ProviderType.GEMINI_API.value
        assert "model_id" in active
        assert "thinking_depth" in active

    def test_providers_count(self, pm):
        """Все типы кроме AUTO присутствуют в списке."""
        d = pm.to_api_dict()
        non_auto = [p for p in ProviderType if p != ProviderType.AUTO]
        assert len(d["providers"]) == len(non_auto)

    def test_fallback_effective_chain_present(self, pm):
        d = pm.to_api_dict()
        assert "effective_chain" in d["fallback"]

    def test_thinking_options_all_depths(self, pm):
        d = pm.to_api_dict()
        option_ids = {o["id"] for o in d["thinking_options"]}
        for depth in ThinkingDepth:
            assert depth.value in option_ids

    def test_active_quota_empty_for_auto_provider(self, pm):
        """Для AUTO-провайдера quota в active — пустой dict."""
        pm.set_provider(ProviderType.AUTO)
        d = pm.to_api_dict()
        assert d["active"]["quota"] == {}


# ---------------------------------------------------------------------------
# format_status
# ---------------------------------------------------------------------------

class TestFormatStatus:
    """Telegram-форматирование статуса."""

    def test_contains_provider_display(self, pm):
        pm.set_provider(ProviderType.LM_STUDIO)
        status = pm.format_status()
        assert "LM Studio" in status or "lm_studio" in status.lower()

    def test_contains_thinking_section(self, pm):
        status = pm.format_status()
        # Строка включает ключевые секции
        assert "Thinking" in status or "thinking" in status.lower()

    def test_fallback_section_present(self, pm):
        status = pm.format_status()
        assert "Fallback" in status or "fallback" in status.lower()


# ---------------------------------------------------------------------------
# is_provider_available
# ---------------------------------------------------------------------------

class TestIsProviderAvailable:
    """Проверка доступности провайдеров."""

    def test_lm_studio_always_available(self, pm):
        assert pm.is_provider_available(ProviderType.LM_STUDIO) is True

    def test_auto_always_available(self, pm):
        assert pm.is_provider_available(ProviderType.AUTO) is True

    def test_gemini_api_available_with_key(self, pm):
        with patch.dict(os.environ, {"GEMINI_API_KEY": "testkey123"}):
            assert pm.is_provider_available(ProviderType.GEMINI_API) is True

    def test_gemini_api_unavailable_without_key(self, pm):
        env = {k: v for k, v in os.environ.items()
               if k not in ("GEMINI_API_KEY", "GOOGLE_API_KEY")}
        with patch.dict(os.environ, env, clear=True):
            assert pm.is_provider_available(ProviderType.GEMINI_API) is False

    def test_openai_api_available_with_key(self, pm):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test123"}):
            assert pm.is_provider_available(ProviderType.OPENAI_API) is True

    def test_gemini_oauth_unavailable_without_auth_file(self, pm, tmp_path):
        """Без auth-profiles.json gemini_oauth недоступен."""
        with patch("src.core.provider_manager.os.path.exists", return_value=False):
            assert pm.is_provider_available(ProviderType.GEMINI_OAUTH) is False


# ---------------------------------------------------------------------------
# Персистентность (save / load round-trip)
# ---------------------------------------------------------------------------

class TestPersistence:
    """Сохранение и загрузка состояния из JSON."""

    def test_save_load_roundtrip(self, tmp_path):
        state_file = str(tmp_path / "state.json")

        # Создаём первый менеджер, меняем состояние
        m1 = ProviderManager.__new__(ProviderManager)
        m1._STATE_FILE = state_file
        m1._state = ProviderState()
        m1.set_provider(ProviderType.OPENAI_API, model_id="openai/gpt-4o")
        m1.set_thinking_depth(ThinkingDepth.HIGH)
        m1.set_temperature(0.9)

        # Создаём второй менеджер, он загружает из того же файла
        m2 = ProviderManager.__new__(ProviderManager)
        m2._STATE_FILE = state_file
        m2._state = ProviderState()
        m2._load_state()

        assert m2._state.provider == ProviderType.OPENAI_API
        assert m2._state.model_id == "openai/gpt-4o"
        assert m2._state.thinking_depth == ThinkingDepth.HIGH
        assert abs(m2._state.temperature - 0.9) < 1e-6

    def test_load_corrupted_json_does_not_crash(self, tmp_path):
        state_file = str(tmp_path / "state.json")
        with open(state_file, "w") as f:
            f.write("{ not valid json")
        m = ProviderManager.__new__(ProviderManager)
        m._STATE_FILE = state_file
        m._state = ProviderState()
        m._load_state()  # Не должно бросить исключение
        assert m._state.provider == ProviderType.AUTO  # дефолт сохранён

    def test_load_unknown_provider_value_ignored(self, tmp_path):
        """Неизвестный provider-тип в JSON не ломает загрузку."""
        state_file = str(tmp_path / "state.json")
        data = {"provider": "nonexistent_provider_xyz", "model_id": ""}
        with open(state_file, "w") as f:
            json.dump(data, f)
        m = ProviderManager.__new__(ProviderManager)
        m._STATE_FILE = state_file
        m._state = ProviderState()
        m._load_state()
        # Дефолт AUTO сохраняется при невалидном значении
        assert m._state.provider == ProviderType.AUTO


# ---------------------------------------------------------------------------
# get_default_model_for_provider
# ---------------------------------------------------------------------------

class TestGetDefaultModel:
    """Получение default-модели провайдера."""

    def test_gemini_api_has_default(self, pm):
        default = pm.get_default_model_for_provider(ProviderType.GEMINI_API)
        assert default != ""

    def test_lm_studio_has_default(self, pm):
        default = pm.get_default_model_for_provider(ProviderType.LM_STUDIO)
        assert default != ""

    def test_openai_oauth_default_is_flagship(self, pm):
        """Первая default-модель OpenAI OAuth — флагман."""
        default = pm.get_default_model_for_provider(ProviderType.OPENAI_OAUTH)
        models = PROVIDER_MODELS[ProviderType.OPENAI_OAUTH]
        flagships = [m["id"] for m in models if m.get("default")]
        assert default in flagships

    def test_auto_provider_returns_empty(self, pm):
        """AUTO не имеет моделей — возвращается пустая строка."""
        default = pm.get_default_model_for_provider(ProviderType.AUTO)
        assert default == ""


# ---------------------------------------------------------------------------
# set_temperature / set_max_output_tokens
# ---------------------------------------------------------------------------

class TestTemperatureAndTokens:
    """Граничные значения temperature и max_output_tokens."""

    def test_temperature_clamped_min(self, pm):
        pm.set_temperature(-5.0)
        assert pm._state.temperature == -1.0

    def test_temperature_clamped_max(self, pm):
        pm.set_temperature(99.0)
        assert pm._state.temperature == 2.0

    def test_temperature_valid_range(self, pm):
        pm.set_temperature(1.2)
        assert abs(pm._state.temperature - 1.2) < 1e-6

    def test_max_output_tokens_clamped_min(self, pm):
        pm.set_max_output_tokens(-100)
        assert pm._state.max_output_tokens == 0

    def test_max_output_tokens_valid(self, pm):
        pm.set_max_output_tokens(8192)
        assert pm._state.max_output_tokens == 8192
