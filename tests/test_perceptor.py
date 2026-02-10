# -*- coding: utf-8 -*-
import pytest
import os
from unittest.mock import AsyncMock, MagicMock, patch
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

