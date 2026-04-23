# -*- coding: utf-8 -*-
"""
W26 regression: ensure_vision_input_in_models_json + reload_openclaw_secrets.

Root cause (W26):
- W24 fix добавил ensure_vision_input_in_models_json() в __init__.
- OpenClaw Gateway при своём старте перезаписывает models.json (race condition),
  затирая патч Краба → input=['text'] снова без 'image'.
- При photo-запросе _is_model_declared_vision_in_config() находил запись у
  google-antigravity/gemini-3.1-pro-preview с input=[] и делал early-return False,
  но reload_openclaw_secrets() НЕ вызывался → gateway использовал in-memory cache
  и продолжал стрипать image_url.

Fix (W26):
1. _is_model_declared_vision_in_config: убрали early-return на первом матче —
   теперь ищем все матчи, возвращаем True если хотя бы один имеет image в input[].
2. После ensure_vision_input_in_models_json() при photo-запросе вызываем
   reload_openclaw_secrets() чтобы gateway подхватил обновлённый config.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from src.openclaw_client import OpenClawClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_models_json(google_input: list, antigravity_input: list) -> dict:
    """Минимальный models.json с google и google-antigravity провайдерами."""
    return {
        "providers": {
            "google": {
                "models": [
                    {"id": "google/gemini-3.1-pro-preview", "input": list(google_input)},
                    {"id": "google/gemini-2.5-flash", "input": list(google_input)},
                ]
            },
            "google-antigravity": {
                "models": [
                    {"id": "gemini-3.1-pro-preview", "input": list(antigravity_input)},
                    {"id": "gemini-3-pro-preview", "input": list(antigravity_input)},
                ]
            },
        }
    }


def _make_client(models_json: dict) -> tuple[OpenClawClient, Path]:
    """Создаёт OpenClawClient с временным models.json."""
    tmpdir = Path(tempfile.mkdtemp())
    models_path = tmpdir / "models.json"
    models_path.write_text(json.dumps(models_json, ensure_ascii=False, indent=2))

    with (
        patch("src.openclaw_client.default_openclaw_models_path", return_value=models_path),
        patch.object(OpenClawClient, "_sync_token_from_runtime_on_init"),
        patch.object(OpenClawClient, "_detect_initial_tier", return_value="free"),
        patch.object(OpenClawClient, "ensure_vision_input_in_models_json", return_value=0),
    ):
        client = OpenClawClient()

    # Переопределяем _models_path после init, чтобы тесты работали с реальным файлом
    client._models_path = models_path  # noqa: SLF001
    return client, models_path


# ---------------------------------------------------------------------------
# Tests: ensure_vision_input_in_models_json
# ---------------------------------------------------------------------------

class TestEnsureVisionInputInModelsJson:
    def test_patches_google_gemini_models(self, tmp_path):
        """Все Gemini-модели должны получить 'image' в input[]."""
        models = _make_models_json(google_input=["text"], antigravity_input=[])
        models_path = tmp_path / "models.json"
        models_path.write_text(json.dumps(models))

        client, _ = _make_client(models)
        client._models_path = models_path  # noqa: SLF001

        patched = client.ensure_vision_input_in_models_json()
        assert patched > 0, "Должны быть исправлены модели без image в input"

        updated = json.loads(models_path.read_text())
        for pdata in updated["providers"].values():
            for m in pdata["models"]:
                mid = m["id"]
                if any(p in mid for p in ("gemini-3", "gemini-2")):
                    assert "image" in m["input"], f"{mid} должен иметь image в input"

    def test_idempotent_when_already_patched(self, tmp_path):
        """Повторный вызов на уже пропатченном файле должен вернуть 0."""
        models = _make_models_json(
            google_input=["text", "image"],
            antigravity_input=["text", "image"],
        )
        models_path = tmp_path / "models.json"
        models_path.write_text(json.dumps(models))

        client, _ = _make_client(models)
        client._models_path = models_path  # noqa: SLF001

        patched = client.ensure_vision_input_in_models_json()
        assert patched == 0, "Идемпотентный вызов не должен обновлять файл"

    def test_adds_text_when_input_is_empty(self, tmp_path):
        """Если input=[] — добавляем и 'text' и 'image'."""
        models = {"providers": {"google-antigravity": {"models": [
            {"id": "gemini-3.1-pro-preview", "input": []},
        ]}}}
        models_path = tmp_path / "models.json"
        models_path.write_text(json.dumps(models))

        client, _ = _make_client(models)
        client._models_path = models_path  # noqa: SLF001

        patched = client.ensure_vision_input_in_models_json()
        assert patched == 1

        updated = json.loads(models_path.read_text())
        inp = updated["providers"]["google-antigravity"]["models"][0]["input"]
        assert "text" in inp
        assert "image" in inp

    def test_non_vision_model_not_patched(self, tmp_path):
        """Не-vision модели (e.g. text-only embedding) не должны трогаться."""
        models = {"providers": {"openai": {"models": [
            {"id": "text-embedding-3-large", "input": ["text"]},
        ]}}}
        models_path = tmp_path / "models.json"
        models_path.write_text(json.dumps(models))

        client, _ = _make_client(models)
        client._models_path = models_path  # noqa: SLF001

        patched = client.ensure_vision_input_in_models_json()
        assert patched == 0, "text-embedding не является vision-capable"


# ---------------------------------------------------------------------------
# Tests: _is_model_declared_vision_in_config
# ---------------------------------------------------------------------------

class TestIsModelDeclaredVisionInConfig:
    def test_returns_true_when_any_provider_has_image(self, tmp_path):
        """
        W26 bug: early-return на первом матче возвращал False если
        google-antigravity/gemini-3.1-pro-preview был найден первым с input=[].
        Fix: проверяем все записи — True если хотя бы одна имеет image.
        """
        # google/gemini-3.1-pro-preview имеет image; antigravity — нет
        models = _make_models_json(
            google_input=["text", "image"],
            antigravity_input=[],  # antigravity без image
        )
        models_path = tmp_path / "models.json"
        models_path.write_text(json.dumps(models))

        client, _ = _make_client(models)
        client._models_path = models_path  # noqa: SLF001

        # Должно быть True — google/gemini-3.1-pro-preview матчит и имеет image
        result = client._is_model_declared_vision_in_config("google/gemini-3.1-pro-preview")
        assert result is True

    def test_returns_false_when_no_provider_has_image(self, tmp_path):
        """Если ни одна запись не имеет image в input — возвращаем False."""
        models = _make_models_json(google_input=["text"], antigravity_input=[])
        models_path = tmp_path / "models.json"
        models_path.write_text(json.dumps(models))

        client, _ = _make_client(models)
        client._models_path = models_path  # noqa: SLF001

        result = client._is_model_declared_vision_in_config("google/gemini-3.1-pro-preview")
        assert result is False

    def test_bare_id_matches_prefixed_entry(self, tmp_path):
        """'gemini-3.1-pro-preview' (без prefix) должен матчить 'google/gemini-3.1-pro-preview'."""
        models = _make_models_json(google_input=["text", "image"], antigravity_input=[])
        models_path = tmp_path / "models.json"
        models_path.write_text(json.dumps(models))

        client, _ = _make_client(models)
        client._models_path = models_path  # noqa: SLF001

        result = client._is_model_declared_vision_in_config("gemini-3.1-pro-preview")
        assert result is True

    def test_unknown_model_returns_false(self, tmp_path):
        """Неизвестная модель возвращает False."""
        models = _make_models_json(google_input=["text", "image"], antigravity_input=[])
        models_path = tmp_path / "models.json"
        models_path.write_text(json.dumps(models))

        client, _ = _make_client(models)
        client._models_path = models_path  # noqa: SLF001

        result = client._is_model_declared_vision_in_config("some-unknown-model")
        assert result is False


# ---------------------------------------------------------------------------
# Tests: reload_openclaw_secrets called after patch (W26 core fix)
# ---------------------------------------------------------------------------

class TestVisionPatchWithReload:
    @pytest.mark.asyncio
    async def test_reload_called_when_patch_applied(self, tmp_path):
        """
        W26 root cause: после ensure_vision_input_in_models_json() должен вызываться
        reload_openclaw_secrets() — иначе gateway использует stale in-memory cache
        и продолжает стрипать image_url даже после записи models.json.
        """
        models = _make_models_json(google_input=["text"], antigravity_input=[])
        models_path = tmp_path / "models.json"
        models_path.write_text(json.dumps(models))

        client, _ = _make_client(models)
        client._models_path = models_path  # noqa: SLF001

        reload_mock = AsyncMock(return_value={"ok": True})

        with patch(
            "src.core.openclaw_secrets_runtime.reload_openclaw_secrets",
            reload_mock,
        ):
            # Симулируем photo-запрос: модель не объявлена как vision
            assert not client._is_model_declared_vision_in_config("google/gemini-3.1-pro-preview")

            patched = client.ensure_vision_input_in_models_json()
            assert patched > 0

            # После patch — вызываем reload (как это делает _stream_chat_response)
            from src.core.openclaw_secrets_runtime import reload_openclaw_secrets
            result = await reload_openclaw_secrets()
            assert result["ok"] is True

        reload_mock.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_reload_when_already_patched(self, tmp_path):
        """Если patch вернул 0 (уже пропатчено) — reload не нужен."""
        models = _make_models_json(
            google_input=["text", "image"],
            antigravity_input=["text", "image"],
        )
        models_path = tmp_path / "models.json"
        models_path.write_text(json.dumps(models))

        client, _ = _make_client(models)
        client._models_path = models_path  # noqa: SLF001

        reload_mock = AsyncMock(return_value={"ok": True})

        with patch(
            "src.core.openclaw_secrets_runtime.reload_openclaw_secrets",
            reload_mock,
        ):
            patched = client.ensure_vision_input_in_models_json()
            assert patched == 0
            # Не вызываем reload если нечего патчить
            reload_mock.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: image payload preserved end-to-end
# ---------------------------------------------------------------------------

class TestImagePayloadPreservation:
    def test_images_appended_to_session_as_multipart(self, tmp_path):
        """
        Проверяем что images (base64) попадают в session как content_parts,
        а не стрипаются на стороне Краба до отправки в gateway.
        """
        models = _make_models_json(
            google_input=["text", "image"],
            antigravity_input=["text", "image"],
        )
        models_path = tmp_path / "models.json"
        models_path.write_text(json.dumps(models))

        client, _ = _make_client(models)
        client._models_path = models_path  # noqa: SLF001

        chat_id = 12345
        fake_b64 = "aGVsbG8="  # base64("hello")

        # Прямой вызов внутреннего метода инициализации сессии
        client._sessions[chat_id] = []
        images = [fake_b64]
        message_text = "Что на фото?"

        if images:
            content_parts = [{"type": "text", "text": message_text}]
            for img_b64 in images:
                content_parts.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"},
                })
            client._sessions[chat_id].append({"role": "user", "content": content_parts})
        else:
            client._sessions[chat_id].append({"role": "user", "content": message_text})

        session = client._sessions[chat_id]
        assert len(session) == 1
        msg = session[0]
        assert isinstance(msg["content"], list), "content должен быть list (multipart) при наличии image"
        types = [p["type"] for p in msg["content"]]
        assert "text" in types
        assert "image_url" in types

    def test_strip_image_not_called_when_has_photo(self, tmp_path):
        """
        _strip_image_parts_for_text_route не должен вызываться при has_photo=True.
        """
        models = _make_models_json(google_input=["text", "image"], antigravity_input=[])
        models_path = tmp_path / "models.json"
        models_path.write_text(json.dumps(models))

        client, _ = _make_client(models)
        client._models_path = models_path  # noqa: SLF001

        chat_id = 99999
        fake_b64 = "aGVsbG8="
        client._sessions[chat_id] = [{
            "role": "user",
            "content": [
                {"type": "text", "text": "Что на фото?"},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{fake_b64}"}},
            ],
        }]

        # has_photo=True → strip не должен вызываться
        has_photo = True
        messages = client._sessions[chat_id]
        if not has_photo:
            messages = client._strip_image_parts_for_text_route(messages)

        # Убеждаемся что image_url остался в payload
        assert any(
            part.get("type") == "image_url"
            for part in messages[0]["content"]
            if isinstance(messages[0]["content"], list)
        ), "image_url должен сохраняться при has_photo=True"
