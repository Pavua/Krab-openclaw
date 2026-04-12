import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

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
        with open(mock_roles_file, "r") as f:
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

        # Mock buffered POST — send_message_stream теперь использует _openclaw_completion_once
        mock_post_response = MagicMock()
        mock_post_response.status_code = 200
        mock_post_response.json.return_value = {
            "choices": [{"message": {"role": "assistant", "content": "ok"}}]
        }
        mock_post_response.text = '{"choices":[{"message":{"role":"assistant","content":"ok"}}]}'
        client._http_client.post = AsyncMock(return_value=mock_post_response)

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

        # Verify payload construction — теперь используется .post (buffered), не .stream
        call_args = client._http_client.post.call_args
        assert call_args is not None

        payload = call_args[1]["json"]
        # Ищем user-сообщение с vision-контентом среди всех messages (история может расти)
        vision_msg = next(
            (
                m
                for m in payload["messages"]
                if m.get("role") == "user" and isinstance(m.get("content"), list)
            ),
            None,
        )
        assert vision_msg is not None, (
            f"Не найдено user-сообщение с vision в payload: {payload['messages']}"
        )
        assert {"type": "text", "text": "Look at this"} in vision_msg["content"]
        assert {
            "type": "image_url",
            "image_url": {"url": "data:image/jpeg;base64,base64string"},
        } in vision_msg["content"]
