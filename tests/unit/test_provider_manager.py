# -*- coding: utf-8 -*-
"""
Юнит-тесты ``src/core/provider_manager.py`` — ProviderManager, ProviderState,
QuotaInfo, FallbackConfig, перечисления ProviderType / ThinkingDepth.

Что покрыто:

1. **Dataclass-ы:** QuotaInfo.to_dict, FallbackConfig defaults, ProviderState defaults.
2. **set_provider / set_model / set_thinking_depth** — мутация + персистентность.
3. **resolve_config_for_provider** — корректность генерируемого runtime-конфига.
4. **to_api_dict** — структура ответа REST API.
5. **format_status** — не падает, содержит ключевые строки.
6. **report_usage** — накопление токенов, игнор AUTO.
7. **get_fallback_chain / set_fallback_chain** — фильтрация по доступности.
8. **Персистентность** — save → reload, повреждённый JSON.
9. **Edge cases** — пустая модель, temperature clamping, max_output_tokens.
10. **Синглтон** — модульный экземпляр существует.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.core.provider_manager import (
    PROVIDER_DISPLAY_NAMES,
    PROVIDER_MODELS,
    THINKING_DEPTH_DISPLAY,
    THINKING_DEPTH_PARAMS,
    FallbackConfig,
    ProviderManager,
    ProviderState,
    ProviderType,
    QuotaInfo,
    ThinkingDepth,
    provider_manager,
)

# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_manager(tmp_path: Path) -> ProviderManager:
    """Создаёт ProviderManager с изолированным state-файлом."""
    pm = ProviderManager.__new__(ProviderManager)
    pm._state = ProviderState()
    pm._STATE_FILE = str(tmp_path / "provider_state.json")
    return pm


# ═════════════════════════════════════════════════════════════════════════════
# 1. Перечисления и константы
# ═════════════════════════════════════════════════════════════════════════════


class TestEnumsAndConstants:
    def test_provider_type_values(self) -> None:
        """Все ProviderType содержат ожидаемые строки."""
        assert ProviderType.AUTO.value == "auto"
        assert ProviderType.GEMINI_OAUTH.value == "gemini_oauth"
        assert ProviderType.LM_STUDIO.value == "lm_studio"

    def test_thinking_depth_values(self) -> None:
        assert ThinkingDepth.OFF.value == "off"
        assert ThinkingDepth.HIGH.value == "high"
        assert ThinkingDepth.AUTO.value == "auto"

    def test_display_names_cover_all_providers(self) -> None:
        """Каждый ProviderType имеет display name."""
        for pt in ProviderType:
            assert pt in PROVIDER_DISPLAY_NAMES

    def test_thinking_display_cover_all_depths(self) -> None:
        for d in ThinkingDepth:
            assert d in THINKING_DEPTH_DISPLAY

    def test_thinking_params_cover_all_depths(self) -> None:
        for d in ThinkingDepth:
            assert d in THINKING_DEPTH_PARAMS
            params = THINKING_DEPTH_PARAMS[d]
            assert "thinking" in params
            assert "budget_tokens" in params

    def test_provider_models_catalog_not_empty(self) -> None:
        """Каталог моделей содержит записи для каждого не-AUTO провайдера."""
        for pt in ProviderType:
            if pt == ProviderType.AUTO:
                continue
            models = PROVIDER_MODELS.get(pt, [])
            assert len(models) > 0, f"Нет моделей для {pt.value}"

    def test_get_default_model_always_returns_string(self) -> None:
        """get_default_model_for_provider возвращает строку для любого провайдера."""
        pm = ProviderManager.__new__(ProviderManager)
        pm._state = ProviderState()
        pm._STATE_FILE = "/dev/null"
        for pt in ProviderType:
            if pt == ProviderType.AUTO:
                continue
            result = pm.get_default_model_for_provider(pt)
            # Даже если нет default=True, fallback на первую модель
            assert isinstance(result, str)
            assert result != "", f"Пустая default модель для {pt.value}"


# ═════════════════════════════════════════════════════════════════════════════
# 2. QuotaInfo dataclass
# ═════════════════════════════════════════════════════════════════════════════


class TestQuotaInfo:
    def test_to_dict_unlimited(self) -> None:
        """limit=0 → percentage=0, label содержит ∞."""
        q = QuotaInfo(used_tokens=500, limit_tokens=0, requests_count=3)
        d = q.to_dict()
        assert d["used"] == 500
        assert d["limit"] == 0
        assert d["requests"] == 3
        assert d["percentage"] == 0.0
        assert "∞" in d["label"]

    def test_to_dict_with_limit(self) -> None:
        q = QuotaInfo(used_tokens=750, limit_tokens=1000, requests_count=10)
        d = q.to_dict()
        assert d["percentage"] == 75.0
        assert d["label"] == "750/1000"

    def test_to_dict_over_limit_capped(self) -> None:
        """used > limit — percentage не превышает 100."""
        q = QuotaInfo(used_tokens=2000, limit_tokens=1000)
        d = q.to_dict()
        assert d["percentage"] == 100.0

    def test_default_quota_info(self) -> None:
        q = QuotaInfo()
        assert q.used_tokens == 0
        assert q.requests_count == 0


# ═════════════════════════════════════════════════════════════════════════════
# 3. FallbackConfig / ProviderState defaults
# ═════════════════════════════════════════════════════════════════════════════


class TestDataclassDefaults:
    def test_fallback_config_defaults(self) -> None:
        fc = FallbackConfig()
        assert len(fc.chain) == 4
        assert fc.max_attempts == 3
        assert fc.lm_studio_as_last_resort is False

    def test_provider_state_defaults(self) -> None:
        ps = ProviderState()
        assert ps.provider == ProviderType.AUTO
        assert ps.model_id == ""
        assert ps.thinking_depth == ThinkingDepth.AUTO
        assert ps.temperature == -1.0
        assert ps.max_output_tokens == 0

    def test_provider_state_quotas_exclude_auto(self) -> None:
        """Квоты создаются для всех типов кроме AUTO."""
        ps = ProviderState()
        assert ProviderType.AUTO not in ps.quotas
        for pt in ProviderType:
            if pt != ProviderType.AUTO:
                assert pt in ps.quotas


# ═════════════════════════════════════════════════════════════════════════════
# 4. ProviderManager: set / get
# ═════════════════════════════════════════════════════════════════════════════


class TestProviderManagerSetGet:
    def test_set_provider(self, tmp_path: Path) -> None:
        pm = _make_manager(tmp_path)
        pm.set_provider(ProviderType.GEMINI_API, "gemini-2.5-flash")
        assert pm.active_provider == ProviderType.GEMINI_API
        assert pm.active_model_id == "gemini-2.5-flash"

    def test_set_model(self, tmp_path: Path) -> None:
        pm = _make_manager(tmp_path)
        pm.set_model("openai/gpt-4o")
        assert pm._state.model_id == "openai/gpt-4o"

    def test_set_thinking_depth(self, tmp_path: Path) -> None:
        pm = _make_manager(tmp_path)
        pm.set_thinking_depth(ThinkingDepth.HIGH)
        assert pm.thinking_depth == ThinkingDepth.HIGH
        assert pm.thinking_params == THINKING_DEPTH_PARAMS[ThinkingDepth.HIGH]

    def test_set_temperature_clamped(self, tmp_path: Path) -> None:
        """Temperature клампится в диапазон [-1, 2]."""
        pm = _make_manager(tmp_path)
        pm.set_temperature(5.0)
        assert pm._state.temperature == 2.0
        pm.set_temperature(-99.0)
        assert pm._state.temperature == -1.0

    def test_set_max_output_tokens_non_negative(self, tmp_path: Path) -> None:
        pm = _make_manager(tmp_path)
        pm.set_max_output_tokens(-100)
        assert pm._state.max_output_tokens == 0
        pm.set_max_output_tokens(4096)
        assert pm._state.max_output_tokens == 4096

    def test_active_model_id_returns_default_when_empty(self, tmp_path: Path) -> None:
        """Если model_id пустой, active_model_id отдаёт default модель провайдера."""
        pm = _make_manager(tmp_path)
        pm.set_provider(ProviderType.GEMINI_API)
        default = pm.get_default_model_for_provider(ProviderType.GEMINI_API)
        assert pm.active_model_id == default
        assert default != ""

    def test_active_vision_model_auto(self, tmp_path: Path) -> None:
        """Без явной vision_model выбирается первая vision-модель провайдера."""
        pm = _make_manager(tmp_path)
        pm.set_provider(ProviderType.GEMINI_API)
        vision = pm.active_vision_model_id
        # Должна быть vision-модель из каталога
        models = PROVIDER_MODELS[ProviderType.GEMINI_API]
        vision_ids = [m["id"] for m in models if m.get("vision")]
        assert vision in vision_ids

    def test_set_vision_model_explicit(self, tmp_path: Path) -> None:
        pm = _make_manager(tmp_path)
        pm.set_vision_model("custom-vision-model")
        assert pm.active_vision_model_id == "custom-vision-model"

    def test_get_models_for_provider(self, tmp_path: Path) -> None:
        pm = _make_manager(tmp_path)
        models = pm.get_models_for_provider(ProviderType.LM_STUDIO)
        assert isinstance(models, list)
        assert len(models) > 0

    def test_get_models_for_unknown_provider_returns_empty(self, tmp_path: Path) -> None:
        """AUTO не имеет своего каталога моделей."""
        pm = _make_manager(tmp_path)
        assert pm.get_models_for_provider(ProviderType.AUTO) == []


# ═════════════════════════════════════════════════════════════════════════════
# 5. resolve_config_for_provider
# ═════════════════════════════════════════════════════════════════════════════


class TestResolveConfig:
    def test_cloud_provider_force_cloud_true(self, tmp_path: Path) -> None:
        pm = _make_manager(tmp_path)
        cfg = pm.resolve_config_for_provider(ProviderType.GEMINI_API)
        assert cfg["force_cloud"] is True
        assert cfg["provider_type"] == "gemini_api"

    def test_lm_studio_force_cloud_false(self, tmp_path: Path) -> None:
        pm = _make_manager(tmp_path)
        cfg = pm.resolve_config_for_provider(ProviderType.LM_STUDIO)
        assert cfg["force_cloud"] is False

    def test_resolve_includes_thinking_params(self, tmp_path: Path) -> None:
        pm = _make_manager(tmp_path)
        pm.set_thinking_depth(ThinkingDepth.HIGH)
        cfg = pm.resolve_config_for_provider(ProviderType.GEMINI_API)
        assert cfg["thinking_enabled"] is True
        assert cfg["thinking_budget_tokens"] == 32768

    def test_resolve_includes_temperature_when_set(self, tmp_path: Path) -> None:
        pm = _make_manager(tmp_path)
        pm.set_temperature(0.7)
        cfg = pm.resolve_config_for_provider(ProviderType.GEMINI_API)
        assert cfg["temperature"] == 0.7

    def test_resolve_excludes_temperature_when_default(self, tmp_path: Path) -> None:
        """temperature=-1 (дефолт) — не включается в конфиг."""
        pm = _make_manager(tmp_path)
        cfg = pm.resolve_config_for_provider(ProviderType.GEMINI_API)
        assert "temperature" not in cfg

    def test_resolve_includes_max_output_tokens_when_set(self, tmp_path: Path) -> None:
        pm = _make_manager(tmp_path)
        pm.set_max_output_tokens(8192)
        cfg = pm.resolve_config_for_provider(ProviderType.GEMINI_API)
        assert cfg["max_output_tokens"] == 8192

    def test_resolve_excludes_max_output_tokens_when_zero(self, tmp_path: Path) -> None:
        pm = _make_manager(tmp_path)
        cfg = pm.resolve_config_for_provider(ProviderType.GEMINI_API)
        assert "max_output_tokens" not in cfg


# ═════════════════════════════════════════════════════════════════════════════
# 6. report_usage
# ═════════════════════════════════════════════════════════════════════════════


class TestReportUsage:
    def test_report_usage_accumulates(self, tmp_path: Path) -> None:
        pm = _make_manager(tmp_path)
        pm.report_usage(ProviderType.GEMINI_API, 100)
        pm.report_usage(ProviderType.GEMINI_API, 250)
        q = pm._state.quotas[ProviderType.GEMINI_API]
        assert q.used_tokens == 350
        assert q.requests_count == 2

    def test_report_usage_auto_ignored(self, tmp_path: Path) -> None:
        """AUTO провайдер не трекается — noop."""
        pm = _make_manager(tmp_path)
        pm.report_usage(ProviderType.AUTO, 999)
        # Не должно упасть, квоты AUTO нет.

    def test_report_usage_persisted(self, tmp_path: Path) -> None:
        """После report_usage state-файл содержит обновлённые квоты."""
        pm = _make_manager(tmp_path)
        pm.report_usage(ProviderType.LM_STUDIO, 500)
        state_file = Path(pm._STATE_FILE)
        assert state_file.exists()
        data = json.loads(state_file.read_text())
        assert data["quotas"]["lm_studio"]["used_tokens"] == 500


# ═════════════════════════════════════════════════════════════════════════════
# 7. Fallback chain
# ═════════════════════════════════════════════════════════════════════════════


class TestFallbackChain:
    def test_set_fallback_chain(self, tmp_path: Path) -> None:
        pm = _make_manager(tmp_path)
        new_chain = [ProviderType.LM_STUDIO, ProviderType.GEMINI_API]
        pm.set_fallback_chain(new_chain)
        assert pm._state.fallback.chain == new_chain

    def test_get_fallback_chain_filters_unavailable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """get_fallback_chain убирает недоступных провайдеров из цепочки."""
        pm = _make_manager(tmp_path)
        pm.set_fallback_chain(
            [
                ProviderType.GEMINI_OAUTH,
                ProviderType.GEMINI_API,
                ProviderType.LM_STUDIO,
            ]
        )
        # GEMINI_OAUTH и GEMINI_API недоступны без ключей/файлов.
        # LM_STUDIO всегда доступен.
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        chain = pm.get_fallback_chain()
        assert ProviderType.LM_STUDIO in chain

    def test_lm_studio_last_resort_flag(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        pm = _make_manager(tmp_path)
        pm.set_lm_studio_last_resort(True)
        assert pm._state.fallback.lm_studio_as_last_resort is True


# ═════════════════════════════════════════════════════════════════════════════
# 8. is_provider_available
# ═════════════════════════════════════════════════════════════════════════════


class TestIsProviderAvailable:
    def test_lm_studio_always_available(self, tmp_path: Path) -> None:
        pm = _make_manager(tmp_path)
        assert pm.is_provider_available(ProviderType.LM_STUDIO) is True

    def test_auto_always_available(self, tmp_path: Path) -> None:
        pm = _make_manager(tmp_path)
        assert pm.is_provider_available(ProviderType.AUTO) is True

    def test_gemini_api_available_with_key(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        pm = _make_manager(tmp_path)
        monkeypatch.setenv("GEMINI_API_KEY", "test-key-123")
        assert pm.is_provider_available(ProviderType.GEMINI_API) is True

    def test_gemini_api_unavailable_without_key(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        pm = _make_manager(tmp_path)
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        assert pm.is_provider_available(ProviderType.GEMINI_API) is False

    def test_openai_api_available_with_key(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        pm = _make_manager(tmp_path)
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-abc")
        assert pm.is_provider_available(ProviderType.OPENAI_API) is True

    def test_openai_api_unavailable_without_key(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        pm = _make_manager(tmp_path)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        assert pm.is_provider_available(ProviderType.OPENAI_API) is False


# ═════════════════════════════════════════════════════════════════════════════
# 9. to_api_dict
# ═════════════════════════════════════════════════════════════════════════════


class TestToApiDict:
    def test_structure_keys(self, tmp_path: Path) -> None:
        pm = _make_manager(tmp_path)
        d = pm.to_api_dict()
        assert "active" in d
        assert "providers" in d
        assert "fallback" in d
        assert "thinking_options" in d

    def test_active_section(self, tmp_path: Path) -> None:
        pm = _make_manager(tmp_path)
        pm.set_provider(ProviderType.GEMINI_API, "gemini-2.5-flash")
        d = pm.to_api_dict()
        active = d["active"]
        assert active["provider"] == "gemini_api"
        assert active["model_id"] == "gemini-2.5-flash"
        assert active["thinking_depth"] == "auto"
        assert active["force_cloud"] is True

    def test_providers_list_excludes_auto(self, tmp_path: Path) -> None:
        pm = _make_manager(tmp_path)
        d = pm.to_api_dict()
        provider_ids = [p["id"] for p in d["providers"]]
        assert "auto" not in provider_ids
        assert "gemini_api" in provider_ids

    def test_thinking_options_complete(self, tmp_path: Path) -> None:
        pm = _make_manager(tmp_path)
        d = pm.to_api_dict()
        opts = d["thinking_options"]
        assert len(opts) == len(ThinkingDepth)
        ids = {o["id"] for o in opts}
        assert ids == {d.value for d in ThinkingDepth}

    def test_fallback_section(self, tmp_path: Path) -> None:
        pm = _make_manager(tmp_path)
        d = pm.to_api_dict()
        fb = d["fallback"]
        assert "chain" in fb
        assert "effective_chain" in fb
        assert "max_attempts" in fb


# ═════════════════════════════════════════════════════════════════════════════
# 10. format_status
# ═════════════════════════════════════════════════════════════════════════════


class TestFormatStatus:
    def test_format_status_contains_key_info(self, tmp_path: Path) -> None:
        """format_status возвращает строку с ключевой информацией."""
        pm = _make_manager(tmp_path)
        pm.set_provider(ProviderType.GEMINI_API, "gemini-2.5-flash")
        pm.set_thinking_depth(ThinkingDepth.MEDIUM)
        text = pm.format_status()
        assert "gemini-2.5-flash" in text
        assert "Активный провайдер" in text
        assert "Thinking" in text or "thinking" in text.lower()
        assert "Модель" in text

    def test_format_provider_list_not_empty(self, tmp_path: Path) -> None:
        pm = _make_manager(tmp_path)
        text = pm.format_provider_list()
        assert len(text) > 100
        assert "Все провайдеры" in text

    def test_format_thinking_help_not_empty(self, tmp_path: Path) -> None:
        pm = _make_manager(tmp_path)
        text = pm.format_thinking_help()
        assert "Thinking" in text
        for d in ThinkingDepth:
            assert d.value in text


# ═════════════════════════════════════════════════════════════════════════════
# 11. Персистентность
# ═════════════════════════════════════════════════════════════════════════════


class TestPersistence:
    def test_save_and_reload_round_trip(self, tmp_path: Path) -> None:
        """set_provider → новый instance → state восстановлен."""
        state_path = str(tmp_path / "provider_state.json")

        pm1 = ProviderManager.__new__(ProviderManager)
        pm1._state = ProviderState()
        pm1._STATE_FILE = state_path
        pm1.set_provider(ProviderType.OPENAI_API, "openai/gpt-4o")
        pm1.set_thinking_depth(ThinkingDepth.LOW)
        pm1.set_temperature(1.5)
        pm1.set_max_output_tokens(2048)
        pm1.set_fallback_chain([ProviderType.OPENAI_API, ProviderType.LM_STUDIO])
        pm1.report_usage(ProviderType.OPENAI_API, 1234)

        # Новая instance загружает с диска
        pm2 = ProviderManager.__new__(ProviderManager)
        pm2._state = ProviderState()
        pm2._STATE_FILE = state_path
        pm2._load_state()

        assert pm2.active_provider == ProviderType.OPENAI_API
        assert pm2._state.model_id == "openai/gpt-4o"
        assert pm2.thinking_depth == ThinkingDepth.LOW
        assert pm2._state.temperature == 1.5
        assert pm2._state.max_output_tokens == 2048
        assert pm2._state.fallback.chain == [ProviderType.OPENAI_API, ProviderType.LM_STUDIO]
        q = pm2._state.quotas[ProviderType.OPENAI_API]
        assert q.used_tokens == 1234
        assert q.requests_count == 1

    def test_malformed_json_does_not_raise(self, tmp_path: Path) -> None:
        """Повреждённый state-файл не роняет загрузку."""
        state_path = tmp_path / "provider_state.json"
        state_path.write_text("{broken json!!!", encoding="utf-8")

        pm = ProviderManager.__new__(ProviderManager)
        pm._state = ProviderState()
        pm._STATE_FILE = str(state_path)
        pm._load_state()  # не должно бросить

        # Состояние дефолтное
        assert pm.active_provider == ProviderType.AUTO

    def test_missing_state_file_uses_defaults(self, tmp_path: Path) -> None:
        state_path = str(tmp_path / "nonexistent.json")
        pm = ProviderManager.__new__(ProviderManager)
        pm._state = ProviderState()
        pm._STATE_FILE = state_path
        pm._load_state()
        assert pm.active_provider == ProviderType.AUTO
        assert pm._state.model_id == ""

    def test_invalid_thinking_depth_falls_back_to_auto(self, tmp_path: Path) -> None:
        """Невалидное значение thinking_depth в JSON → AUTO."""
        state_path = tmp_path / "provider_state.json"
        state_path.write_text(
            json.dumps(
                {
                    "provider": "auto",
                    "thinking_depth": "nonexistent_depth",
                }
            ),
            encoding="utf-8",
        )

        pm = ProviderManager.__new__(ProviderManager)
        pm._state = ProviderState()
        pm._STATE_FILE = str(state_path)
        pm._load_state()
        assert pm.thinking_depth == ThinkingDepth.AUTO

    def test_invalid_fallback_chain_keeps_default(self, tmp_path: Path) -> None:
        """Невалидные провайдеры в fallback chain → chain не меняется."""
        state_path = tmp_path / "provider_state.json"
        state_path.write_text(
            json.dumps(
                {
                    "provider": "auto",
                    "fallback": {"chain": ["invalid_provider", "also_bad"]},
                }
            ),
            encoding="utf-8",
        )

        pm = ProviderManager.__new__(ProviderManager)
        pm._state = ProviderState()
        pm._STATE_FILE = str(state_path)
        pm._load_state()
        # Дефолтная цепочка сохранена
        assert len(pm._state.fallback.chain) == 4


# ═════════════════════════════════════════════════════════════════════════════
# 12. Синглтон
# ═════════════════════════════════════════════════════════════════════════════


class TestSingleton:
    def test_module_singleton_exists(self) -> None:
        """Модульный синглтон provider_manager — instance ProviderManager."""
        assert isinstance(provider_manager, ProviderManager)
