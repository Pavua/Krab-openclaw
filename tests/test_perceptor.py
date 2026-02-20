# -*- coding: utf-8 -*-
import pytest
import os
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path
from PIL import Image
from src.modules.perceptor import Perceptor

@pytest.mark.asyncio
async def test_perceptor_stt_mock():
    # Мокаем внешнюю зависимость mlx_whisper
    with patch('mlx_whisper.transcribe', return_value={"text": "Привет мир"}):
        perceptor = Perceptor({"WHISPER_MODEL": "test"})
        # Создаем пустой файл для теста
        test_file = "test_audio.ogg"
        with open(test_file, "w") as f: f.write("dummy")
        
        res = await perceptor.transcribe(test_file, MagicMock())
        assert res == "Привет мир"
        os.remove(test_file)

@pytest.mark.asyncio
async def test_perceptor_tts_logic():
    with patch.object(Perceptor, "_warmup_audio"):
        perceptor = Perceptor({})
    # Проверяем orchestration speak без реального edge/openai вызова.
    with patch.object(perceptor, "_speak_edge", new=AsyncMock(return_value="artifacts/downloads/test.ogg")):
        res = await perceptor.speak("Hello", voice="Milena")
        assert res is not None
        assert ".ogg" in res


def test_local_vision_model_resolution_priority():
    with patch.dict(os.environ, {"LOCAL_VISION_MODEL": "env-vision-model"}):
        with patch.object(Perceptor, "_warmup_audio"):
            perceptor = Perceptor({})
    router = MagicMock()
    router.active_local_model = "active-model"
    router.local_preferred_model = "preferred-model"
    assert perceptor._resolve_local_vision_model(router) == "env-vision-model"


def test_extract_lm_studio_vision_text_from_list_content():
    with patch.object(Perceptor, "_warmup_audio"):
        perceptor = Perceptor({})
    payload = {
        "choices": [
            {
                "message": {
                    "content": [
                        {"type": "text", "text": "Описание кадра."},
                        {"type": "text", "text": "Дополнительная деталь."},
                    ]
                }
            }
        ]
    }
    text = perceptor._extract_lm_studio_vision_text(payload)
    assert "Описание кадра." in text
    assert "Дополнительная деталь." in text


@pytest.mark.asyncio
async def test_analyze_image_prefers_local_vision_success(tmp_path: Path):
    with patch.dict(os.environ, {"LOCAL_VISION_ENABLED": "1"}):
        with patch.object(Perceptor, "_warmup_audio"):
            perceptor = Perceptor({})

    image_path = tmp_path / "sample.jpg"
    Image.new("RGB", (8, 8), color=(120, 10, 10)).save(image_path)

    with patch.object(
        perceptor,
        "_analyze_image_local_lm_studio",
        new=AsyncMock(return_value={"ok": True, "text": "Локальный vision ответ", "model": "vision-local"}),
    ) as local_mock:
        result = await perceptor.analyze_image(str(image_path), router=MagicMock(), prompt="Опиши картинку")
        assert result == "Локальный vision ответ"
        local_mock.assert_awaited_once()


def test_infer_vision_support_from_capabilities_tokens():
    with patch.object(Perceptor, "_warmup_audio"):
        perceptor = Perceptor({})
    support, reason = perceptor._infer_vision_support_from_entry(
        {"capabilities": ["vision", "reasoning"], "type": "llm"},
        model_name="custom-model",
    )
    assert support is True
    assert reason in {"capability_keys", "token_hint"}


@pytest.mark.asyncio
async def test_local_vision_precheck_blocks_text_only_model(tmp_path: Path):
    with patch.dict(os.environ, {"LOCAL_VISION_ENABLED": "1", "LOCAL_VISION_MODEL": "text-only-model"}):
        with patch.object(Perceptor, "_warmup_audio"):
            perceptor = Perceptor({})

    image_path = tmp_path / "sample.jpg"
    Image.new("RGB", (8, 8), color=(10, 120, 10)).save(image_path)

    with patch.object(
        perceptor,
        "_check_local_vision_support",
        new=AsyncMock(return_value={"supported": False, "reason": "text_only_tokens", "model": "text-only-model"}),
    ):
        result = await perceptor._analyze_image_local_lm_studio(
            file_path=str(image_path),
            router=MagicMock(lm_studio_url="http://127.0.0.1:1234/v1"),
            prompt="Опиши изображение",
        )
    assert result.get("ok") is False
    assert str(result.get("error", "")).startswith("local_model_not_vision_capability")


def test_postprocess_transcript_adds_punctuation_and_caps():
    with patch.object(Perceptor, "_warmup_audio"):
        perceptor = Perceptor({})
    raw = "привет как дела сегодня у нас отличная погода"
    fixed = perceptor._postprocess_transcript(raw)
    assert fixed.startswith("Привет")
    assert fixed.endswith(".")


def test_postprocess_transcript_applies_custom_replace_map():
    with patch.object(Perceptor, "_warmup_audio"):
        perceptor = Perceptor({"STT_REPLACE_JSON": '{"джимини":"Gemini"}'})
    fixed = perceptor._postprocess_transcript("проверка джимини сегодня")
    assert "Gemini" in fixed


@pytest.mark.asyncio
async def test_perceptor_transcribe_fallback_on_unsupported_kwargs(tmp_path: Path):
    with patch.object(Perceptor, "_warmup_audio"):
        perceptor = Perceptor({})

    test_file = tmp_path / "test_audio.ogg"
    test_file.write_text("dummy", encoding="utf-8")

    with patch("mlx_whisper.transcribe") as mocked_transcribe:
        mocked_transcribe.side_effect = [
            TypeError("unexpected keyword argument 'beam_size'"),
            {"text": "привет мир как дела"},
        ]
        result = await perceptor.transcribe(str(test_file), MagicMock())

    assert mocked_transcribe.call_count == 2
    assert result.startswith("Привет мир")
