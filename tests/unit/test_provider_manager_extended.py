# -*- coding: utf-8 -*-
"""
Расширенные тесты src/core/provider_manager.py.

Покрывает области, ещё не охваченные test_provider_manager.py и
test_core_provider_manager.py:

- GEMINI_OAUTH / OPENAI_OAUTH через мок auth-profiles.json
- get_available_providers — полный список
- format_provider_list — структура вывода, тир-секции, маркеры
- format_thinking_help — бюджеты, маркер текущего режима
- get_fallback_chain + lm_studio_as_last_resort флаг
- quota.percentage в format_status (> 80% → красный маркер)
- resolve_config_for_provider для GEMINI_OAUTH и OPENAI_OAUTH
- каталог моделей: уникальность id, наличие обязательных ключей
- to_api_dict: active.quota для ненулевого провайдера
- ProviderManager без STATE_FILE (директории нет)
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import mock_open, patch

from src.core.provider_manager import (
    PROVIDER_DISPLAY_NAMES,
    PROVIDER_MODELS,
    ProviderManager,
    ProviderState,
    ProviderType,
    ThinkingDepth,
)

# ── Хелпер: изолированный менеджер ───────────────────────────────────────────


def _make_pm(tmp_path: Path) -> ProviderManager:
    """Создаёт ProviderManager с временным state-файлом."""
    pm = ProviderManager.__new__(ProviderManager)
    pm._state = ProviderState()
    pm._STATE_FILE = str(tmp_path / "pm_state.json")
    return pm


# ═════════════════════════════════════════════════════════════════════════════
# 1. OAuth-провайдеры: is_provider_available
# ═════════════════════════════════════════════════════════════════════════════


class TestOAuthProviderAvailability:
    """Доступность OAuth-провайдеров через auth-profiles.json."""

    def _auth_file_profiles(self, provider_name: str) -> dict:
        """Формирует валидный auth-profiles.json с одним профилем."""
        return {
            "profiles": {
                "default": {
                    "provider": provider_name,
                    "access": "fake-access-token",
                }
            }
        }

    def test_gemini_oauth_available_with_valid_profile(self, tmp_path: Path) -> None:
        """GEMINI_OAUTH доступен, если auth-файл содержит google-antigravity профиль."""
        pm = _make_pm(tmp_path)
        auth_data = json.dumps(self._auth_file_profiles("google-antigravity"))
        with (
            patch("src.core.provider_manager.os.path.exists", return_value=True),
            patch("builtins.open", mock_open(read_data=auth_data)),
        ):
            assert pm.is_provider_available(ProviderType.GEMINI_OAUTH) is True

    def test_gemini_oauth_unavailable_wrong_provider(self, tmp_path: Path) -> None:
        """GEMINI_OAUTH недоступен, если в профилях нет google-antigravity."""
        pm = _make_pm(tmp_path)
        auth_data = json.dumps(self._auth_file_profiles("openai-codex"))
        with (
            patch("src.core.provider_manager.os.path.exists", return_value=True),
            patch("builtins.open", mock_open(read_data=auth_data)),
        ):
            assert pm.is_provider_available(ProviderType.GEMINI_OAUTH) is False

    def test_openai_oauth_available_with_valid_profile(self, tmp_path: Path) -> None:
        """OPENAI_OAUTH доступен при наличии openai-codex профиля с access."""
        pm = _make_pm(tmp_path)
        auth_data = json.dumps(self._auth_file_profiles("openai-codex"))
        with (
            patch("src.core.provider_manager.os.path.exists", return_value=True),
            patch("builtins.open", mock_open(read_data=auth_data)),
        ):
            assert pm.is_provider_available(ProviderType.OPENAI_OAUTH) is True

    def test_openai_oauth_unavailable_missing_access(self, tmp_path: Path) -> None:
        """OPENAI_OAUTH недоступен, если access пустой/None."""
        pm = _make_pm(tmp_path)
        auth_data = json.dumps(
            {"profiles": {"default": {"provider": "openai-codex", "access": ""}}}
        )
        with (
            patch("src.core.provider_manager.os.path.exists", return_value=True),
            patch("builtins.open", mock_open(read_data=auth_data)),
        ):
            assert pm.is_provider_available(ProviderType.OPENAI_OAUTH) is False

    def test_gemini_oauth_unavailable_corrupted_auth_file(self, tmp_path: Path) -> None:
        """GEMINI_OAUTH недоступен при битом JSON auth-файла."""
        pm = _make_pm(tmp_path)
        with (
            patch("src.core.provider_manager.os.path.exists", return_value=True),
            patch("builtins.open", mock_open(read_data="{not valid}")),
        ):
            assert pm.is_provider_available(ProviderType.GEMINI_OAUTH) is False


# ═════════════════════════════════════════════════════════════════════════════
# 2. get_available_providers
# ═════════════════════════════════════════════════════════════════════════════


class TestGetAvailableProviders:
    """get_available_providers возвращает реальный список."""

    def test_auto_and_lm_studio_always_present(self, tmp_path: Path) -> None:
        """AUTO и LM_STUDIO всегда в доступных."""
        pm = _make_pm(tmp_path)
        # Убираем все ключи API чтобы не мешали OAuth-провайдеры
        with patch.dict(os.environ, {}, clear=True):
            available = pm.get_available_providers()
        assert ProviderType.AUTO in available
        assert ProviderType.LM_STUDIO in available

    def test_gemini_api_in_available_when_key_set(self, tmp_path: Path) -> None:
        pm = _make_pm(tmp_path)
        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}):
            available = pm.get_available_providers()
        assert ProviderType.GEMINI_API in available

    def test_openai_api_not_in_available_without_key(self, tmp_path: Path) -> None:
        pm = _make_pm(tmp_path)
        clean = {k: v for k, v in os.environ.items() if k != "OPENAI_API_KEY"}
        with patch.dict(os.environ, clean, clear=True):
            available = pm.get_available_providers()
        assert ProviderType.OPENAI_API not in available

    def test_result_is_list_of_provider_types(self, tmp_path: Path) -> None:
        pm = _make_pm(tmp_path)
        available = pm.get_available_providers()
        assert isinstance(available, list)
        for p in available:
            assert isinstance(p, ProviderType)


# ═════════════════════════════════════════════════════════════════════════════
# 3. get_fallback_chain + lm_studio_as_last_resort
# ═════════════════════════════════════════════════════════════════════════════


class TestFallbackChainWithLastResort:
    """lm_studio_as_last_resort влияет на get_fallback_chain."""

    def test_lm_studio_included_even_if_not_in_available_when_last_resort(
        self, tmp_path: Path
    ) -> None:
        """LM_STUDIO включается в chain через last_resort даже без явной доступности."""
        pm = _make_pm(tmp_path)
        pm.set_fallback_chain([ProviderType.GEMINI_API, ProviderType.LM_STUDIO])
        pm.set_lm_studio_last_resort(True)
        # Мокаем available как пустой (кроме AUTO)
        with patch.object(pm, "get_available_providers", return_value=[ProviderType.AUTO]):
            chain = pm.get_fallback_chain()
        assert ProviderType.LM_STUDIO in chain

    def test_lm_studio_excluded_when_not_last_resort_and_not_available(
        self, tmp_path: Path
    ) -> None:
        """Без last_resort LM_STUDIO включается только если доступен."""
        pm = _make_pm(tmp_path)
        pm.set_fallback_chain([ProviderType.LM_STUDIO])
        pm.set_lm_studio_last_resort(False)
        # available не содержит LM_STUDIO
        with patch.object(pm, "get_available_providers", return_value=[ProviderType.AUTO]):
            chain = pm.get_fallback_chain()
        assert ProviderType.LM_STUDIO not in chain

    def test_max_attempts_persisted(self, tmp_path: Path) -> None:
        pm = _make_pm(tmp_path)
        pm._state.fallback.max_attempts = 5
        pm._save_state()
        pm2 = _make_pm(tmp_path)
        pm2._STATE_FILE = pm._STATE_FILE
        pm2._load_state()
        assert pm2._state.fallback.max_attempts == 5


# ═════════════════════════════════════════════════════════════════════════════
# 4. resolve_config_for_provider — OAuth провайдеры
# ═════════════════════════════════════════════════════════════════════════════


class TestResolveConfigOAuth:
    """Конфиг для OAuth провайдеров."""

    def test_gemini_oauth_force_cloud_true(self, tmp_path: Path) -> None:
        pm = _make_pm(tmp_path)
        cfg = pm.resolve_config_for_provider(ProviderType.GEMINI_OAUTH)
        assert cfg["force_cloud"] is True
        assert cfg["provider_type"] == "gemini_oauth"

    def test_openai_oauth_force_cloud_true(self, tmp_path: Path) -> None:
        pm = _make_pm(tmp_path)
        cfg = pm.resolve_config_for_provider(ProviderType.OPENAI_OAUTH)
        assert cfg["force_cloud"] is True
        assert cfg["provider_type"] == "openai_oauth"

    def test_auto_provider_force_cloud_true(self, tmp_path: Path) -> None:
        """AUTO тоже не LM_STUDIO → force_cloud=True."""
        pm = _make_pm(tmp_path)
        cfg = pm.resolve_config_for_provider(ProviderType.AUTO)
        assert cfg["force_cloud"] is True

    def test_thinking_budget_tokens_none_for_auto_depth(self, tmp_path: Path) -> None:
        """ThinkingDepth.AUTO → budget_tokens=None."""
        pm = _make_pm(tmp_path)
        pm.set_thinking_depth(ThinkingDepth.AUTO)
        cfg = pm.resolve_config_for_provider(ProviderType.GEMINI_API)
        assert cfg["thinking_budget_tokens"] is None

    def test_thinking_budget_tokens_for_low_depth(self, tmp_path: Path) -> None:
        """ThinkingDepth.LOW → budget_tokens=1024."""
        pm = _make_pm(tmp_path)
        pm.set_thinking_depth(ThinkingDepth.LOW)
        cfg = pm.resolve_config_for_provider(ProviderType.GEMINI_API)
        assert cfg["thinking_budget_tokens"] == 1024


# ═════════════════════════════════════════════════════════════════════════════
# 5. format_provider_list — структура и содержимое
# ═════════════════════════════════════════════════════════════════════════════


class TestFormatProviderList:
    """Детальный список провайдеров."""

    def test_contains_all_provider_names(self, tmp_path: Path) -> None:
        pm = _make_pm(tmp_path)
        text = pm.format_provider_list()
        for ptype, pname_display in PROVIDER_DISPLAY_NAMES.items():
            if ptype == ProviderType.AUTO:
                continue
            # display name может содержать спецсимволы, проверяем что хотя бы id есть
            assert ptype.value in text.lower() or pname_display.split()[0] in text

    def test_active_provider_marked(self, tmp_path: Path) -> None:
        """Активный провайдер помечается маркером АКТИВЕН."""
        pm = _make_pm(tmp_path)
        pm.set_provider(ProviderType.GEMINI_API)
        text = pm.format_provider_list()
        assert "АКТИВЕН" in text

    def test_model_ids_present_in_text(self, tmp_path: Path) -> None:
        """ID моделей каталога присутствуют в выводе."""
        pm = _make_pm(tmp_path)
        text = pm.format_provider_list()
        # Проверяем несколько реальных ID
        assert "gemini-2.5-flash" in text
        assert "openai/chatgpt-5" in text

    def test_tier_labels_present(self, tmp_path: Path) -> None:
        """Тир-секции присутствуют в выводе."""
        pm = _make_pm(tmp_path)
        text = pm.format_provider_list()
        # Хотя бы один tier-label должен присутствовать
        tier_keywords = ["Pro", "Flash", "Flagship", "Reasoning", "Ultra"]
        assert any(kw in text for kw in tier_keywords)

    def test_legend_present(self, tmp_path: Path) -> None:
        """Легенда внизу присутствует."""
        pm = _make_pm(tmp_path)
        text = pm.format_provider_list()
        assert "Легенда" in text

    def test_current_model_marked(self, tmp_path: Path) -> None:
        """Текущая модель помечается ✅ в списке."""
        pm = _make_pm(tmp_path)
        pm.set_provider(ProviderType.GEMINI_API, "gemini-2.5-flash")
        text = pm.format_provider_list()
        # Строка с текущей моделью должна иметь ✅
        lines = text.split("\n")
        line_with_model = next((l for l in lines if "gemini-2.5-flash" in l and "✅" in l), None)
        assert line_with_model is not None


# ═════════════════════════════════════════════════════════════════════════════
# 6. format_thinking_help — бюджеты и маркер текущего режима
# ═════════════════════════════════════════════════════════════════════════════


class TestFormatThinkingHelp:
    """Помощь по thinking-режимам."""

    def test_contains_budget_tokens_for_low(self, tmp_path: Path) -> None:
        """LOW режим показывает 1024 токена."""
        pm = _make_pm(tmp_path)
        text = pm.format_thinking_help()
        assert "1024" in text

    def test_contains_budget_tokens_for_high(self, tmp_path: Path) -> None:
        """HIGH режим показывает 32768 токена."""
        pm = _make_pm(tmp_path)
        text = pm.format_thinking_help()
        assert "32768" in text

    def test_current_depth_marked(self, tmp_path: Path) -> None:
        """Активный режим помечается АКТИВЕН."""
        pm = _make_pm(tmp_path)
        pm.set_thinking_depth(ThinkingDepth.MEDIUM)
        text = pm.format_thinking_help()
        # В строке medium должен быть маркер
        lines = text.split("\n")
        medium_line = next((l for l in lines if "medium" in l.lower()), None)
        assert medium_line is not None
        assert "АКТИВЕН" in medium_line

    def test_all_depth_ids_in_text(self, tmp_path: Path) -> None:
        """Все depth.value присутствуют в тексте."""
        pm = _make_pm(tmp_path)
        text = pm.format_thinking_help()
        for d in ThinkingDepth:
            assert d.value in text

    def test_commands_hint_present(self, tmp_path: Path) -> None:
        """Подсказка с командами присутствует."""
        pm = _make_pm(tmp_path)
        text = pm.format_thinking_help()
        assert "!provider thinking" in text


# ═════════════════════════════════════════════════════════════════════════════
# 7. format_status — квота > 80% (красный маркер)
# ═════════════════════════════════════════════════════════════════════════════


class TestFormatStatusQuotaWarning:
    """format_status помечает квоту красным при > 80%."""

    def test_quota_over_80_percent_red_marker(self, tmp_path: Path) -> None:
        pm = _make_pm(tmp_path)
        pm.set_provider(ProviderType.GEMINI_API)
        # Устанавливаем использование > 80%
        q = pm._state.quotas[ProviderType.GEMINI_API]
        q.used_tokens = 900
        q.limit_tokens = 1000
        q.requests_count = 5
        text = pm.format_status()
        assert "🔴" in text

    def test_quota_under_80_percent_no_red_marker(self, tmp_path: Path) -> None:
        pm = _make_pm(tmp_path)
        pm.set_provider(ProviderType.GEMINI_API)
        q = pm._state.quotas[ProviderType.GEMINI_API]
        q.used_tokens = 500
        q.limit_tokens = 1000
        q.requests_count = 3
        text = pm.format_status()
        assert "🔴" not in text

    def test_format_status_no_fallback_shows_none_label(self, tmp_path: Path) -> None:
        """Пустая fallback-цепочка → label 'нет'."""
        pm = _make_pm(tmp_path)
        pm.set_fallback_chain([])
        with patch.object(pm, "get_fallback_chain", return_value=[]):
            text = pm.format_status()
        assert "нет" in text


# ═════════════════════════════════════════════════════════════════════════════
# 8. Каталог моделей: целостность
# ═════════════════════════════════════════════════════════════════════════════


class TestModelCatalogIntegrity:
    """Каталог моделей не содержит дубликатов и имеет обязательные поля."""

    def test_no_duplicate_ids_per_provider(self) -> None:
        """Внутри одного провайдера нет двух моделей с одинаковым id."""
        for ptype, models in PROVIDER_MODELS.items():
            ids = [m["id"] for m in models]
            assert len(ids) == len(set(ids)), f"Дублирующиеся id у провайдера {ptype.value}"

    def test_all_models_have_required_keys(self) -> None:
        """Каждая модель имеет: id, name, vision, thinking, default, tier."""
        required = {"id", "name", "vision", "thinking", "default", "tier"}
        for ptype, models in PROVIDER_MODELS.items():
            for m in models:
                missing = required - m.keys()
                assert not missing, (
                    f"У модели {m.get('id', '?')} ({ptype.value}) нет ключей: {missing}"
                )

    def test_default_model_ids_are_strings(self) -> None:
        """Все id моделей — строки."""
        for ptype, models in PROVIDER_MODELS.items():
            for m in models:
                assert isinstance(m["id"], str) and m["id"], (
                    f"Пустой/нестроковый id у {ptype.value}"
                )

    def test_at_most_one_default_per_provider(self) -> None:
        """У каждого провайдера не более одной модели с default=True."""
        for ptype, models in PROVIDER_MODELS.items():
            defaults = [m for m in models if m.get("default")]
            assert len(defaults) <= 1, (
                f"Несколько default у {ptype.value}: {[m['id'] for m in defaults]}"
            )

    def test_lm_studio_has_vision_model(self) -> None:
        """LM_STUDIO каталог содержит хотя бы одну vision-модель."""
        models = PROVIDER_MODELS[ProviderType.LM_STUDIO]
        vision_models = [m for m in models if m.get("vision")]
        assert len(vision_models) > 0

    def test_gemini_api_has_thinking_model(self) -> None:
        """GEMINI_API содержит хотя бы одну thinking-модель."""
        models = PROVIDER_MODELS[ProviderType.GEMINI_API]
        thinking_models = [m for m in models if m.get("thinking")]
        assert len(thinking_models) > 0


# ═════════════════════════════════════════════════════════════════════════════
# 9. to_api_dict — quota для ненулевых провайдеров
# ═════════════════════════════════════════════════════════════════════════════


class TestToApiDictQuota:
    """to_api_dict корректно передаёт quota."""

    def test_active_quota_has_correct_tokens(self, tmp_path: Path) -> None:
        """active.quota показывает реальные токены для не-AUTO провайдера."""
        pm = _make_pm(tmp_path)
        pm.set_provider(ProviderType.GEMINI_API)
        pm.report_usage(ProviderType.GEMINI_API, 777)
        d = pm.to_api_dict()
        assert d["active"]["quota"]["used"] == 777

    def test_providers_list_contains_quota_for_each(self, tmp_path: Path) -> None:
        """Каждый провайдер в providers-списке имеет quota-поле."""
        pm = _make_pm(tmp_path)
        d = pm.to_api_dict()
        for p in d["providers"]:
            assert "quota" in p
            assert "used" in p["quota"]

    def test_providers_available_flag_reflects_lm_studio(self, tmp_path: Path) -> None:
        """LM_STUDIO всегда available=True в providers-списке."""
        pm = _make_pm(tmp_path)
        d = pm.to_api_dict()
        lm = next(p for p in d["providers"] if p["id"] == "lm_studio")
        assert lm["available"] is True


# ═════════════════════════════════════════════════════════════════════════════
# 10. _save_state — директория не существует
# ═════════════════════════════════════════════════════════════════════════════


class TestSaveStateEdgeCases:
    """_save_state создаёт директорию при необходимости."""

    def test_save_creates_intermediate_dirs(self, tmp_path: Path) -> None:
        """_save_state создаёт вложенные директории."""
        pm = _make_pm(tmp_path)
        pm._STATE_FILE = str(tmp_path / "nested" / "deep" / "state.json")
        pm._save_state()
        assert Path(pm._STATE_FILE).exists()

    def test_save_and_load_vision_model(self, tmp_path: Path) -> None:
        """vision_model_id сохраняется и восстанавливается."""
        pm = _make_pm(tmp_path)
        pm.set_vision_model("my-custom-vision-model-v2")

        pm2 = _make_pm(tmp_path)
        pm2._STATE_FILE = pm._STATE_FILE
        pm2._load_state()
        assert pm2._state.vision_model_id == "my-custom-vision-model-v2"

    def test_save_and_load_lm_studio_last_resort(self, tmp_path: Path) -> None:
        """lm_studio_as_last_resort сохраняется и восстанавливается."""
        pm = _make_pm(tmp_path)
        pm.set_lm_studio_last_resort(True)

        pm2 = _make_pm(tmp_path)
        pm2._STATE_FILE = pm._STATE_FILE
        pm2._load_state()
        assert pm2._state.fallback.lm_studio_as_last_resort is True
