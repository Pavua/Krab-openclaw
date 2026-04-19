# -*- coding: utf-8 -*-
"""
Тесты internal helper-методов WebApp.

Покрываем статические и classmethod-помощники, которые не требуют
живого окружения (FastAPI app/Telegram/OpenClaw), но содержат
нетривиальную логику форматирования, нормализации и валидации.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.modules.web_app import WebApp

# ---------------------------------------------------------------------------
# Вспомогательные фикстуры
# ---------------------------------------------------------------------------


def _make_web_app() -> WebApp:
    """Создаёт минимальный экземпляр WebApp без реальных зависимостей."""
    deps: dict = {
        "kraab": MagicMock(),
        "openclaw_client": MagicMock(),
    }
    return WebApp(deps=deps, port=0)


# ---------------------------------------------------------------------------
# _tail_text
# ---------------------------------------------------------------------------


class TestTailText:
    """Тесты хелпера обрезки текста по хвосту."""

    def test_короткий_текст_без_обрезки(self):
        assert WebApp._tail_text("hello", 100) == "hello"

    def test_длинный_текст_обрезается(self):
        result = WebApp._tail_text("a" * 3000, 2000)
        assert len(result) == 2000

    def test_пустая_строка(self):
        assert WebApp._tail_text("") == ""

    def test_none_обрабатывается(self):
        # None кастуется до пустой строки
        assert WebApp._tail_text(None) == ""  # type: ignore[arg-type]

    def test_ровно_граница(self):
        # Текст в точности равен max_chars — не должен обрезаться
        text = "x" * 2000
        assert WebApp._tail_text(text, 2000) == text


# ---------------------------------------------------------------------------
# _mask_secret
# ---------------------------------------------------------------------------


class TestMaskSecret:
    """Тесты маскирования токенов/ключей."""

    def test_нормальный_токен(self):
        result = WebApp._mask_secret("sk-abcdefghij")
        assert result == "sk-...hij"

    def test_короткий_секрет_полностью_маскируется(self):
        result = WebApp._mask_secret("abc")
        assert result == "***"

    def test_пустая_строка_возвращает_пустую(self):
        assert WebApp._mask_secret("") == ""

    def test_ровно_6_символов(self):
        # ≤6 — полная маска
        assert WebApp._mask_secret("abcdef") == "******"

    def test_семь_символов(self):
        result = WebApp._mask_secret("abcdefg")
        assert result == "abc...efg"


# ---------------------------------------------------------------------------
# _normalize_thinking_mode
# ---------------------------------------------------------------------------


class TestNormalizeThinkingMode:
    """Тесты нормализации режима thinking OpenClaw runtime."""

    @pytest.mark.parametrize(
        "value",
        ["off", "minimal", "low", "medium", "high", "xhigh", "adaptive"],
    )
    def test_допустимые_значения(self, value: str):
        assert WebApp._normalize_thinking_mode(value) == value

    def test_алиас_auto_маппится_в_adaptive(self):
        assert WebApp._normalize_thinking_mode("auto") == "adaptive"

    def test_регистр_игнорируется(self):
        assert WebApp._normalize_thinking_mode("HIGH") == "high"

    def test_недопустимое_значение_бросает_ошибку(self):
        with pytest.raises(ValueError, match="runtime_invalid_thinking_mode"):
            WebApp._normalize_thinking_mode("turbo")

    def test_пустая_строка_allow_blank(self):
        assert WebApp._normalize_thinking_mode("", allow_blank=True) == ""

    def test_пустая_строка_без_allow_blank(self):
        with pytest.raises(ValueError):
            WebApp._normalize_thinking_mode("")


# ---------------------------------------------------------------------------
# _normalize_context_tokens
# ---------------------------------------------------------------------------


class TestNormalizeContextTokens:
    """Тесты валидации contextTokens."""

    def test_минимально_допустимое_значение(self):
        assert WebApp._normalize_context_tokens(4096) == 4096

    def test_максимально_допустимое_значение(self):
        assert WebApp._normalize_context_tokens(2_000_000) == 2_000_000

    def test_строковое_число_принимается(self):
        assert WebApp._normalize_context_tokens("32768") == 32768

    def test_ниже_минимума(self):
        with pytest.raises(ValueError, match="runtime_invalid_context_tokens"):
            WebApp._normalize_context_tokens(1024)

    def test_выше_максимума(self):
        with pytest.raises(ValueError, match="runtime_invalid_context_tokens"):
            WebApp._normalize_context_tokens(9_999_999)

    def test_нечисловое_значение(self):
        with pytest.raises(ValueError, match="runtime_invalid_context_tokens"):
            WebApp._normalize_context_tokens("bad")


# ---------------------------------------------------------------------------
# _normalize_runtime_max_concurrent
# ---------------------------------------------------------------------------


class TestNormalizeRuntimeMaxConcurrent:
    """Тесты валидации queue concurrency."""

    def test_единица_допустима(self):
        assert WebApp._normalize_runtime_max_concurrent(1) == 1

    def test_максимум_64(self):
        assert WebApp._normalize_runtime_max_concurrent(64) == 64

    def test_ноль_отклоняется(self):
        with pytest.raises(ValueError, match="runtime_invalid_max_concurrent"):
            WebApp._normalize_runtime_max_concurrent(0)

    def test_65_отклоняется(self):
        with pytest.raises(ValueError, match="runtime_invalid_max_concurrent"):
            WebApp._normalize_runtime_max_concurrent(65)

    def test_строковый_ввод(self):
        assert WebApp._normalize_runtime_max_concurrent("8") == 8

    def test_нечисловой_ввод(self):
        with pytest.raises(ValueError, match="runtime_invalid_max_concurrent"):
            WebApp._normalize_runtime_max_concurrent("много")


# ---------------------------------------------------------------------------
# _humanize_remaining_ms
# ---------------------------------------------------------------------------


class TestHumanizeRemainingMs:
    """Тесты форматирования оставшегося времени в читаемый вид."""

    def test_ноль_минут(self):
        assert WebApp._humanize_remaining_ms(0) == "0м"

    def test_только_минуты(self):
        assert WebApp._humanize_remaining_ms(5 * 60 * 1000) == "5м"

    def test_часы_и_минуты(self):
        ms = (2 * 60 + 30) * 60 * 1000
        assert WebApp._humanize_remaining_ms(ms) == "2ч 30м"

    def test_дни(self):
        ms = 3 * 24 * 60 * 60 * 1000
        assert WebApp._humanize_remaining_ms(ms) == "3д"

    def test_отрицательное_значение(self):
        result = WebApp._humanize_remaining_ms(-60 * 1000)
        assert result.startswith("-")

    def test_нечисловое_значение_возвращает_пустую(self):
        assert WebApp._humanize_remaining_ms("bad") == ""

    def test_none_возвращает_пустую(self):
        assert WebApp._humanize_remaining_ms(None) == ""


# ---------------------------------------------------------------------------
# _canonical_runtime_model_id
# ---------------------------------------------------------------------------


class TestCanonicalRuntimeModelId:
    """Тесты формирования provider-prefixed model id."""

    def test_без_слеша_добавляет_префикс(self):
        assert WebApp._canonical_runtime_model_id("google", "gemini-3-pro") == "google/gemini-3-pro"

    def test_уже_с_префиксом_не_дублируется(self):
        assert (
            WebApp._canonical_runtime_model_id("google", "google/gemini-3-pro")
            == "google/gemini-3-pro"
        )

    def test_пустой_model_id(self):
        assert WebApp._canonical_runtime_model_id("google", "") == ""

    def test_пустой_провайдер(self):
        assert WebApp._canonical_runtime_model_id("", "gemini-3-pro") == "gemini-3-pro"


# ---------------------------------------------------------------------------
# _provider_label
# ---------------------------------------------------------------------------


class TestProviderLabel:
    """Тесты отображаемого имени провайдера."""

    def test_google(self):
        assert WebApp._provider_label("google") == "Google"

    def test_lmstudio(self):
        assert WebApp._provider_label("lmstudio") == "LM Studio"

    def test_неизвестный_провайдер_возвращается_как_есть(self):
        assert WebApp._provider_label("my-custom") == "my-custom"

    def test_пустой_провайдер(self):
        assert WebApp._provider_label("") == "provider"


# ---------------------------------------------------------------------------
# _quota_state_from_failure_counts
# ---------------------------------------------------------------------------


class TestQuotaStateFromFailureCounts:
    """Тесты классификации quota-состояния провайдера."""

    def test_quota_blocked(self):
        result = WebApp._quota_state_from_failure_counts({"quota_exceeded": 3})
        assert result["quota_state"] == "blocked"

    def test_rate_limit(self):
        result = WebApp._quota_state_from_failure_counts({"rate_limit": 1})
        assert result["quota_state"] == "limited"

    def test_нет_данных(self):
        result = WebApp._quota_state_from_failure_counts({})
        assert result["quota_state"] == "unknown"

    def test_none_безопасно(self):
        result = WebApp._quota_state_from_failure_counts(None)
        assert result["quota_state"] == "unknown"


# ---------------------------------------------------------------------------
# _float_env
# ---------------------------------------------------------------------------


class TestFloatEnv:
    """Тесты чтения float из env с clamping."""

    def test_нормальное_значение(self):
        with patch.dict(os.environ, {"TEST_VAL": "10.0"}):
            result = WebApp._float_env("TEST_VAL", 5.0, min_value=1.0, max_value=100.0)
        assert result == 10.0

    def test_clamping_снизу(self):
        with patch.dict(os.environ, {"TEST_VAL": "0.0"}):
            result = WebApp._float_env("TEST_VAL", 5.0, min_value=1.0, max_value=100.0)
        assert result == 1.0

    def test_clamping_сверху(self):
        with patch.dict(os.environ, {"TEST_VAL": "999.0"}):
            result = WebApp._float_env("TEST_VAL", 5.0, min_value=1.0, max_value=100.0)
        assert result == 100.0

    def test_fallback_на_default_если_не_число(self):
        with patch.dict(os.environ, {"TEST_VAL": "bad"}):
            result = WebApp._float_env("TEST_VAL", 5.0, min_value=1.0, max_value=100.0)
        assert result == 5.0

    def test_переменная_отсутствует_возвращает_default(self):
        env = {k: v for k, v in os.environ.items() if k != "TEST_MISSING_KEY"}
        with patch.dict(os.environ, env, clear=True):
            result = WebApp._float_env("TEST_MISSING_KEY", 7.5, min_value=1.0, max_value=100.0)
        assert result == 7.5


# ---------------------------------------------------------------------------
# _bool_env
# ---------------------------------------------------------------------------


class TestBoolEnv:
    """Тесты безопасного парсинга булевых env."""

    @pytest.mark.parametrize("truthy", ["1", "true", "yes", "on", "True", "YES"])
    def test_truthy_значения(self, truthy: str):
        assert WebApp._bool_env(truthy) is True

    @pytest.mark.parametrize("falsy", ["0", "false", "no", "off", "nope"])
    def test_falsy_значения(self, falsy: str):
        assert WebApp._bool_env(falsy) is False

    def test_пустая_строка_возвращает_default_false(self):
        assert WebApp._bool_env("") is False

    def test_пустая_строка_возвращает_default_true(self):
        assert WebApp._bool_env("", default=True) is True


# ---------------------------------------------------------------------------
# _clone_jsonish_dict
# ---------------------------------------------------------------------------


class TestCloneJsonishDict:
    """Тесты неглубокого клонирования dict-payload."""

    def test_списки_клонируются(self):
        orig = {"items": [1, 2, 3]}
        cloned = WebApp._clone_jsonish_dict(orig)
        cloned["items"].append(4)
        assert orig["items"] == [1, 2, 3]

    def test_вложенный_dict_клонируется(self):
        orig = {"nested": {"a": 1}}
        cloned = WebApp._clone_jsonish_dict(orig)
        cloned["nested"]["a"] = 99
        assert orig["nested"]["a"] == 1

    def test_скалярные_значения(self):
        orig = {"x": 42, "y": "hello"}
        cloned = WebApp._clone_jsonish_dict(orig)
        assert cloned == orig

    def test_none_возвращает_пустой_dict(self):
        assert WebApp._clone_jsonish_dict(None) == {}  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# _json_backup_path
# ---------------------------------------------------------------------------


class TestJsonBackupPath:
    """Тесты формирования backup-пути для JSON-файлов."""

    def test_формат_содержит_метку(self):
        path = Path("/tmp/test.json")
        result = WebApp._json_backup_path(path, label="my_label")
        assert "my_label" in str(result)
        assert result.name.startswith("test.json.bak_")

    def test_специальные_символы_в_метке_нормализуются(self):
        path = Path("/tmp/config.json")
        result = WebApp._json_backup_path(path, label="My Label! 2026")
        # Метка должна содержать только безопасные символы
        bak_part = result.name
        assert "!" not in bak_part
        assert " " not in bak_part


# ---------------------------------------------------------------------------
# _paths_match
# ---------------------------------------------------------------------------


class TestPathsMatch:
    """Тесты сравнения путей без лишних исключений."""

    def test_одинаковые_пути(self):
        assert WebApp._paths_match("/tmp/foo", "/tmp/foo") is True

    def test_разные_пути(self):
        assert WebApp._paths_match("/tmp/foo", "/tmp/bar") is False

    def test_path_objects(self):
        assert WebApp._paths_match(Path("/tmp/foo"), Path("/tmp/foo")) is True
