import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from src.core.model_manager import ModelRouter

@pytest.mark.asyncio
async def test_lms_load_success():
    """Test successful model loading via LMS CLI."""
    mock_config = MagicMock()
    router = ModelRouter(mock_config)
    
    with patch("asyncio.create_subprocess_shell") as mock_exec, \
         patch.object(router, 'check_local_health', new_callable=AsyncMock) as mock_check:
        # Mock successful execution
        process_mock = AsyncMock()
        process_mock.communicate.return_value = (b"Load success", b"")
        process_mock.returncode = 0
        mock_exec.return_value = process_mock
        
        result = await router.load_local_model("qwen2.5-7b")
        
        assert result is True
        assert router.active_local_model == "qwen2.5-7b"
        assert router.is_local_available is True
        
        # Expect absolute path interaction
        args, _ = mock_exec.call_args
        assert "lms load qwen2.5-7b --gpu auto" in args[0]

@pytest.mark.asyncio
async def test_lms_load_failure():
    """Test failure scenarios in model loading."""
    mock_config = MagicMock()
    router = ModelRouter(mock_config)
    
    with patch("asyncio.create_subprocess_shell") as mock_exec:
        # Mock failed execution
        process_mock = AsyncMock()
        process_mock.communicate.return_value = (b"", b"Model not found")
        process_mock.returncode = 1
        mock_exec.return_value = process_mock
        
        result = await router.load_local_model("invalid-model")
        
        assert result is False
        # Expect absolute path interaction
        args, _ = mock_exec.call_args
        assert "lms load invalid-model --gpu auto" in args[0]
