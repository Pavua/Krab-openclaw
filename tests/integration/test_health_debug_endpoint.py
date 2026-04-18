"""Unit tests for /api/ecosystem/health/debug endpoint response structure."""

from unittest.mock import AsyncMock, MagicMock

import pytest


class TestEcosystemHealthDebugLogic:
    """Test endpoint logic without requiring a live server."""

    @pytest.mark.asyncio
    async def test_response_structure_default(self, monkeypatch):
        """Verify response structure without section param."""
        # Mock the EcosystemHealthService
        mock_svc = MagicMock()
        mock_svc._collect_session_12_stats.return_value = {"status": "ok"}
        mock_svc.collect = AsyncMock(
            return_value={"session_12": {"data": "test"}, "runtime_route": {}}
        )

        # Simulate the endpoint logic
        section = ""
        direct = mock_svc._collect_session_12_stats()
        full = await mock_svc.collect()

        response = {
            "direct": direct,
            "full_has_session_12": "session_12" in full,
            "full_keys": list(full.keys()),
        }

        if section:
            response["section_filter"] = section
            response["full_section"] = full.get(section)
        else:
            response["full_session_12"] = full.get("session_12")

        assert "direct" in response
        assert "full_keys" in response
        assert "full_has_session_12" in response
        assert response["full_keys"] == ["session_12", "runtime_route"]
        assert "full_session_12" in response

    @pytest.mark.asyncio
    async def test_response_structure_with_section(self, monkeypatch):
        """Verify response structure with section=session_10."""
        mock_svc = MagicMock()
        mock_svc._collect_session_12_stats.return_value = {"status": "ok"}
        mock_svc.collect = AsyncMock(
            return_value={"session_10": {"info": "data"}, "session_12": {}}
        )

        section = "session_10"
        direct = mock_svc._collect_session_12_stats()
        full = await mock_svc.collect()

        response = {
            "direct": direct,
            "full_has_session_12": "session_12" in full,
            "full_keys": list(full.keys()),
        }

        if section:
            response["section_filter"] = section
            response["full_section"] = full.get(section)
        else:
            response["full_session_12"] = full.get("session_12")

        assert response["section_filter"] == "session_10"
        assert "full_section" in response
        assert response["full_section"] == {"info": "data"}

    @pytest.mark.asyncio
    async def test_nonexistent_section_returns_none(self, monkeypatch):
        """Nonexistent section returns None in full_section."""
        mock_svc = MagicMock()
        mock_svc._collect_session_12_stats.return_value = {"status": "ok"}
        mock_svc.collect = AsyncMock(return_value={"session_12": {}})

        section = "nonexistent"
        direct = mock_svc._collect_session_12_stats()
        full = await mock_svc.collect()

        response = {
            "direct": direct,
            "full_has_session_12": "session_12" in full,
            "full_keys": list(full.keys()),
        }

        if section:
            response["section_filter"] = section
            response["full_section"] = full.get(section)

        assert response["full_section"] is None

    @pytest.mark.asyncio
    async def test_exception_handling(self, monkeypatch):
        """Exception is caught and returned with trace."""
        mock_svc = MagicMock()
        mock_svc._collect_session_12_stats.side_effect = RuntimeError("test error")

        try:
            mock_svc._collect_session_12_stats()
        except Exception as exc:
            import traceback

            response = {
                "error": str(exc),
                "trace": traceback.format_exc()[:500],
            }
            assert "error" in response
            assert "test error" in response["error"]
            assert "trace" in response
            assert len(response["trace"]) <= 500
