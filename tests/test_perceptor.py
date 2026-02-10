
import pytest
from unittest.mock import MagicMock, patch
from src.modules.perceptor import Perceptor

@pytest.fixture
def mock_mlx_whisper():
    mock_module = MagicMock()
    mock_module.transcribe.return_value = {"text": "Test transcription."}
    
    with patch.dict("sys.modules", {"mlx_whisper": mock_module}):
        yield mock_module

def test_perceptor_initialization(mock_mlx_whisper):
    """Проверка инициализации и warmup."""
    config = {"WHISPER_MODEL": "test-model"}
    # Import inside verify to ensure patch is active
    from src.modules.perceptor import Perceptor
    
    perceptor = Perceptor(config)
    
    assert perceptor.whisper_model == "mlx-community/whisper-large-v3-turbo" 
    # Verify warmup called transcribe
    mock_mlx_whisper.transcribe.assert_called()

@pytest.mark.asyncio
async def test_transcribe(mock_mlx_whisper):
    """Проверка метода transcribe."""
    from src.modules.perceptor import Perceptor
    perceptor = Perceptor({"WHISPER_MODEL": "test"})
    
    # Mock router
    router_mock = MagicMock()
    
    result = await perceptor.transcribe("/tmp/test.ogg", router_mock)
    
    assert result == "Test transcription."
