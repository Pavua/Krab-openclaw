# -*- coding: utf-8 -*-
"""Wave 55-B: тесты для scripts/krab_anthropic_auth_check.py и !health anthropic.

Все внешние зависимости (gcloud, AnthropicVertex SDK, filesystem) замоканы.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

# ---------------------------------------------------------------------------
# Загрузка скрипта из scripts/ через importlib (без __init__ в scripts/)
# ---------------------------------------------------------------------------

_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "krab_anthropic_auth_check.py"


def _load_module():
    """Загружает krab_anthropic_auth_check как модуль."""
    spec = importlib.util.spec_from_file_location("krab_anthropic_auth_check", _SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# Загружаем один раз на весь модуль
_mod = _load_module()


# ---------------------------------------------------------------------------
# Вспомогательные фикстуры
# ---------------------------------------------------------------------------


def _models_json(model_ids: list[str]) -> dict:
    """Создаёт минимальный dict для models.json с anthropic-vertex провайдером."""
    return {
        "providers": {
            "anthropic-vertex": {
                "baseUrl": "https://aiplatform.googleapis.com/v1",
                "models": [{"id": mid} for mid in model_ids],
            }
        }
    }


# ---------------------------------------------------------------------------
# test_check_helper_runs — happy path: токен valid, модели доступны
# ---------------------------------------------------------------------------


class TestCheckHelperRuns:
    """test_check_helper_runs: полный happy path с замоканными внешними зависимостями."""

    def test_check_helper_runs(self, tmp_path: Path) -> None:
        """Скрипт запускается, возвращает all_ok=True при валидном токене и ответе модели."""
        models_path = tmp_path / "models.json"
        models_path.write_text(
            json.dumps(_models_json(["claude-sonnet-4-6", "claude-opus-4-6"])),
            encoding="utf-8",
        )

        # Мокаем gcloud subprocess и AnthropicVertex SDK
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "ya29.faketoken123456789"

        mock_content = MagicMock()
        mock_content.text = "OK"
        mock_resp = MagicMock()
        mock_resp.content = [mock_content]
        mock_av_instance = MagicMock()
        mock_av_instance.messages.create.return_value = mock_resp
        mock_av_cls = MagicMock(return_value=mock_av_instance)

        with (
            patch("subprocess.run", return_value=mock_proc),
            patch.dict("sys.modules", {"anthropic": MagicMock(AnthropicVertex=mock_av_cls)}),
        ):
            result = asyncio.run(_mod.run_check(models_path=models_path))

        assert result["gcloud_token_valid"] is True
        assert result["all_ok"] is True
        assert len(result["models"]) == 2
        for m in result["models"]:
            assert m["auth_ok"] is True
            assert m["error"] is None

    def test_check_helper_returns_json(self, tmp_path: Path) -> None:
        """run_check возвращает dict с обязательными ключами."""
        models_path = tmp_path / "models.json"
        models_path.write_text(
            json.dumps(_models_json(["claude-sonnet-4-6"])),
            encoding="utf-8",
        )

        mock_proc = MagicMock(returncode=0, stdout="ya29.token")
        mock_content = MagicMock(text="OK")
        mock_resp = MagicMock(content=[mock_content])
        mock_av = MagicMock(
            return_value=MagicMock(messages=MagicMock(create=MagicMock(return_value=mock_resp)))
        )

        with (
            patch("subprocess.run", return_value=mock_proc),
            patch.dict("sys.modules", {"anthropic": MagicMock(AnthropicVertex=mock_av)}),
        ):
            result = asyncio.run(_mod.run_check(models_path=models_path))

        # Обязательные ключи
        assert "gcloud_token_valid" in result
        assert "project" in result
        assert "region" in result
        assert "models" in result
        assert "all_ok" in result
        # models — список словарей с нужными ключами
        assert isinstance(result["models"], list)
        assert len(result["models"]) == 1
        m = result["models"][0]
        assert "id" in m
        assert "auth_ok" in m
        assert "latency_ms" in m
        assert "error" in m


# ---------------------------------------------------------------------------
# test_check_helper_detects_expired_token — gcloud returncode=1
# ---------------------------------------------------------------------------


class TestCheckHelperDetectsExpiredToken:
    """test_check_helper_detects_expired_token: gcloud failure → auth_ok=False для всех моделей."""

    def test_check_helper_detects_expired_token(self, tmp_path: Path) -> None:
        """Если gcloud вернул ошибку, все модели получают auth_ok=False без SDK-вызова."""
        models_path = tmp_path / "models.json"
        models_path.write_text(
            json.dumps(_models_json(["claude-sonnet-4-6"])),
            encoding="utf-8",
        )

        # gcloud возвращает ошибку (токен истёк)
        mock_proc = MagicMock(returncode=1, stdout="")

        av_cls = MagicMock()  # SDK не должен вызываться
        with (
            patch("subprocess.run", return_value=mock_proc),
            patch.dict("sys.modules", {"anthropic": MagicMock(AnthropicVertex=av_cls)}),
        ):
            result = asyncio.run(_mod.run_check(models_path=models_path))

        assert result["gcloud_token_valid"] is False
        assert result["all_ok"] is False
        assert len(result["models"]) == 1
        assert result["models"][0]["auth_ok"] is False
        assert "ADC" in (result["models"][0]["error"] or "")
        # SDK не должен вызывался ни разу
        av_cls.assert_not_called()

    def test_gcloud_exception_treated_as_invalid(self) -> None:
        """Если subprocess.run бросает исключение — токен считается невалидным."""
        with patch("subprocess.run", side_effect=FileNotFoundError("gcloud not found")):
            result = _mod.check_gcloud_token()

        assert result is False


# ---------------------------------------------------------------------------
# test_check_helper_handles_missing_models — models.json нет или пустой
# ---------------------------------------------------------------------------


class TestCheckHelperHandlesMissingModels:
    """test_check_helper_handles_missing_models: отсутствие моделей не ронит скрипт."""

    def test_models_json_not_found(self, tmp_path: Path) -> None:
        """Если models.json не существует, models=[]. all_ok=False."""
        missing_path = tmp_path / "nonexistent_models.json"

        mock_proc = MagicMock(returncode=0, stdout="ya29.faketoken")
        with patch("subprocess.run", return_value=mock_proc):
            result = asyncio.run(_mod.run_check(models_path=missing_path))

        assert result["models"] == []
        assert result["all_ok"] is False  # нет моделей → не all_ok

    def test_models_json_no_anthropic_provider(self, tmp_path: Path) -> None:
        """Если anthropic-vertex нет в providers — models пустой."""
        models_path = tmp_path / "models.json"
        models_path.write_text(
            json.dumps({"providers": {"google": {}}}),
            encoding="utf-8",
        )

        mock_proc = MagicMock(returncode=0, stdout="ya29.faketoken")
        with patch("subprocess.run", return_value=mock_proc):
            result = asyncio.run(_mod.run_check(models_path=models_path))

        assert result["models"] == []

    def test_models_json_malformed(self, tmp_path: Path) -> None:
        """Если JSON невалидный, get_anthropic_vertex_models возвращает []."""
        models_path = tmp_path / "models.json"
        models_path.write_text("{ invalid json !!!", encoding="utf-8")

        ids = _mod.get_anthropic_vertex_models(models_path)
        assert ids == []


# ---------------------------------------------------------------------------
# test_check_helper_sdk_error — SDK бросает исключение
# ---------------------------------------------------------------------------


class TestCheckHelperSDKError:
    """SDK exception → auth_ok=False с описанием ошибки."""

    def test_sdk_auth_error_captured(self, tmp_path: Path) -> None:
        """PermissionDenied от SDK → auth_ok=False, error содержит текст."""
        models_path = tmp_path / "models.json"
        models_path.write_text(
            json.dumps(_models_json(["claude-sonnet-4-6"])),
            encoding="utf-8",
        )

        mock_av_instance = MagicMock()
        mock_av_instance.messages.create.side_effect = Exception(
            "403 PERMISSION_DENIED: Caller does not have permission"
        )
        mock_av_cls = MagicMock(return_value=mock_av_instance)

        # Патчим check_gcloud_token на уровне модуля — гарантируем token_valid=True
        with (
            patch.object(_mod, "check_gcloud_token", return_value=True),
            patch.dict("sys.modules", {"anthropic": MagicMock(AnthropicVertex=mock_av_cls)}),
        ):
            result = asyncio.run(_mod.run_check(models_path=models_path))

        assert result["all_ok"] is False
        assert len(result["models"]) == 1
        m = result["models"][0]
        assert m["auth_ok"] is False
        assert m["error"] is not None
        assert "PERMISSION_DENIED" in m["error"]


# ---------------------------------------------------------------------------
# test_telegram_subcommand_invokes_helper — !health anthropic subcommand
# ---------------------------------------------------------------------------


class TestTelegramSubcommandInvokesHelper:
    """test_telegram_subcommand_invokes_helper: !health anthropic вызывает run_check + reply."""

    @pytest.mark.asyncio
    async def test_health_anthropic_subcommand_calls_run_check(self) -> None:
        """`!health anthropic` вызывает run_check и возвращает форматированный текст."""
        from src.core.access_control import AccessLevel
        from src.handlers.command_handlers import handle_health

        # Минимальный stub бота с OWNER access_profile
        mock_access_profile = SimpleNamespace(level=AccessLevel.OWNER)
        bot = SimpleNamespace(
            me=SimpleNamespace(id=111),
            _proactive_watch_task=MagicMock(**{"done.return_value": False}),
            get_voice_runtime_profile=lambda: {"enabled": True, "voice": "ru-RU"},
            _get_command_args=lambda m: "anthropic",
            _get_access_profile=lambda user: mock_access_profile,
            _session_start_time=None,
        )
        msg = SimpleNamespace(
            from_user=SimpleNamespace(id=42),
            reply=AsyncMock(),
            edit=AsyncMock(),
        )

        # Мокаем importlib.util.spec_from_file_location чтобы не реально грузить скрипт
        mock_run_check_data = {
            "gcloud_token_valid": True,
            "project": "test-project",
            "region": "us-east5",
            "models": [
                {
                    "id": "anthropic-vertex/claude-sonnet-4-6",
                    "auth_ok": True,
                    "latency_ms": 500.0,
                    "error": None,
                }
            ],
            "all_ok": True,
        }
        mock_format_result = "✅ Все OK (mocked)"

        mock_script_mod = MagicMock()
        mock_script_mod.run_check = AsyncMock(return_value=mock_run_check_data)
        mock_script_mod.format_check_result = MagicMock(return_value=mock_format_result)

        mock_spec = MagicMock()
        mock_spec.loader = MagicMock()

        with (
            patch("importlib.util.spec_from_file_location", return_value=mock_spec),
            patch("importlib.util.module_from_spec", return_value=mock_script_mod),
        ):
            await handle_health(bot, msg)

        # Проверяем что reply был вызван с форматированным текстом
        msg.reply.assert_called_once_with(mock_format_result)

    @pytest.mark.asyncio
    async def test_health_anthropic_owner_only(self) -> None:
        """`!health anthropic` без OWNER доступа → UserInputError."""
        from src.core.access_control import AccessLevel
        from src.core.exceptions import UserInputError
        from src.handlers.command_handlers import handle_health

        non_owner_profile = SimpleNamespace(level=AccessLevel.GUEST)
        bot = SimpleNamespace(
            me=SimpleNamespace(id=111),
            _proactive_watch_task=MagicMock(**{"done.return_value": False}),
            get_voice_runtime_profile=lambda: {"enabled": False, "voice": ""},
            _get_command_args=lambda m: "anthropic",
            _get_access_profile=lambda user: non_owner_profile,
        )
        msg = SimpleNamespace(
            from_user=SimpleNamespace(id=999),
            reply=AsyncMock(),
            edit=AsyncMock(),
        )

        with pytest.raises(UserInputError) as exc_info:
            await handle_health(bot, msg)
        # UserInputError.user_message содержит ограничение доступа (русское "владельцу")
        msg_text = exc_info.value.user_message or ""
        assert "anthropic" in msg_text.lower() or "владельц" in msg_text


# ---------------------------------------------------------------------------
# test_format_check_result — форматирование результата
# ---------------------------------------------------------------------------


class TestFormatCheckResult:
    """Проверяем format_check_result без IO-зависимостей."""

    def test_format_all_ok(self) -> None:
        """all_ok=True → текст содержит '✅ Всё OK'."""
        data = {
            "gcloud_token_valid": True,
            "project": "proj",
            "region": "us-east5",
            "models": [
                {
                    "id": "anthropic-vertex/claude-sonnet-4-6",
                    "auth_ok": True,
                    "latency_ms": 700.0,
                    "error": None,
                }
            ],
            "all_ok": True,
        }
        text = _mod.format_check_result(data)
        assert "✅" in text
        assert "claude-sonnet-4-6" in text
        assert "OK" in text

    def test_format_expired_token(self) -> None:
        """gcloud_token_valid=False → текст содержит '❌' для токена."""
        data = {
            "gcloud_token_valid": False,
            "project": "proj",
            "region": "us-east5",
            "models": [
                {
                    "id": "anthropic-vertex/claude-sonnet-4-6",
                    "auth_ok": False,
                    "latency_ms": 0.0,
                    "error": "ADC невалиден",
                }
            ],
            "all_ok": False,
        }
        text = _mod.format_check_result(data)
        assert "❌" in text
        assert "expired" in text.lower() or "missing" in text.lower()

    def test_format_no_models(self) -> None:
        """Пустой список models → упоминание что модели не найдены."""
        data = {
            "gcloud_token_valid": True,
            "project": "proj",
            "region": "us-east5",
            "models": [],
            "all_ok": False,
        }
        text = _mod.format_check_result(data)
        assert "не найдены" in text or "не найден" in text
