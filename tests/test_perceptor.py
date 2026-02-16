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
    perceptor = Perceptor({})
    # Проверяем структуру генерации путей (не запуская say)
    with patch('asyncio.create_subprocess_exec') as mock_exec:
        # Мокаем успех обоих процессов
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))
        mock_exec.return_value = mock_proc
        
        # Мокаем наличие файлов
        with patch('os.path.exists', return_value=True),              patch('os.remove'):
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
