
import pytest
import json
import os
from unittest.mock import AsyncMock, MagicMock
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
        """Test formatting of vision payload"""
        client = OpenClawClient()
        client._http_client = MagicMock()
        client._http_client.stream = MagicMock()
        
        # Mock stream context manager
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.aiter_lines.return_value = []
        
        cm = AsyncMock()
        cm.__aenter__.return_value = mock_response
        client._http_client.stream.return_value = cm
        
        # Call with images
        gen = client.send_message_stream("Look at this", "123", images=["base64string"])
        async for _ in gen: pass # Consume generator
        
        # Verify payload construction
        call_args = client._http_client.stream.call_args
        assert call_args is not None
        
        payload = call_args[1]['json']
        last_msg = payload['messages'][-1]
        
        assert last_msg['role'] == "user"
        assert isinstance(last_msg['content'], list)
        assert last_msg['content'][0] == {"type": "text", "text": "Look at this"}
        assert last_msg['content'][1] == {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,base64string"}}
