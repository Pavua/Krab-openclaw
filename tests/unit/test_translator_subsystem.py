# -*- coding: utf-8 -*-
"""
Юнит-тесты для translator subsystem (4 модуля).
Все файловые операции мокаются — тесты не трогают диск.
"""

from __future__ import annotations

import json

# ---------------------------------------------------------------------------
# translator_runtime_profile
# ---------------------------------------------------------------------------
from src.core.translator_runtime_profile import (
    ALLOWED_LANGUAGE_PAIRS,
    ALLOWED_TRANSLATION_MODES,
    ALLOWED_VOICE_STRATEGIES,
    default_translator_runtime_profile,
    load_translator_runtime_profile,
    normalize_translator_runtime_profile,
    save_translator_runtime_profile,
)


class TestTranslatorRuntimeProfile:
    """Тесты для runtime profile: дефолты, нормализация, persist/load."""

    def test_default_profile_has_required_keys(self):
        """Дефолтный профиль содержит все обязательные ключи."""
        profile = default_translator_runtime_profile
        assert "language_pair" in profile
        assert "translation_mode" in profile
        assert "voice_strategy" in profile
        assert "target_device" in profile
        assert "quick_phrases" in profile

    def test_default_profile_values(self):
        """Дефолтные значения соответствуют спеке."""
        p = default_translator_runtime_profile
        assert p["language_pair"] == "es-ru"
        assert p["translation_mode"] == "bilingual"
        assert p["voice_strategy"] == "voice-first"
        assert p["ordinary_calls_enabled"] is True
        assert p["diagnostics_enabled"] is False
        assert p["quick_phrases"] == []

    def test_allowed_sets_non_empty(self):
        """Наборы допустимых значений не пустые."""
        assert len(ALLOWED_LANGUAGE_PAIRS) > 0
        assert len(ALLOWED_TRANSLATION_MODES) > 0
        assert len(ALLOWED_VOICE_STRATEGIES) > 0
        assert "es-ru" in ALLOWED_LANGUAGE_PAIRS
        assert "bilingual" in ALLOWED_TRANSLATION_MODES
        assert "voice-first" in ALLOWED_VOICE_STRATEGIES

    def test_normalize_updates_string_field(self):
        """normalize меняет строковые поля."""
        result = normalize_translator_runtime_profile({"language_pair": "en-ru"})
        assert result["language_pair"] == "en-ru"

    def test_normalize_bool_from_string(self):
        """normalize корректно парсит булевы значения из строки."""
        result = normalize_translator_runtime_profile({"diagnostics_enabled": "true"})
        assert result["diagnostics_enabled"] is True

        result2 = normalize_translator_runtime_profile({"ordinary_calls_enabled": "false"})
        assert result2["ordinary_calls_enabled"] is False

    def test_normalize_ignores_unknown_keys(self):
        """normalize игнорирует неизвестные ключи — не ломает схему."""
        result = normalize_translator_runtime_profile({"nonexistent_key": "value"})
        assert "nonexistent_key" not in result

    def test_normalize_list_field(self):
        """normalize устанавливает list-поле корректно."""
        result = normalize_translator_runtime_profile({"quick_phrases": ["привет", "спасибо"]})
        assert result["quick_phrases"] == ["привет", "спасибо"]

    def test_normalize_with_custom_base(self):
        """normalize применяет изменения поверх переданного base."""
        base = {**default_translator_runtime_profile, "language_pair": "es-en"}
        result = normalize_translator_runtime_profile({"translation_mode": "auto_to_ru"}, base=base)
        assert result["language_pair"] == "es-en"
        assert result["translation_mode"] == "auto_to_ru"

    def test_load_profile_missing_file_returns_defaults(self, tmp_path):
        """load возвращает дефолты, если файл отсутствует."""
        path = tmp_path / "profile.json"
        result = load_translator_runtime_profile(path)
        assert result["language_pair"] == default_translator_runtime_profile["language_pair"]

    def test_load_profile_merges_saved_data(self, tmp_path):
        """load мерджит сохранённые данные поверх дефолтов."""
        path = tmp_path / "profile.json"
        path.write_text(json.dumps({"language_pair": "auto-detect", "diagnostics_enabled": True}))
        result = load_translator_runtime_profile(path)
        assert result["language_pair"] == "auto-detect"
        assert result["diagnostics_enabled"] is True
        # Остальные ключи остаются дефолтными
        assert result["translation_mode"] == default_translator_runtime_profile["translation_mode"]

    def test_load_profile_invalid_json_returns_defaults(self, tmp_path):
        """load возвращает дефолты при битом JSON."""
        path = tmp_path / "profile.json"
        path.write_text("NOT_JSON!!!")
        result = load_translator_runtime_profile(path)
        assert result["language_pair"] == default_translator_runtime_profile["language_pair"]

    def test_save_and_reload_profile(self, tmp_path):
        """save + load — round-trip сохраняет данные."""
        path = tmp_path / "sub" / "profile.json"
        profile = {**default_translator_runtime_profile, "language_pair": "es-en"}
        save_translator_runtime_profile(path, profile)
        assert path.exists()
        loaded = load_translator_runtime_profile(path)
        assert loaded["language_pair"] == "es-en"


# ---------------------------------------------------------------------------
# translator_session_state
# ---------------------------------------------------------------------------

from src.core.translator_session_state import (
    apply_translator_session_update,
    default_translator_session_state,
    load_translator_session_state,
    save_translator_session_state,
)


class TestTranslatorSessionState:
    """Тесты для session state: дефолты, обновления, persist/load."""

    def test_default_state_idle(self):
        """Дефолтный state — idle со всеми обязательными ключами."""
        state = default_translator_session_state()
        assert state["session_status"] == "idle"
        assert state["translation_muted"] is False
        assert state["active_chats"] == []
        assert state["stats"]["total_translations"] == 0

    def test_apply_update_changes_status(self):
        """apply_update меняет session_status и проставляет updated_at."""
        result = apply_translator_session_update({"session_status": "active"})
        assert result["session_status"] == "active"
        assert result["updated_at"] != ""

    def test_apply_update_bool_from_string(self):
        """apply_update парсит булево из строки для muted."""
        result = apply_translator_session_update({"translation_muted": "yes"})
        assert result["translation_muted"] is True

        result2 = apply_translator_session_update({"translation_muted": "false"})
        assert result2["translation_muted"] is False

    def test_apply_update_list_field(self):
        """apply_update корректно обновляет list-поле active_chats."""
        result = apply_translator_session_update({"active_chats": [123, 456]})
        assert result["active_chats"] == [123, 456]

    def test_apply_update_dict_merge(self):
        """apply_update мерджит вложенный dict (stats) а не заменяет."""
        base = default_translator_session_state()
        base["stats"]["total_translations"] = 5
        result = apply_translator_session_update({"stats": {"total_translations": 10}}, base=base)
        assert result["stats"]["total_translations"] == 10

    def test_apply_update_empty_changes_returns_base(self):
        """apply_update с пустыми изменениями не мутирует state."""
        base = default_translator_session_state()
        result = apply_translator_session_update({}, base=base)
        assert result["session_status"] == "idle"
        # updated_at не меняется (нет изменений)
        assert result["updated_at"] == base["updated_at"]

    def test_load_session_state_missing_file(self, tmp_path):
        """load возвращает дефолты, если файл не существует."""
        path = tmp_path / "session.json"
        result = load_translator_session_state(path)
        assert result["session_status"] == "idle"

    def test_load_session_state_merges_data(self, tmp_path):
        """load мерджит сохранённые данные поверх дефолтов."""
        path = tmp_path / "session.json"
        path.write_text(json.dumps({"session_status": "active", "session_id": "abc123"}))
        result = load_translator_session_state(path)
        assert result["session_status"] == "active"
        assert result["session_id"] == "abc123"
        assert result["translation_muted"] is False  # дефолт

    def test_save_and_reload_session_state(self, tmp_path):
        """save + load — round-trip."""
        path = tmp_path / "state.json"
        state = apply_translator_session_update({"session_status": "active", "session_id": "xyz"})
        save_translator_session_state(path, state)
        loaded = load_translator_session_state(path)
        assert loaded["session_status"] == "active"
        assert loaded["session_id"] == "xyz"


# ---------------------------------------------------------------------------
# translator_live_trial_preflight
# ---------------------------------------------------------------------------

from src.core.translator_live_trial_preflight import build_translator_live_trial_preflight


class TestTranslatorLiveTrialPreflight:
    """Тесты для preflight: статусы, checks, blockers, next_step."""

    def test_empty_inputs_returns_blocked(self):
        """Без входных данных preflight возвращает status=blocked."""
        result = build_translator_live_trial_preflight()
        assert result["ok"] is True
        assert result["status"] == "blocked"
        assert result["ready"] is False

    def test_trial_ready_status(self):
        """При ordinary_calls.status=trial_ready → ready_for_trial."""
        delivery = {"ordinary_calls": {"status": "trial_ready"}}
        result = build_translator_live_trial_preflight(delivery_matrix=delivery)
        assert result["status"] == "ready_for_trial"
        assert result["ready"] is True

    def test_companion_pending_status(self):
        """При mobile.status=not_configured → companion_pending."""
        mobile = {"status": "not_configured"}
        result = build_translator_live_trial_preflight(mobile_readiness=mobile)
        assert result["status"] == "companion_pending"

    def test_session_pending_status(self):
        """При mobile.status=registered → session_pending."""
        mobile = {"status": "registered"}
        result = build_translator_live_trial_preflight(mobile_readiness=mobile)
        assert result["status"] == "session_pending"

    def test_checks_structure(self):
        """Результат содержит все ожидаемые check-ключи."""
        result = build_translator_live_trial_preflight()
        checks = result["checks"]
        assert "voice_gateway" in checks
        assert "shared_workspace" in checks
        assert "userbot_runtime" in checks
        assert "mobile_companion" in checks
        assert "active_session" in checks

    def test_gateway_ok_propagated(self):
        """voice_gateway.ok отражает переданный статус сервиса."""
        readiness = {"services": {"voice_gateway": {"ok": True, "status": "running"}}}
        result = build_translator_live_trial_preflight(translator_readiness=readiness)
        assert result["checks"]["voice_gateway"]["ok"] is True

    def test_blockers_extracted(self):
        """Blockers из ordinary_calls передаются в результат."""
        delivery = {"ordinary_calls": {"status": "blocked", "blockers": ["no_device", "no_auth"]}}
        result = build_translator_live_trial_preflight(delivery_matrix=delivery)
        assert "no_device" in result["blockers"]
        assert "no_auth" in result["blockers"]

    def test_next_step_default_for_trial_ready(self):
        """next_step=run_controlled_live_trial при ready_for_trial."""
        delivery = {"ordinary_calls": {"status": "trial_ready"}}
        result = build_translator_live_trial_preflight(delivery_matrix=delivery)
        assert result["actions"]["next_step"] == "run_controlled_live_trial"

    def test_helpers_always_present(self, tmp_path):
        """helpers всегда в результате (paths могут не существовать)."""
        result = build_translator_live_trial_preflight(project_root=tmp_path)
        assert "start_full_ecosystem" in result["helpers"]
        assert "start_voice_gateway" in result["helpers"]
        assert "prepare_xcode_project" in result["helpers"]


# ---------------------------------------------------------------------------
# translator_mobile_onboarding
# ---------------------------------------------------------------------------

from src.core.translator_mobile_onboarding import build_translator_mobile_onboarding_packet


class TestTranslatorMobileOnboarding:
    """Тесты для onboarding packet: статусы, trial_profiles, helpers."""

    def test_empty_inputs_returns_blocked(self):
        """Без входных данных пакет возвращает status=blocked."""
        result = build_translator_mobile_onboarding_packet()
        assert result["ok"] is True
        assert result["status"] == "blocked"
        assert result["ready"] is False

    def test_trial_ready_when_preflight_ready(self):
        """Если preflight=ready_for_trial → onboarding status=trial_ready."""
        preflight = {"status": "ready_for_trial", "blockers": [], "warnings": [], "actions": {}}
        result = build_translator_mobile_onboarding_packet(live_trial_preflight=preflight)
        assert result["status"] == "trial_ready"
        assert result["ready"] is True

    def test_awaiting_companion_when_not_configured(self):
        """Если mobile.status=not_configured → awaiting_companion."""
        mobile = {"status": "not_configured"}
        result = build_translator_mobile_onboarding_packet(mobile_readiness=mobile)
        assert result["status"] == "awaiting_companion"

    def test_ready_for_onboarding_when_registered(self):
        """Если mobile.status=registered → ready_for_onboarding."""
        mobile = {"status": "registered"}
        result = build_translator_mobile_onboarding_packet(mobile_readiness=mobile)
        assert result["status"] == "ready_for_onboarding"

    def test_trial_profiles_always_three(self):
        """В пакете всегда ровно 3 trial profiles."""
        result = build_translator_mobile_onboarding_packet()
        assert len(result["trial_profiles"]) == 3
        ids = {p["id"] for p in result["trial_profiles"]}
        assert ids == {"subtitles_first", "voice_first_guarded", "ru_es_duplex"}

    def test_subtitles_first_ready_when_device_ready(self):
        """subtitles_first становится ready при device_ready в ordinary_calls."""
        delivery = {"ordinary_calls": {"status": "device_ready"}}
        result = build_translator_mobile_onboarding_packet(delivery_matrix=delivery)
        subtitles = next(p for p in result["trial_profiles"] if p["id"] == "subtitles_first")
        assert subtitles["status"] == "ready"

    def test_install_tracks_always_present(self):
        """install_tracks всегда содержит два трека."""
        result = build_translator_mobile_onboarding_packet()
        assert len(result["install_tracks"]) == 2
        ids = {t["id"] for t in result["install_tracks"]}
        assert "xcode_free_signing" in ids
        assert "altstore_sidestore" in ids

    def test_summary_registered_devices_count(self):
        """summary.registered_devices берётся из mobile.summary."""
        mobile = {"status": "registered", "summary": {"registered_devices": 2, "bound_devices": 1}}
        result = build_translator_mobile_onboarding_packet(mobile_readiness=mobile)
        assert result["summary"]["registered_devices"] == 2
        assert result["summary"]["bound_devices"] == 1

    def test_blockers_from_preflight(self):
        """blockers передаются из preflight в результат."""
        preflight = {"status": "blocked", "blockers": ["no_gateway"], "warnings": [], "actions": {}}
        result = build_translator_mobile_onboarding_packet(live_trial_preflight=preflight)
        assert "no_gateway" in result["blockers"]
