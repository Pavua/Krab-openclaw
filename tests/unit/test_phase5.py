
import pytest
import json
import os
from unittest.mock import AsyncMock, MagicMock, patch
from src import employee_templates
from src.openclaw_client import OpenClawClient

class TestPhase5:
    
    @pytest.fixture
    def mock_roles_file(self, tmp_path):
        # Mock ROLES_FILE
        file = tmp_path / "roles.json"
        with pytest.MonkeyPatch.context() as m:
            m.setattr(employee_templates, "ROLES_FILE", str(file))
            # Reset ROLES
            employee_templates.ROLES = employee_templates.DEFAULT_ROLES.copy()
            yield file

    def test_save_role(self, mock_roles_file):
        """Test saving a new agent role"""
        assert employee_templates.save_role("test_agent", "You are a test agent")
        
        # Verify in memory
        assert "test_agent" in employee_templates.ROLES
        assert employee_templates.ROLES["test_agent"] == "You are a test agent"
        
        # Verify file
        with open(mock_roles_file, 'r') as f:
            data = json.load(f)
            assert data["test_agent"] == "You are a test agent"

    @pytest.mark.asyncio
    async def test_vision_payload(self):
        """Тест форматирования vision-payload с изображениями."""
        client = OpenClawClient()
        client._http_client = MagicMock()
        client._http_client.stream = MagicMock()
        
        # Mock async iterable для aiter_lines — обязательно async generator
        async def _empty_aiter():
            return
            yield  # noqa: RET504  — делает функцию async generator

        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.aiter_lines = _empty_aiter
        
        cm = AsyncMock()
        cm.__aenter__.return_value = mock_response
        client._http_client.stream.return_value = cm

        # Mock model_manager: покрываем все async-методы, вызываемые send_message_stream
        mock_mm = MagicMock()
        mock_mm.get_best_model = AsyncMock(return_value="google/gemini-2.5-flash")
        mock_mm.get_best_cloud_model = AsyncMock(return_value="google/gemini-2.5-flash")
        mock_mm.ensure_model_loaded = AsyncMock(return_value=True)
        mock_mm.resolve_preferred_local_model = AsyncMock(return_value=None)
        mock_mm.is_local_model = MagicMock(return_value=False)
        mock_mm.mark_request_started = MagicMock()
        mock_mm.mark_request_finished = MagicMock()
        mock_mm.get_current_model = MagicMock(return_value=None)
        mock_mm._models_cache = {}
        mock_mm._local_candidates = AsyncMock(return_value=[])
        
        with patch("src.model_manager.model_manager", mock_mm):
            gen = client.send_message_stream("Look at this", "123", images=["base64string"])
            async for _ in gen:
                pass
        
        # Verify payload construction
        call_args = client._http_client.stream.call_args
        assert call_args is not None
        
        payload = call_args[1]['json']
        last_msg = payload['messages'][-1]
        
        assert last_msg['role'] == "user"
        assert isinstance(last_msg['content'], list)
        assert last_msg['content'][0] == {"type": "text", "text": "Look at this"}
        assert last_msg['content'][1] == {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,base64string"}}

